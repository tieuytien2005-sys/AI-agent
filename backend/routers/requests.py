import datetime as dt
import json
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from backend.core.database import execute_sql
from backend.core.security import get_current_user, require_role

router = APIRouter(prefix="/api/requests", tags=["Purchase Requests"])


class ApproveRequest(BaseModel):
    reason: Optional[str] = Field(None, description="Lý do phê duyệt")


class ReasonRequiredRequest(BaseModel):
    reason: str = Field(..., min_length=1, description="Lý do bắt buộc")


def _get_request_or_404(request_id: int) -> Dict[str, Any]:
    rows = execute_sql(
        "SELECT * FROM purchase_requests WHERE id = %(id)s",
        {"id": request_id},
    )
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy phiếu đề xuất có id={request_id}",
        )
    return rows[0]


def _build_snapshot(req: Dict[str, Any]) -> str:
    lines = execute_sql(
        """SELECT prl.product_id, p.sku_code, prl.supplier_id, prl.qty, prl.unit_price, prl.option_type
           FROM purchase_request_lines prl
           JOIN products p ON prl.product_id = p.id
           WHERE prl.request_id = %(req_id)s""",
        {"req_id": req["id"]},
    )
    snap = {
        "request_id": req["id"],
        "code": req["code"],
        "status_before": req["status"],
        "total_value": float(req["total_value"]) if req.get("total_value") is not None else 0.0,
        "lines": [
            {
                "product_id": l["product_id"],
                "sku_code": l["sku_code"],
                "supplier_id": l["supplier_id"],
                "qty": l["qty"],
                "unit_price": float(l["unit_price"]),
                "option_type": l["option_type"],
            }
            for l in lines
        ],
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    return json.dumps(snap)


@router.get("", summary="Lấy danh sách phiếu đề xuất mua hàng")
def list_requests(
    status_filter: Optional[str] = Query(None, alias="status", description="Lọc theo trạng thái"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    count_rows = execute_sql(
        "SELECT COUNT(*) AS cnt FROM purchase_requests WHERE (%(st)s::text IS NULL OR status = %(st)s)",
        {"st": status_filter},
    )
    total = count_rows[0]["cnt"] if count_rows else 0

    items = execute_sql(
        """SELECT pr.id, pr.code, pr.status, pr.total_value, pr.currency,
                  pr.summary, pr.created_by_run_id, pr.created_at,
                  pr.submitted_at, pr.decided_at,
                  COUNT(prl.id)::int AS lines_count,
                  COALESCE(AVG(prl.confidence), 0)::float AS confidence_avg
           FROM purchase_requests pr
           LEFT JOIN purchase_request_lines prl ON pr.id = prl.request_id
           WHERE (%(st)s::text IS NULL OR pr.status = %(st)s)
           GROUP BY pr.id
           ORDER BY pr.id DESC
           LIMIT %(limit)s OFFSET %(offset)s""",
        {"st": status_filter, "limit": limit, "offset": offset},
    )
    return {"total": total, "items": items}


@router.get("/{id}", summary="Lấy chi tiết một phiếu đề xuất mua hàng")
def get_request_detail(
    id: int,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    req = _get_request_or_404(id)

    lines = execute_sql(
        """SELECT prl.id, prl.product_id, p.sku_code, l.code AS store_code,
                  prl.supplier_id, s.name AS supplier_name, prl.option_type,
                  prl.qty, prl.unit_price, prl.line_value,
                  prl.needed_by_date, prl.is_recommended, prl.is_selected,
                  prl.confidence, prl.reason,
                  se.on_time_rate AS supplier_on_time_rate,
                  se.defect_rate AS supplier_defect_rate,
                  s.lead_time_days AS supplier_lead_time
           FROM purchase_request_lines prl
           JOIN products p ON prl.product_id = p.id
           JOIN locations l ON prl.location_id = l.id
           JOIN suppliers s ON prl.supplier_id = s.id
           LEFT JOIN LATERAL (
               SELECT on_time_rate, defect_rate
               FROM supplier_evaluations
               WHERE supplier_id = s.id
               ORDER BY evaluated_at DESC LIMIT 1
           ) se ON TRUE
           WHERE prl.request_id = %(req_id)s
           ORDER BY prl.id ASC""",
        {"req_id": id},
    )

    decisions = execute_sql(
        """SELECT rd.id, rd.actor_id, u.email AS actor_email, rd.action,
                  rd.reason, rd.created_at
           FROM request_decisions rd
           JOIN users u ON rd.actor_id = u.id
           WHERE rd.request_id = %(req_id)s
           ORDER BY rd.created_at ASC""",
        {"req_id": id},
    )

    res = dict(req)
    res["lines"] = lines
    res["decisions"] = decisions
    return res


@router.post("/{id}/submit", summary="Gửi duyệt phiếu đề xuất (DRAFT -> PENDING_APPROVAL)")
def submit_request(
    id: int,
    current_user: Dict[str, Any] = Depends(require_role("PURCHASER", "MANAGER", "ADMIN")),
):
    req = _get_request_or_404(id)
    if req["status"] != "DRAFT":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Chỉ phiếu ở trạng thái DRAFT mới có thể gửi duyệt (trạng thái hiện tại: {req['status']})",
        )

    snap_json = _build_snapshot(req)
    now = dt.datetime.now(dt.timezone.utc)

    execute_sql(
        """UPDATE purchase_requests
           SET status = 'PENDING_APPROVAL', submitted_at = %(now)s
           WHERE id = %(id)s""",
        {"now": now, "id": id},
    )

    execute_sql(
        """INSERT INTO request_decisions (request_id, actor_id, action, reason, snapshot, created_at)
           VALUES (%(req_id)s, %(actor_id)s, 'SUBMIT', NULL, %(snap)s::jsonb, %(now)s)""",
        {
            "req_id": id,
            "actor_id": current_user["id"],
            "snap": snap_json,
            "now": now,
        },
    )

    return {
        "id": req["id"],
        "code": req["code"],
        "status": "PENDING_APPROVAL",
        "submitted_at": now,
    }


@router.post("/{id}/approve", summary="Phê duyệt phiếu đề xuất (PENDING_APPROVAL -> APPROVED)")
def approve_request(
    id: int,
    body: ApproveRequest = ApproveRequest(),
    current_user: Dict[str, Any] = Depends(require_role("MANAGER", "ADMIN")),
):
    req = _get_request_or_404(id)
    if req["status"] != "PENDING_APPROVAL":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Chỉ phiếu ở trạng thái PENDING_APPROVAL mới có thể phê duyệt (trạng thái hiện tại: {req['status']})",
        )

    snap_json = _build_snapshot(req)
    now = dt.datetime.now(dt.timezone.utc)

    execute_sql(
        """UPDATE purchase_requests
           SET status = 'APPROVED', decided_at = %(now)s, decided_by = %(actor_id)s
           WHERE id = %(id)s""",
        {"now": now, "actor_id": current_user["id"], "id": id},
    )

    execute_sql(
        """INSERT INTO request_decisions (request_id, actor_id, action, reason, snapshot, created_at)
           VALUES (%(req_id)s, %(actor_id)s, 'APPROVE', %(reason)s, %(snap)s::jsonb, %(now)s)""",
        {
            "req_id": id,
            "actor_id": current_user["id"],
            "reason": body.reason,
            "snap": snap_json,
            "now": now,
        },
    )

    return {
        "id": req["id"],
        "code": req["code"],
        "status": "APPROVED",
        "decided_at": now,
    }


@router.post("/{id}/reject", summary="Từ chối phiếu đề xuất (PENDING_APPROVAL -> REJECTED)")
def reject_request(
    id: int,
    body: ReasonRequiredRequest,
    current_user: Dict[str, Any] = Depends(require_role("MANAGER", "ADMIN")),
):
    if not body.reason or not body.reason.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Lý do từ chối là bắt buộc",
        )

    req = _get_request_or_404(id)
    if req["status"] != "PENDING_APPROVAL":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Chỉ phiếu ở trạng thái PENDING_APPROVAL mới có thể từ chối (trạng thái hiện tại: {req['status']})",
        )

    snap_json = _build_snapshot(req)
    now = dt.datetime.now(dt.timezone.utc)

    execute_sql(
        """UPDATE purchase_requests
           SET status = 'REJECTED', decided_at = %(now)s, decided_by = %(actor_id)s
           WHERE id = %(id)s""",
        {"now": now, "actor_id": current_user["id"], "id": id},
    )

    execute_sql(
        """INSERT INTO request_decisions (request_id, actor_id, action, reason, snapshot, created_at)
           VALUES (%(req_id)s, %(actor_id)s, 'REJECT', %(reason)s, %(snap)s::jsonb, %(now)s)""",
        {
            "req_id": id,
            "actor_id": current_user["id"],
            "reason": body.reason.strip(),
            "snap": snap_json,
            "now": now,
        },
    )

    return {
        "id": req["id"],
        "code": req["code"],
        "status": "REJECTED",
        "decided_at": now,
    }


@router.post("/{id}/revise", summary="Yêu cầu sửa đổi phiếu (PENDING_APPROVAL -> DRAFT)")
def revise_request(
    id: int,
    body: ReasonRequiredRequest,
    current_user: Dict[str, Any] = Depends(require_role("MANAGER", "ADMIN")),
):
    if not body.reason or not body.reason.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Lý do yêu cầu sửa đổi là bắt buộc",
        )

    req = _get_request_or_404(id)
    if req["status"] != "PENDING_APPROVAL":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Chỉ phiếu ở trạng thái PENDING_APPROVAL mới có thể yêu cầu sửa đổi (trạng thái hiện tại: {req['status']})",
        )

    snap_json = _build_snapshot(req)
    now = dt.datetime.now(dt.timezone.utc)

    execute_sql(
        "UPDATE purchase_requests SET status = 'DRAFT' WHERE id = %(id)s",
        {"id": id},
    )

    execute_sql(
        """INSERT INTO request_decisions (request_id, actor_id, action, reason, snapshot, created_at)
           VALUES (%(req_id)s, %(actor_id)s, 'REQUEST_CHANGES', %(reason)s, %(snap)s::jsonb, %(now)s)""",
        {
            "req_id": id,
            "actor_id": current_user["id"],
            "reason": body.reason.strip(),
            "snap": snap_json,
            "now": now,
        },
    )

    return {
        "id": req["id"],
        "code": req["code"],
        "status": "DRAFT",
    }


@router.get("/{id}/decisions", summary="Lấy lịch sử quyết định của phiếu")
def get_request_decisions(
    id: int,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    _get_request_or_404(id)
    rows = execute_sql(
        """SELECT rd.id, rd.action, rd.reason,
                  u.email AS actor_email, u.role AS actor_role,
                  rd.created_at, rd.snapshot
           FROM request_decisions rd
           JOIN users u ON rd.actor_id = u.id
           WHERE rd.request_id = %(req_id)s
           ORDER BY rd.created_at ASC""",
        {"req_id": id},
    )
    return rows

