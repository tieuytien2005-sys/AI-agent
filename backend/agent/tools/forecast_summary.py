from typing import List, Union, Dict, Any
from backend.core.database import execute_sql
from backend.core.config import PRODUCTION_RUN_ID


def get_forecasts_summary(
    product_ids: Union[List[Union[int, str]], Union[int, str]] = "FOODS_3_795",
    location_id: Union[int, str] = "CA_1",
    horizon_days: int = 28,
    **kwargs
) -> Dict[str, Any]:
    """
    Lấy tóm tắt dự báo nhu cầu (p10, p50, p90) theo sản phẩm và địa điểm trong khoảng horizon_days.
    """
    if kwargs.get("product_id") and product_ids == "FOODS_3_795":
        product_ids = kwargs["product_id"]
    if not isinstance(product_ids, list):
        product_ids = [product_ids]

    # Phân giải location_id
    l_rows = execute_sql(
        "SELECT id, code FROM locations WHERE id::text = %(l)s OR code = %(l)s LIMIT 1",
        {"l": str(location_id)}
    )
    if not l_rows:
        raise ValueError(f"Không tìm thấy địa điểm/kho với id hoặc mã '{location_id}'")
    l_id = l_rows[0]["id"]
    store_code = l_rows[0]["code"]

    results = []
    for pid in product_ids:
        p_rows = execute_sql(
            "SELECT id, sku_code, name FROM products WHERE id::text = %(p)s OR sku_code = %(p)s LIMIT 1",
            {"p": str(pid)}
        )
        if not p_rows:
            continue
        p_id = p_rows[0]["id"]
        sku_code = p_rows[0]["sku_code"]
        p_name = p_rows[0]["name"]

        sql = """
            SELECT df.forecast_date, df.p10, df.p50, df.p90, df.run_id,
                   fr.model_name, fr.model_version
            FROM demand_forecasts df
            JOIN forecast_runs fr ON fr.id = df.run_id
            WHERE df.product_id = %(p)s AND df.location_id = %(l)s
        """
        params = {"p": p_id, "l": l_id, "limit": horizon_days}
        if PRODUCTION_RUN_ID:
            sql += " AND df.run_id = %(run_id)s"
            params["run_id"] = PRODUCTION_RUN_ID
        else:
            sql += " AND (fr.model_version LIKE '%%full%%' OR fr.id = (SELECT MAX(id) FROM forecast_runs))"

        sql += " ORDER BY fr.created_at DESC, df.forecast_date ASC LIMIT %(limit)s"
        rows = execute_sql(sql, params)

        if rows:
            daily_series = [
                {
                    "forecast_date": str(r["forecast_date"]),
                    "p10": float(r["p10"]) if r["p10"] is not None else float(r["p50"]),
                    "p50": float(r["p50"]),
                    "p90": float(r["p90"]) if r["p90"] is not None else float(r["p50"]),
                }
                for r in rows
            ]
            total_p50 = sum(d["p50"] for d in daily_series)
            avg_p50 = total_p50 / len(daily_series)
            avg_p10 = sum(d["p10"] for d in daily_series) / len(daily_series)
            avg_p90 = sum(d["p90"] for d in daily_series) / len(daily_series)
            run_id = rows[0]["run_id"]
            model_name = rows[0]["model_name"]
            model_version = rows[0]["model_version"]
        else:
            daily_series = []
            total_p50 = 0.0
            avg_p50 = 0.0
            avg_p10 = 0.0
            avg_p90 = 0.0
            run_id = None
            model_name = "N/A"
            model_version = "N/A"

        results.append({
            "product_id": p_id,
            "sku_code": sku_code,
            "product_name": p_name,
            "location_id": l_id,
            "store_code": store_code,
            "run_id": run_id,
            "model_name": model_name,
            "model_version": model_version,
            "horizon_days": len(daily_series),
            "total_demand_p50": round(total_p50, 1),
            "avg_daily_p10": round(avg_p10, 2),
            "avg_daily_p50": round(avg_p50, 2),
            "avg_daily_p90": round(avg_p90, 2),
            "daily_forecasts": daily_series,
        })

    return {
        "store_code": store_code,
        "count": len(results),
        "products": results,
    }

