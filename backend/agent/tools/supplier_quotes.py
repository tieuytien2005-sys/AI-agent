import datetime as dt
from typing import Optional, Union, List, Dict, Any
from backend.core.database import execute_sql


def get_supplier_quotes(
    product_id: Union[int, str] = "FOODS_3_795",
    location_id: Union[int, str] = "CA_1",
    qty: Optional[int] = None,
    as_of_date: Optional[str] = None,
    **kwargs
) -> List[Dict[str, Any]]:
    """
    Lấy danh sách báo giá hiệu lực của các nhà cung cấp cho sản phẩm tại địa điểm theo số lượng qty.
    Tự động áp dụng chiết khấu theo số lượng (price breaks).
    """
    qty = int(qty or kwargs.get("quantity") or 100)

    # Phân giải ID
    p_rows = execute_sql(
        "SELECT id, sku_code FROM products WHERE id::text = %(p)s OR sku_code = %(p)s LIMIT 1",
        {"p": str(product_id)}
    )
    if not p_rows:
        raise ValueError(f"Không tìm thấy sản phẩm '{product_id}'")
    p_id = p_rows[0]["id"]
    sku_code = p_rows[0]["sku_code"]

    l_rows = execute_sql(
        "SELECT id, code FROM locations WHERE id::text = %(l)s OR code = %(l)s LIMIT 1",
        {"l": str(location_id)}
    )
    if not l_rows:
        raise ValueError(f"Không tìm thấy địa điểm '{location_id}'")
    l_id = l_rows[0]["id"]

    max_date_rows = execute_sql(
        "SELECT MAX(valid_from) as latest_from FROM supplier_quotes WHERE product_id = %(p)s",
        {"p": p_id}
    )
    latest_from = str(max_date_rows[0]["latest_from"]) if max_date_rows and max_date_rows[0]["latest_from"] else str(dt.date.today())

    # Nếu không truyền as_of_date, dùng ngày báo giá mới nhất trong hệ thống
    if not as_of_date:
        as_of_date = latest_from

    sql = """
        SELECT sq.id as quote_id, sq.supplier_id, sq.base_unit_price, sq.currency,
               sq.lead_time_days, sq.moq, sq.valid_from, sq.valid_to,
               s.code as supplier_code, s.name as supplier_name,
               se.on_time_rate, se.defect_rate, se.total_score,
               qpb.min_qty, qpb.unit_price as break_unit_price
        FROM supplier_quotes sq
        JOIN suppliers s ON s.id = sq.supplier_id
        LEFT JOIN (
            SELECT DISTINCT ON (supplier_id) supplier_id, on_time_rate, defect_rate, total_score
            FROM supplier_evaluations
            ORDER BY supplier_id, period_end DESC
        ) se ON se.supplier_id = sq.supplier_id
        LEFT JOIN quote_price_breaks qpb ON qpb.quote_id = sq.id
        WHERE sq.product_id = %(p)s
          AND (sq.location_id = %(l)s OR sq.location_id IS NULL)
          AND sq.valid_from <= %(as_of_date)s
          AND (sq.valid_to IS NULL OR sq.valid_to >= %(as_of_date)s)
        ORDER BY sq.supplier_id, qpb.min_qty ASC
    """
    rows = execute_sql(sql, {"p": p_id, "l": l_id, "as_of_date": as_of_date})
    # Fallback về kỳ báo giá mới nhất nếu LLM truyền ngày nằm ngoài tập dữ liệu mô phỏng M5 (2011-2016)
    if not rows and as_of_date != latest_from:
        rows = execute_sql(sql, {"p": p_id, "l": l_id, "as_of_date": latest_from})

    # Nhóm theo quote_id để tính chiết khấu theo qty
    quotes_by_id: Dict[int, Dict[str, Any]] = {}
    for r in rows:
        qid = r["quote_id"]
        if qid not in quotes_by_id:
            quotes_by_id[qid] = {
                "quote_id": qid,
                "supplier_id": r["supplier_id"],
                "supplier_code": r["supplier_code"],
                "supplier_name": r["supplier_name"],
                "product_id": p_id,
                "sku_code": sku_code,
                "base_unit_price": float(r["base_unit_price"]),
                "currency": r["currency"],
                "lead_time_days": int(r["lead_time_days"]),
                "moq": int(r["moq"]),
                "on_time_rate": float(r["on_time_rate"]) if r["on_time_rate"] is not None else 0.85,
                "defect_rate": float(r["defect_rate"]) if r["defect_rate"] is not None else 0.02,
                "total_score": float(r["total_score"]) if r["total_score"] is not None else 80.0,
                "price_breaks": [],
            }
        if r["min_qty"] is not None and r["break_unit_price"] is not None:
            quotes_by_id[qid]["price_breaks"].append({
                "min_qty": int(r["min_qty"]),
                "unit_price": float(r["break_unit_price"])
            })

    # Xác định đơn giá áp dụng
    final_quotes = []
    for q in quotes_by_id.values():
        applicable_price = q["base_unit_price"]
        applied_min_qty = 0
        discount_applied = 0.0

        for pb in sorted(q["price_breaks"], key=lambda x: x["min_qty"], reverse=True):
            if qty >= pb["min_qty"]:
                applicable_price = pb["unit_price"]
                applied_min_qty = pb["min_qty"]
                discount_applied = round((1.0 - applicable_price / q["base_unit_price"]), 4)
                break

        unit_price = round(applicable_price, 2)
        total_value = round(qty * unit_price, 2)

        final_quotes.append({
            "quote_id": q["quote_id"],
            "supplier_id": q["supplier_id"],
            "supplier_code": q["supplier_code"],
            "supplier_name": q["supplier_name"],
            "product_id": q["product_id"],
            "sku_code": q["sku_code"],
            "base_unit_price": q["base_unit_price"],
            "applicable_unit_price": unit_price,
            "qty": qty,
            "total_value": total_value,
            "currency": q["currency"],
            "lead_time_days": q["lead_time_days"],
            "moq": q["moq"],
            "applied_discount_rate": discount_applied,
            "applied_tier_min_qty": applied_min_qty,
            "on_time_rate": q["on_time_rate"],
            "defect_rate": q["defect_rate"],
            "performance_score": q["total_score"],
        })

    return final_quotes

