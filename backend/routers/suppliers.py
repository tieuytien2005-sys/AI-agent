import datetime as dt
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.core.database import execute_sql
from backend.core.security import get_current_user

router = APIRouter(prefix="/api/suppliers", tags=["Suppliers"])


@router.get("", summary="Lấy danh sách nhà cung cấp kèm điểm đánh giá mới nhất")
def list_suppliers(
    category: Optional[str] = Query(None, description="Lọc theo danh mục"),
    is_active: Optional[bool] = Query(None, description="Lọc theo trạng thái hoạt động"),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    rows = execute_sql(
        """SELECT s.id, s.code, s.name, s.category,
                  s.lead_time_days, s.moq, s.payment_terms_days,
                  se.total_score AS latest_score,
                  se.on_time_rate,
                  se.defect_rate
           FROM suppliers s
           LEFT JOIN LATERAL (
               SELECT total_score, on_time_rate, defect_rate
               FROM supplier_evaluations
               WHERE supplier_id = s.id
               ORDER BY evaluated_at DESC
               LIMIT 1
           ) se ON TRUE
           WHERE (%(cat)s::text IS NULL OR s.category = %(cat)s)
             AND (%(act)s::boolean IS NULL OR s.is_active = %(act)s)
           ORDER BY s.id ASC""",
        {"cat": category, "act": is_active},
    )
    return rows


@router.get("/{id}", summary="Lấy chi tiết nhà cung cấp kèm lịch sử đánh giá")
def get_supplier_detail(
    id: int,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    supp_rows = execute_sql(
        "SELECT * FROM suppliers WHERE id = %(id)s",
        {"id": id},
    )
    if not supp_rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy nhà cung cấp với id={id}",
        )

    supplier = supp_rows[0]
    evals = execute_sql(
        """SELECT id, period_start, period_end, on_time_rate,
                  defect_rate, avg_lead_time, price_index,
                  weights, total_score, evaluated_at
           FROM supplier_evaluations
           WHERE supplier_id = %(id)s
           ORDER BY evaluated_at DESC""",
        {"id": id},
    )
    supplier["evaluations"] = evals
    return supplier


@router.get("/{id}/quotes", summary="Lấy danh sách báo giá của nhà cung cấp")
def get_supplier_quotes(
    id: int,
    product_id: Optional[int] = Query(None, description="Lọc theo product_id"),
    as_of_date: Optional[str] = Query(None, description="Ngày hiệu lực (mặc định hôm nay YYYY-MM-DD)"),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    ref_date = as_of_date or dt.date.today().isoformat()

    quotes = execute_sql(
        """SELECT sq.id, p.sku_code, l.code AS store_code,
                  sq.base_unit_price, sq.currency,
                  sq.lead_time_days, sq.moq,
                  sq.valid_from, sq.valid_to
           FROM supplier_quotes sq
           JOIN products p ON sq.product_id = p.id
           LEFT JOIN locations l ON sq.location_id = l.id
           WHERE sq.supplier_id = %(id)s
             AND (%(pid)s::bigint IS NULL OR sq.product_id = %(pid)s)
             AND (sq.valid_from <= %(ref_date)s)
             AND (sq.valid_to IS NULL OR sq.valid_to >= %(ref_date)s)
           ORDER BY sq.id ASC""",
        {"id": id, "pid": product_id, "ref_date": ref_date},
    )

    result = []
    for q in quotes:
        breaks = execute_sql(
            """SELECT min_qty, unit_price
               FROM quote_price_breaks
               WHERE quote_id = %(qid)s
               ORDER BY min_qty ASC""",
            {"qid": q["id"]},
        )
        item = {
            "sku_code": q["sku_code"],
            "store_code": q["store_code"],
            "base_unit_price": q["base_unit_price"],
            "currency": q["currency"],
            "lead_time_days": q["lead_time_days"],
            "moq": q["moq"],
            "valid_from": q["valid_from"],
            "valid_to": q["valid_to"],
            "price_breaks": breaks,
        }
        result.append(item)

    return result

