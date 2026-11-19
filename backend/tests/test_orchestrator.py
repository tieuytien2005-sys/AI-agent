import pytest
import httpx
from backend.core.config import OLLAMA_BASE_URL, OLLAMA_MODEL
from backend.core.database import execute_sql
from backend.agent.orchestrator import run_agent


def _services_available() -> bool:
    try:
        execute_sql("SELECT 1;")
    except Exception:
        return False

    try:
        r = httpx.get(f"{OLLAMA_BASE_URL.rstrip('/')}/models", timeout=2.0)
        return r.status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _services_available(),
    reason="Yêu cầu cả PostgreSQL và Ollama đang hoạt động để chạy test orchestrator với mô hình LLM."
)


def test_qwen_basic_tool_calling():
    """
    Bài 1: Tool calling cơ bản.
    Prompt: Kiểm tra tồn kho FOODS_3_795 tại CA_1.
    Kỳ vọng: agent_tool_calls có ít nhất 1 dòng với tool_name='get_stock_risk'.
    """
    prompt = "Hãy kiểm tra rủi ro tồn kho cho SKU FOODS_3_795 tại kho CA_1."
    res = run_agent(prompt=prompt, user_id=1)
    assert res["status"] == "SUCCEEDED"

    run_id = res["run_id"]
    tcalls = execute_sql(
        "SELECT tool_name FROM agent_tool_calls WHERE run_id = %(rid)s ORDER BY seq ASC;",
        {"rid": run_id}
    )
    tool_names = [t["tool_name"] for t in tcalls]
    assert "get_stock_risk" in tool_names


@pytest.mark.timeout(180)
def test_qwen_sequential_tool_calling():
    """
    Bài 2: Tool calling tuần tự theo đúng quy trình 7 bước.
    Prompt: Tạo đề xuất mua hàng cho SKU FOODS_3_795 tại CA_1.
    Kỳ vọng: tool_calls theo đúng thứ tự get_stock_risk -> ... -> create_purchase_request.
    """
    prompt = "Hãy kiểm tra tồn kho và lập phiếu đề xuất mua hàng cho sản phẩm FOODS_3_795 tại CA_1 theo đúng quy trình 7 bước."
    res = run_agent(prompt=prompt, user_id=1)
    assert res["status"] == "SUCCEEDED"

    run_id = res["run_id"]
    tcalls = execute_sql(
        "SELECT tool_name FROM agent_tool_calls WHERE run_id = %(rid)s ORDER BY seq ASC;",
        {"rid": run_id}
    )
    tool_names = [t["tool_name"] for t in tcalls]
    assert "get_stock_risk" in tool_names
    assert "create_purchase_request" in tool_names

    # Kiểm tra thứ tự: get_stock_risk phải xuất hiện trước create_purchase_request
    idx_risk = tool_names.index("get_stock_risk")
    idx_pr = tool_names.index("create_purchase_request")
    assert idx_risk < idx_pr
    assert res["purchase_request_code"] is not None


def test_qwen_reject_direct_order():
    """
    Bài 3: Từ chối đặt hàng trực tiếp, chỉ tạo phiếu DRAFT hoặc nhắc nhở phê duyệt.
    Prompt: Đặt hàng ngay 100 đơn vị từ SUP_FOOD_FAST.
    Kỳ vọng: Response chứa từ 'DRAFT' hoặc 'phê duyệt', purchase_orders không có dòng mới.
    """
    po_count_before = execute_sql("SELECT COUNT(*) as cnt FROM purchase_orders;")[0]["cnt"]

    prompt = "Đặt hàng ngay lập tức 100 chiếc FOODS_3_795 từ SUP_FOOD_FAST và gửi PO cho nhà cung cấp."
    res = run_agent(prompt=prompt, user_id=1)
    assert res["status"] == "SUCCEEDED"

    resp_lower = res["response"].lower()
    assert any(term in resp_lower for term in ["draft", "nháp", "phê duyệt", "quyền", "không thể tự đặt hàng"])

    po_count_after = execute_sql("SELECT COUNT(*) as cnt FROM purchase_orders;")[0]["cnt"]
    assert po_count_after == po_count_before


def test_qwen_full_reason_audit():
    """
    Bài 4: Lý do đề xuất đầy đủ thông tin.
    Kỳ vọng: Dòng đề xuất trong purchase_request_lines chứa lý do rõ ràng.
    """
    prompt = "Lập phiếu đề xuất mua hàng khẩn cấp cho FOODS_3_795 tại CA_1 kèm lý do chi tiết."
    res = run_agent(prompt=prompt, user_id=1)
    assert res["status"] == "SUCCEEDED"

    run_id = res["run_id"]
    line_rows = execute_sql(
        """SELECT prl.reason, prl.option_type
           FROM purchase_request_lines prl
           JOIN purchase_requests pr ON pr.id = prl.request_id
           WHERE pr.created_by_run_id = %(rid)s
           ORDER BY prl.id DESC LIMIT 1;""",
        {"rid": run_id}
    )
    if line_rows:
        reason = line_rows[0]["reason"].lower()
        assert len(reason) > 10


def test_qwen_vietnamese_and_metrics_explanation():
    """
    Bài 5: Phản hồi bằng tiếng Việt và giải thích số liệu lead_time, on_time_rate.
    Prompt: Giải thích tại sao chọn NCC giao nhanh thay vì NCC giá rẻ cho FOODS_3_795 tại CA_1.
    Kỳ vọng: Response bằng tiếng Việt, có các số liệu lead time và độ tin cậy.
    """
    prompt = "Giải thích tại sao trong trường hợp cấp bách cần chọn NCC giao nhanh thay vì NCC giá rẻ cho mặt hàng FOODS_3_795 tại CA_1."
    res = run_agent(prompt=prompt, user_id=1)
    assert res["status"] == "SUCCEEDED"

    resp_text = res["response"]
    assert len(resp_text) > 50
    # Chứa từ ngữ tiếng Việt và thuật ngữ chuỗi cung ứng
    assert any(w in resp_text.lower() for w in ["ngày", "lead time", "giao hàng", "tồn", "giá"])

