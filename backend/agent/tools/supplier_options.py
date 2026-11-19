import datetime as dt
from typing import Optional, Union, Dict, Any
from backend.agent.tools.supplier_quotes import get_supplier_quotes


def evaluate_supplier_options(
    product_id: Union[int, str] = "FOODS_3_795",
    location_id: Union[int, str] = "CA_1",
    qty: Optional[int] = None,
    needed_by_date: Optional[str] = None,
    weights: Optional[Dict[str, float]] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Đánh giá và xếp hạng các nhà cung cấp theo 3 phương án: CHEAPEST, FASTEST, BALANCED.
    Gán nhãn dựa trên báo giá và hiệu suất thực tế, không dựa vào tên/tag mẫu của NCC.
    """
    qty = int(qty or kwargs.get("quantity") or 100)
    weights = weights or {"price": 0.4, "lead_time": 0.3, "reliability": 0.3}
    w_price = weights.get("price", 0.4)
    w_lead = weights.get("lead_time", 0.3)
    w_rel = weights.get("reliability", 0.3)

    # 1. Lấy danh sách báo giá hiệu lực
    quotes = get_supplier_quotes(product_id, location_id, qty)
    if not quotes:
        raise ValueError(f"Không có báo giá hiệu lực nào cho sản phẩm '{product_id}'")

    prices = [q["applicable_unit_price"] for q in quotes]
    leads = [q["lead_time_days"] for q in quotes]

    min_p, max_p = min(prices), max(prices)
    min_l, max_l = min(leads), max(leads)

    # 2. Chuẩn hóa và chấm điểm từng NCC
    scored_quotes = []
    for q in quotes:
        # Chuẩn hóa min-max: giá trị thấp là tốt -> (val - min) / (max - min)
        norm_p = (q["applicable_unit_price"] - min_p) / (max_p - min_p) if max_p > min_p else 0.0
        norm_l = (q["lead_time_days"] - min_l) / (max_l - min_l) if max_l > min_l else 0.0
        on_time = q["on_time_rate"]

        composite_score = (
            w_price * (1.0 - norm_p) +
            w_lead * (1.0 - norm_l) +
            w_rel * on_time
        ) * 100.0

        item = dict(q)
        item["composite_score"] = round(composite_score, 2)
        scored_quotes.append(item)

    # 3. Phân định 3 phương án thực tế
    cheapest_item = min(scored_quotes, key=lambda x: (x["applicable_unit_price"], -x["composite_score"]))
    fastest_item = min(scored_quotes, key=lambda x: (x["lead_time_days"], -x["composite_score"]))
    balanced_item = max(scored_quotes, key=lambda x: x["composite_score"])

    # Xây dựng chi tiết từng phương án
    options = {}

    options["CHEAPEST"] = {
        "option_type": "CHEAPEST",
        "supplier_id": cheapest_item["supplier_id"],
        "supplier_code": cheapest_item["supplier_code"],
        "supplier_name": cheapest_item["supplier_name"],
        "quote_id": cheapest_item["quote_id"],
        "unit_price": cheapest_item["applicable_unit_price"],
        "total_value": cheapest_item["total_value"],
        "lead_time_days": cheapest_item["lead_time_days"],
        "on_time_rate": cheapest_item["on_time_rate"],
        "defect_rate": cheapest_item["defect_rate"],
        "score": cheapest_item["composite_score"],
        "pros": f"Đơn giá thấp nhất ({cheapest_item['applicable_unit_price']} {cheapest_item['currency']}), tối ưu tổng chi phí.",
        "trade_offs": f"Lead time {cheapest_item['lead_time_days']} ngày và tỷ lệ giao đúng hạn {round(cheapest_item['on_time_rate']*100, 1)}%.",
    }

    options["FASTEST"] = {
        "option_type": "FASTEST",
        "supplier_id": fastest_item["supplier_id"],
        "supplier_code": fastest_item["supplier_code"],
        "supplier_name": fastest_item["supplier_name"],
        "quote_id": fastest_item["quote_id"],
        "unit_price": fastest_item["applicable_unit_price"],
        "total_value": fastest_item["total_value"],
        "lead_time_days": fastest_item["lead_time_days"],
        "on_time_rate": fastest_item["on_time_rate"],
        "defect_rate": fastest_item["defect_rate"],
        "score": fastest_item["composite_score"],
        "pros": f"Thời gian giao nhanh nhất ({fastest_item['lead_time_days']} ngày), độ tin cậy giao hàng {round(fastest_item['on_time_rate']*100, 1)}%.",
        "trade_offs": f"Đơn giá {fastest_item['applicable_unit_price']} {fastest_item['currency']} (cao hơn phương án rẻ nhất).",
    }

    options["BALANCED"] = {
        "option_type": "BALANCED",
        "supplier_id": balanced_item["supplier_id"],
        "supplier_code": balanced_item["supplier_code"],
        "supplier_name": balanced_item["supplier_name"],
        "quote_id": balanced_item["quote_id"],
        "unit_price": balanced_item["applicable_unit_price"],
        "total_value": balanced_item["total_value"],
        "lead_time_days": balanced_item["lead_time_days"],
        "on_time_rate": balanced_item["on_time_rate"],
        "defect_rate": balanced_item["defect_rate"],
        "score": balanced_item["composite_score"],
        "pros": f"Điểm tổng hợp cao nhất ({balanced_item['composite_score']}/100), cân bằng hài hòa giữa giá, tốc độ và chất lượng.",
        "trade_offs": "Mức giá và thời gian giao hàng nằm ở khoảng trung bình.",
    }

    # Xác định khuyến nghị dựa trên needed_by_date
    if needed_by_date:
        try:
            target_d = dt.date.fromisoformat(needed_by_date)
            days_available = (target_d - dt.date.today()).days
            if days_available <= fastest_item["lead_time_days"] + 1:
                recommended = "FASTEST"
            elif days_available < balanced_item["lead_time_days"]:
                recommended = "FASTEST"
            else:
                recommended = "BALANCED"
        except Exception:
            recommended = "BALANCED"
    else:
        recommended = "BALANCED"

    return {
        "product_id": quotes[0].get("product_id", product_id),
        "sku_code": quotes[0].get("sku_code", str(product_id)),
        "qty": qty,
        "recommended_option": recommended,
        "options": options,
        "all_ranked_suppliers": scored_quotes,
    }

