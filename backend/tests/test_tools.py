import re
import pytest
from backend.core.database import execute_sql
from backend.agent.tools.stock_risk import get_stock_risk
from backend.agent.tools.forecast_summary import get_forecasts_summary
from backend.agent.tools.supplier_quotes import get_supplier_quotes
from backend.agent.tools.supplier_options import evaluate_supplier_options
from backend.agent.tools.purchase_request import create_purchase_request


def _db_is_accessible() -> bool:
    try:
        execute_sql("SELECT 1;")
        return True
    except Exception:
        return False


def test_tool_registry_unknown_tool():
    """Kiểm tra execute_tool xử lý an toàn khi gọi tool không tồn tại."""
    from backend.agent.tool_registry import execute_tool
    import json
    res_str = execute_tool("unknown_tool", {})
    res = json.loads(res_str)
    assert "error" in res


def test_create_purchase_request_security_enforcement():
    """Kiểm tra ràng buộc bảo mật: Chặn đứng tạo purchase_order trực tiếp từ agent."""
    lines = [{"product_id": 1, "purchase_order": True, "qty": 10, "unit_price": 10.0}]
    with pytest.raises(PermissionError) as exc_info:
        create_purchase_request(lines=lines, summary="Thử nghiệm tạo PO trái phép")
    assert "Bảo mật" in str(exc_info.value)


requires_db = pytest.mark.skipif(
    not _db_is_accessible(),
    reason="Không thể kết nối PostgreSQL trên localhost:5432. Bỏ qua test cơ sở dữ liệu."
)


@requires_db
def test_get_stock_risk():
    """
    Test tool get_stock_risk với SKU FOODS_3_795 tại CA_1.
    Kỳ vọng: risk_level='HIGH', days_of_cover < 2.
    """
    res = get_stock_risk(product_id="FOODS_3_795", location_id="CA_1")
    assert res is not None
    assert res["sku_code"] == "FOODS_3_795"
    assert res["store_code"] == "CA_1"
    assert res["risk_level"] == "HIGH"
    assert res["days_of_cover"] < 2.0
    assert res["reorder_point"] > 0
    assert "CẦN ĐẶT HÀNG GẤP" in res["recommendation"]


@requires_db
def test_get_forecasts_summary():
    """
    Test tool get_forecasts_summary cho các SKU.
    Kỳ vọng: Lấy được chuỗi dự báo theo ngày, p50 >= 0, tổng nhu cầu hợp lý.
    """
    # Lấy danh sách 30 SKU từ DB
    p_rows = execute_sql("SELECT sku_code FROM products LIMIT 30;")
    sku_list = [r["sku_code"] for r in p_rows]
    assert len(sku_list) > 0

    res = get_forecasts_summary(product_ids=sku_list, location_id="CA_1", horizon_days=28)
    assert res is not None
    assert res["count"] == len(sku_list)

    total_records = 0
    for prod in res["products"]:
        assert prod["horizon_days"] <= 28
        assert prod["avg_daily_p50"] >= 0.0
        assert prod["total_demand_p50"] >= 0.0
        total_records += len(prod["daily_forecasts"])

    # Nếu có đủ 30 SKU và 28 ngày thì tổng bản ghi là 840
    if len(sku_list) == 30 and all(p["horizon_days"] == 28 for p in res["products"]):
        assert total_records == 840


@requires_db
def test_get_supplier_quotes():
    """
    Test tool get_supplier_quotes với qty=100.
    Kỳ vọng: SUP_FOOD_FAST áp dụng chiết khấu 3%, giá còn 1.89 USD.
    """
    quotes = get_supplier_quotes(product_id="FOODS_3_795", location_id="CA_1", qty=100)
    assert len(quotes) >= 1

    fast_quote = next((q for q in quotes if "FAST" in q["supplier_code"]), None)
    assert fast_quote is not None
    assert fast_quote["lead_time_days"] == 3
    # Báo giá gốc 1.95, chiết khấu 3% -> 1.89 USD
    assert fast_quote["applicable_unit_price"] == 1.89
    assert fast_quote["total_value"] == 189.00


@requires_db
def test_evaluate_supplier_options():
    """
    Test tool evaluate_supplier_options.
    Kỳ vọng: Nhãn CHEAPEST gán cho NCC có unit_price thấp nhất thực tế.
    """
    res = evaluate_supplier_options(product_id="FOODS_3_795", location_id="CA_1", qty=100)
    assert "options" in res
    options = res["options"]

    assert "CHEAPEST" in options
    assert "FASTEST" in options
    assert "BALANCED" in options

    # Kiểm tra phương án CHEAPEST thực tế phải có giá thấp nhất trong tất cả các báo giá
    all_prices = [q["applicable_unit_price"] for q in res["all_ranked_suppliers"]]
    cheapest_price = options["CHEAPEST"]["unit_price"]
    assert cheapest_price == min(all_prices)

    # Phương án FASTEST phải có lead_time_days thấp nhất
    all_leads = [q["lead_time_days"] for q in res["all_ranked_suppliers"]]
    fastest_lead = options["FASTEST"]["lead_time_days"]
    assert fastest_lead == min(all_leads)


@requires_db
def test_create_purchase_request():
    """
    Test tool create_purchase_request.
    Kỳ vọng: Tạo phiếu DRAFT, confidence trong [0, 1], mã phiếu PR-YYYYMMDD-XXXX.
    Ràng buộc an toàn: purchase_orders không phát sinh dòng mới.
    """
    po_count_before = execute_sql("SELECT COUNT(*) as cnt FROM purchase_orders;")[0]["cnt"]

    # Lấy thông tin ID cần thiết từ DB
    p_id = execute_sql("SELECT id FROM products WHERE sku_code = 'FOODS_3_795' LIMIT 1;")[0]["id"]
    l_id = execute_sql("SELECT id FROM locations WHERE code = 'CA_1' LIMIT 1;")[0]["id"]
    s_id = execute_sql("SELECT id FROM suppliers WHERE code LIKE '%FAST%' LIMIT 1;")[0]["id"]

    lines = [
        {
            "product_id": p_id,
            "location_id": l_id,
            "supplier_id": s_id,
            "option_type": "FASTEST",
            "qty": 100,
            "unit_price": 1.89,
            "needed_by_date": "2016-05-26",
            "is_recommended": True,
            "reason": "Tồn kho sắp hết trong 0.9 ngày, cần giao khẩn cấp trong 3 ngày.",
        }
    ]

    res = create_purchase_request(lines=lines, summary="Phiếu đề xuất mua hàng thử nghiệm")
    assert res is not None
    assert res["status"] == "DRAFT"
    assert res["lines_count"] == 1
    assert res["total_value"] == 189.00

    # Kiểm tra format mã phiếu
    assert re.match(r"^PR-\d{8}-\d{4}$", res["code"])

    # Kiểm tra trường confidence trong purchase_request_lines
    req_id = res["request_id"]
    line_row = execute_sql("SELECT confidence, line_value FROM purchase_request_lines WHERE request_id = %(rid)s;", {"rid": req_id})[0]
    assert line_row["confidence"] is not None
    assert 0.0 <= float(line_row["confidence"]) <= 1.0
    assert float(line_row["line_value"]) == 189.00

    # Ràng buộc an toàn tuyệt đối: purchase_orders KHÔNG được phép có dòng mới
    po_count_after = execute_sql("SELECT COUNT(*) as cnt FROM purchase_orders;")[0]["cnt"]
    assert po_count_after == po_count_before


@requires_db
def test_sql_like_with_params():
    """
    Truy vấn: WHERE model_version LIKE '%%full%%' AND id = %(id)s
    Kỳ vọng: trả về dòng đúng, không lỗi ProgrammingError
    """
    full_runs = execute_sql("SELECT id, model_version FROM forecast_runs WHERE model_version LIKE '%%full%%' LIMIT 1", {})
    assert len(full_runs) >= 1
    target_id = full_runs[0]["id"]

    rows = execute_sql(
        "SELECT id, model_version FROM forecast_runs WHERE model_version LIKE '%%full%%' AND id = %(id)s",
        {"id": target_id},
    )
    assert len(rows) == 1
    assert rows[0]["id"] == target_id
    assert "full" in rows[0]["model_version"]


@requires_db
def test_sql_no_percent_confusion():
    """
    Truy vấn LIKE với 3 ký tự % khác nhau trong cùng câu
    Kỳ vọng: không lỗi, kết quả đúng
    """
    rows = execute_sql(
        """SELECT id, sku_code
           FROM products
           WHERE sku_code LIKE 'FOODS%%'
             AND sku_code LIKE '%%_%%'
             AND sku_code LIKE '%%074'
             AND category = %(cat)s""",
        {"cat": "FOODS"},
    )
    assert len(rows) == 1
    assert rows[0]["sku_code"] == "FOODS_2_074"
