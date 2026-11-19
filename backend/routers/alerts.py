import datetime as dt
import json
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from backend.core.database import execute_sql
from backend.core.security import get_current_user, require_role
from backend.agent.tools.stock_risk import get_stock_risk

router = APIRouter(prefix="/api/alerts", tags=["Stock Alerts"])


class DismissRequest(BaseModel):
    reason: Optional[str] = Field(None, description="Lý do hủy/bỏ qua cảnh báo")


@router.get("", summary="Lấy danh sách cảnh báo tồn kho")
def list_alerts(
    status: Optional[str] = Query(None, description="Lọc theo status: OPEN, IN_REVIEW, RESOLVED, DISMISSED"),
    severity: Optional[str] = Query(None, description="Lọc theo mức độ nghiêm trọng: LOW, MEDIUM, HIGH"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    count_rows = execute_sql(
        """SELECT COUNT(*) AS cnt
           FROM stock_alerts sa
           WHERE (%(st)s::text IS NULL OR sa.status = %(st)s)
             AND (%(sev)s::text IS NULL OR sa.severity = %(sev)s)""",
        {"st": status, "sev": severity},
    )
    total = count_rows[0]["cnt"] if count_rows else 0

    items = execute_sql(
        """SELECT sa.id, sa.product_id, p.sku_code, l.code AS store_code,
                  sa.alert_type, sa.severity, sa.status,
                  sa.projected_date, sa.detected_at, sa.details
           FROM stock_alerts sa
           JOIN products p ON sa.product_id = p.id
           JOIN locations l ON sa.location_id = l.id
           WHERE (%(st)s::text IS NULL OR sa.status = %(st)s)
             AND (%(sev)s::text IS NULL OR sa.severity = %(sev)s)
           ORDER BY sa.id DESC
           LIMIT %(limit)s OFFSET %(offset)s""",
        {"st": status, "sev": severity, "limit": limit, "offset": offset},
    )
    return {"total": total, "items": items}


@router.get("/{id}", summary="Lấy chi tiết cảnh báo kèm dự báo nhu cầu")
def get_alert_detail(
    id: int,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    rows = execute_sql(
        """SELECT sa.id, sa.product_id, sa.location_id, p.sku_code, l.code AS store_code,
                  sa.alert_type, sa.severity, sa.status,
                  sa.projected_date, sa.detected_at, sa.details, sa.run_id
           FROM stock_alerts sa
           JOIN products p ON sa.product_id = p.id
           JOIN locations l ON sa.location_id = l.id
           WHERE sa.id = %(id)s""",
        {"id": id},
    )
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy cảnh báo với id={id}",
        )

    alert = rows[0]
    forecast_rows = execute_sql(
        """SELECT df.forecast_date, df.p10, df.p50, df.p90
           FROM demand_forecasts df
           WHERE df.product_id = %(p)s AND df.location_id = %(l)s
           ORDER BY df.forecast_date ASC
           LIMIT 28""",
        {"p": alert["product_id"], "l": alert["location_id"]},
    )
    alert["forecasts"] = forecast_rows
    return alert


@router.post("/scan", summary="Quét rủi ro tồn kho toàn bộ SKU và cập nhật cảnh báo")
def scan_alerts(
    current_user: Dict[str, Any] = Depends(require_role("PURCHASER", "MANAGER", "ADMIN")),
):
    inv_rows = execute_sql(
        "SELECT product_id, location_id FROM inventory_levels ORDER BY product_id, location_id"
    )

    new_count = 0
    updated_count = 0
    severity_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}

    for row in inv_rows:
        pid = row["product_id"]
        lid = row["location_id"]
        try:
            risk = get_stock_risk(pid, lid)
        except Exception:
            continue

        risk_level = risk.get("risk_level", "NONE")
        if risk_level == "NONE":
            continue

        if risk_level in severity_counts:
            severity_counts[risk_level] += 1

        alert_type = risk.get("risk_type", "SHORTAGE")
        if alert_type not in ("SHORTAGE", "EXCESS"):
            alert_type = "SHORTAGE"

        proj_date = risk.get("projected_stockout_date")
        details = {
            "on_hand": risk.get("on_hand", 0),
            "reorder_point": risk.get("reorder_point", 0),
            "days_of_cover": risk.get("days_of_cover", 0),
            "avg_daily_demand": risk.get("avg_daily_demand_28d", 0),
        }
        details_json = json.dumps(details)

        existing = execute_sql(
            """SELECT id FROM stock_alerts
               WHERE product_id = %(p)s AND location_id = %(l)s AND status = 'OPEN'
               LIMIT 1""",
            {"p": pid, "l": lid},
        )

        if existing:
            execute_sql(
                """UPDATE stock_alerts
                   SET severity = %(sev)s,
                       alert_type = %(atype)s,
                       projected_date = %(pdate)s,
                       details = %(details)s::jsonb
                   WHERE id = %(id)s""",
                {
                    "sev": risk_level,
                    "atype": alert_type,
                    "pdate": proj_date,
                    "details": details_json,
                    "id": existing[0]["id"],
                },
            )
            updated_count += 1
        else:
            execute_sql(
                """INSERT INTO stock_alerts (product_id, location_id, alert_type, severity, projected_date, details, status)
                   VALUES (%(p)s, %(l)s, %(atype)s, %(sev)s, %(pdate)s, %(details)s::jsonb, 'OPEN')""",
                {
                    "p": pid,
                    "l": lid,
                    "atype": alert_type,
                    "sev": risk_level,
                    "pdate": proj_date,
                    "details": details_json,
                },
            )
            new_count += 1

    return {
        "scanned": len(inv_rows),
        "new_alerts": new_count,
        "updated_alerts": updated_count,
        "alerts_by_severity": severity_counts,
    }


@router.put("/{id}/dismiss", summary="Bỏ qua cảnh báo (DISMISSED)")
def dismiss_alert(
    id: int,
    body: DismissRequest = DismissRequest(),
    current_user: Dict[str, Any] = Depends(require_role("MANAGER", "ADMIN")),
):
    rows = execute_sql(
        "SELECT id, status FROM stock_alerts WHERE id = %(id)s",
        {"id": id},
    )
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy cảnh báo với id={id}",
        )

    execute_sql(
        "UPDATE stock_alerts SET status = 'DISMISSED' WHERE id = %(id)s",
        {"id": id},
    )
    return {"message": "Đã bỏ qua cảnh báo thành công", "id": id, "status": "DISMISSED"}

