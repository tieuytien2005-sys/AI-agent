import datetime as dt
from typing import Optional, Union, Dict, Any
from backend.core.database import execute_sql
from backend.core.config import PRODUCTION_RUN_ID


def _resolve_ids(product_id: Union[int, str], location_id: Union[int, str]) -> tuple[int, int, str, str]:
    """Hỗ trợ phân giải linh hoạt id dạng số hoặc mã code (sku_code/location_code)."""
    p_rows = execute_sql(
        "SELECT id, sku_code FROM products WHERE id::text = %(p)s OR sku_code = %(p)s LIMIT 1",
        {"p": str(product_id)}
    )
    if not p_rows:
        raise ValueError(f"Không tìm thấy sản phẩm với id hoặc mã '{product_id}'")
    p_id = p_rows[0]["id"]
    sku_code = p_rows[0]["sku_code"]

    l_rows = execute_sql(
        "SELECT id, code FROM locations WHERE id::text = %(l)s OR code = %(l)s LIMIT 1",
        {"l": str(location_id)}
    )
    if not l_rows:
        raise ValueError(f"Không tìm thấy địa điểm/kho với id hoặc mã '{location_id}'")
    l_id = l_rows[0]["id"]
    store_code = l_rows[0]["code"]

    return p_id, l_id, sku_code, store_code


def get_stock_risk(
    product_id: Union[int, str],
    location_id: Union[int, str],
    as_of_date: Optional[str] = None
) -> Dict[str, Any]:
    """
    Xác định rủi ro tồn kho (thiếu hàng hoặc dư hàng) cho một SKU tại kho cụ thể.
    """
    p_id, l_id, sku_code, store_code = _resolve_ids(product_id, location_id)

    # 1. Lấy tồn kho hiện tại và hàng đang về
    inv_rows = execute_sql(
        "SELECT on_hand, on_order FROM inventory_levels WHERE product_id = %(p)s AND location_id = %(l)s",
        {"p": p_id, "l": l_id}
    )
    on_hand = inv_rows[0]["on_hand"] if inv_rows else 0
    on_order = inv_rows[0]["on_order"] if inv_rows else 0

    # 2. Lấy chính sách tồn kho và nhà cung cấp mặc định
    policy_rows = execute_sql(
        """SELECT ip.safety_days, ip.target_cover_days, ip.max_stock_days, ip.default_supplier_id,
                  COALESCE(s.lead_time_days, 7) as lead_time_days
           FROM inventory_policies ip
           LEFT JOIN suppliers s ON s.id = ip.default_supplier_id
           WHERE ip.product_id = %(p)s AND ip.location_id = %(l)s""",
        {"p": p_id, "l": l_id}
    )
    if policy_rows:
        safety_days = float(policy_rows[0]["safety_days"])
        target_cover_days = float(policy_rows[0]["target_cover_days"])
        max_stock_days = float(policy_rows[0]["max_stock_days"])
        lead_time_days = int(policy_rows[0]["lead_time_days"])
    else:
        safety_days = 7.0
        target_cover_days = 21.0
        max_stock_days = 60.0
        lead_time_days = 7

    # 3. Lấy dữ liệu dự báo nhu cầu
    forecast_sql = """
        SELECT df.forecast_date, df.p10, df.p50, df.p90, df.run_id
        FROM demand_forecasts df
        JOIN forecast_runs fr ON fr.id = df.run_id
        WHERE df.product_id = %(p)s AND df.location_id = %(l)s
    """
    params = {"p": p_id, "l": l_id}
    if PRODUCTION_RUN_ID:
        forecast_sql += " AND df.run_id = %(run_id)s"
        params["run_id"] = PRODUCTION_RUN_ID
    else:
        forecast_sql += " AND (fr.model_version LIKE '%%full%%' OR fr.id = (SELECT MAX(id) FROM forecast_runs))"

    forecast_sql += " ORDER BY fr.created_at DESC, df.forecast_date ASC LIMIT 28"
    forecast_rows = execute_sql(forecast_sql, params)

    if forecast_rows:
        p50_list = [float(r["p50"]) for r in forecast_rows]
        avg_demand = sum(p50_list) / len(p50_list)
        forecast_28d_total = sum(p50_list)
        run_id = forecast_rows[0]["run_id"]
    else:
        # Fallback nếu chưa có dự báo ML: lấy từ lịch sử bán hàng 28 ngày gần nhất
        hist_rows = execute_sql(
            """SELECT COALESCE(AVG(qty_sold), 1.0) as avg_qty, COALESCE(SUM(qty_sold), 28.0) as sum_qty
               FROM (
                   SELECT qty_sold FROM demand_history
                   WHERE product_id = %(p)s AND location_id = %(l)s
                   ORDER BY sale_date DESC LIMIT 28
               ) t""",
            {"p": p_id, "l": l_id}
        )
        avg_demand = float(hist_rows[0]["avg_qty"]) if hist_rows else 1.0
        forecast_28d_total = float(hist_rows[0]["sum_qty"]) if hist_rows else 28.0
        run_id = None

    avg_demand = max(avg_demand, 0.05)
    safety_stock = round(avg_demand * safety_days, 1)
    reorder_point = round(avg_demand * lead_time_days + safety_stock, 1)
    days_of_cover = round(on_hand / avg_demand, 1) if avg_demand > 0 else 999.0

    net_inventory = on_hand + on_order
    # Cảnh báo rủi ro dựa trên tổng tồn (net_inventory) hoặc tồn thực tế tại chỗ (days_of_cover)
    if net_inventory <= reorder_point or days_of_cover < 2.0:
        risk_level = "HIGH"
        risk_type = "SHORTAGE"
    elif net_inventory <= reorder_point * 1.3 or days_of_cover <= safety_days:
        risk_level = "MEDIUM"
        risk_type = "SHORTAGE"
    elif net_inventory <= reorder_point * 1.5:
        risk_level = "LOW"
        risk_type = "SHORTAGE"
    else:
        risk_level = "NONE"
        risk_type = "EXCESS" if on_hand > avg_demand * max_stock_days else "NORMAL"

    ref_date = dt.date.fromisoformat(as_of_date) if as_of_date else dt.date.today()
    days_until_out = int(on_hand / avg_demand)
    projected_stockout_date = str(ref_date + dt.timedelta(days=days_until_out))

    if risk_level in ("HIGH", "MEDIUM"):
        shortfall = max(0.0, reorder_point - net_inventory + (target_cover_days * avg_demand))
        recommendation = f"CẦN ĐẶT HÀNG GẤP: thiếu hụt ước tính {round(shortfall)} pcs để đảm bảo cover {target_cover_days} ngày."
    elif risk_type == "EXCESS":
        recommendation = "CẢNH BÁO DƯ HÀNG: không đặt thêm hàng trong kỳ này."
    else:
        recommendation = "Mức tồn kho nằm trong ngưỡng an toàn."

    return {
        "product_id": p_id,
        "location_id": l_id,
        "sku_code": sku_code,
        "store_code": store_code,
        "on_hand": on_hand,
        "on_order": on_order,
        "avg_daily_demand_28d": round(avg_demand, 2),
        "safety_stock": safety_stock,
        "reorder_point": reorder_point,
        "days_of_cover": days_of_cover,
        "risk_level": risk_level,
        "risk_type": risk_type,
        "projected_stockout_date": projected_stockout_date,
        "forecast_28d_total": round(forecast_28d_total, 1),
        "forecast_run_id": run_id,
        "recommendation": recommendation,
    }

