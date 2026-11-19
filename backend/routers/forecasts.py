from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.core.database import execute_sql
from backend.core.security import get_current_user

router = APIRouter(prefix="/api/forecasts", tags=["Demand Forecasts"])


@router.get("", summary="Lấy danh sách các phiên dự báo (forecast_runs)")
def list_forecast_runs(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    rows = execute_sql(
        """SELECT id, model_name, model_version,
                  trained_until, horizon_days,
                  metrics, created_at
           FROM forecast_runs
           ORDER BY created_at DESC"""
    )
    return rows


@router.get("/latest/summary", summary="Lấy tóm tắt phiên dự báo mới nhất")
def get_latest_forecast_summary(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    runs = execute_sql(
        """SELECT id, model_name, model_version, trained_until, metrics
           FROM forecast_runs
           WHERE model_version LIKE '%%full%%'
           ORDER BY id DESC
           LIMIT 1"""
    )
    if not runs:
        runs = execute_sql(
            """SELECT id, model_name, model_version, trained_until, metrics
               FROM forecast_runs
               ORDER BY id DESC
               LIMIT 1"""
        )
    if not runs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chưa có dữ liệu dự báo trong hệ thống",
        )

    run = runs[0]
    metrics = run.get("metrics") or {}

    stats = execute_sql(
        """SELECT MIN(forecast_date) AS min_date,
                  MAX(forecast_date) AS max_date,
                  COUNT(DISTINCT product_id) AS skus_covered,
                  COUNT(*) AS total_rows
           FROM demand_forecasts
           WHERE run_id = %(run_id)s""",
        {"run_id": run["id"]},
    )

    stat = stats[0] if stats else {}
    return {
        "run_id": run["id"],
        "model_name": run["model_name"],
        "trained_until": run["trained_until"],
        "wape_overall": metrics.get("wape_overall"),
        "rmsse_overall": metrics.get("rmsse_overall"),
        "forecast_period": {
            "from": stat.get("min_date"),
            "to": stat.get("max_date"),
        },
        "skus_covered": stat.get("skus_covered", 0),
        "total_rows": stat.get("total_rows", 0),
    }


@router.get("/{run_id}/skus", summary="Lấy chi tiết dự báo từng SKU theo run_id")
def get_forecast_skus(
    run_id: int,
    product_id: Optional[int] = Query(None, description="Lọc theo product_id"),
    location_id: Optional[int] = Query(None, description="Lọc theo location_id"),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    rows = execute_sql(
        """SELECT p.sku_code, l.code AS store_code,
                  df.forecast_date, df.p10, df.p50, df.p90
           FROM demand_forecasts df
           JOIN products p ON df.product_id = p.id
           JOIN locations l ON df.location_id = l.id
           WHERE df.run_id = %(run_id)s
             AND (%(pid)s::bigint IS NULL OR df.product_id = %(pid)s)
             AND (%(lid)s::bigint IS NULL OR df.location_id = %(lid)s)
           ORDER BY p.sku_code, l.code, df.forecast_date ASC""",
        {"run_id": run_id, "pid": product_id, "lid": location_id},
    )

    grouped: Dict[tuple, Dict[str, Any]] = {}
    for r in rows:
        key = (r["sku_code"], r["store_code"])
        if key not in grouped:
            grouped[key] = {
                "sku_code": r["sku_code"],
                "store_code": r["store_code"],
                "forecasts": [],
                "total_p50_28d": 0.0,
            }
        p50_val = float(r["p50"]) if r["p50"] is not None else 0.0
        grouped[key]["forecasts"].append({
            "date": r["forecast_date"],
            "p10": float(r["p10"]) if r["p10"] is not None else None,
            "p50": p50_val,
            "p90": float(r["p90"]) if r["p90"] is not None else None,
        })
        grouped[key]["total_p50_28d"] += p50_val

    for item in grouped.values():
        item["total_p50_28d"] = round(item["total_p50_28d"], 2)

    return list(grouped.values())

