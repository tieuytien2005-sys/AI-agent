import json
from typing import Dict, Any, Optional
from backend.agent.tools.stock_risk import get_stock_risk
from backend.agent.tools.forecast_summary import get_forecasts_summary
from backend.agent.tools.supplier_quotes import get_supplier_quotes
from backend.agent.tools.supplier_options import evaluate_supplier_options
from backend.agent.tools.purchase_request import create_purchase_request

# Danh sách tool schema theo chuẩn OpenAI function calling
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_stock_risk",
            "description": "Kiểm tra mức độ rủi ro tồn kho (thiếu hàng hoặc dư hàng), tính toán ROP, tồn an toàn và số ngày cover cho SKU tại kho chỉ định.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {
                        "type": ["integer", "string"],
                        "description": "ID hoặc mã SKU của sản phẩm (ví dụ: 'FOODS_3_795' hoặc 1)"
                    },
                    "location_id": {
                        "type": ["integer", "string"],
                        "description": "ID hoặc mã cửa hàng/kho (ví dụ: 'CA_1' hoặc 1)"
                    },
                    "as_of_date": {
                        "type": "string",
                        "description": "Ngày kiểm tra (định dạng YYYY-MM-DD), mặc định là ngày hiện tại hoặc ngày snapshot mới nhất."
                    }
                },
                "required": ["product_id", "location_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_forecasts_summary",
            "description": "Lấy chuỗi dự báo nhu cầu chi tiết (p10, p50, p90) trong horizon_days ngày tới cho một hoặc nhiều sản phẩm tại kho.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_ids": {
                        "type": "array",
                        "items": {"type": ["integer", "string"]},
                        "description": "Danh sách ID hoặc mã SKU cần lấy dự báo."
                    },
                    "location_id": {
                        "type": ["integer", "string"],
                        "description": "ID hoặc mã kho/cửa hàng."
                    },
                    "horizon_days": {
                        "type": "integer",
                        "description": "Số ngày dự báo tương lai (mặc định 28 ngày)."
                    }
                },
                "required": ["product_ids", "location_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_supplier_quotes",
            "description": "Lấy danh sách báo giá hiệu lực của các nhà cung cấp cho sản phẩm theo số lượng dự kiến, có tính chiết khấu số lượng.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {
                        "type": ["integer", "string"],
                        "description": "ID hoặc mã SKU sản phẩm."
                    },
                    "location_id": {
                        "type": ["integer", "string"],
                        "description": "ID hoặc mã kho."
                    },
                    "qty": {
                        "type": "integer",
                        "description": "Số lượng sản phẩm dự kiến đặt hàng."
                    },
                    "as_of_date": {
                        "type": "string",
                        "description": "Ngày áp dụng báo giá (YYYY-MM-DD)."
                    }
                },
                "required": ["product_id", "location_id", "qty"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "evaluate_supplier_options",
            "description": "Đánh giá, chấm điểm và xếp hạng 3 phương án nhà cung cấp thực tế: CHEAPEST (giá rẻ nhất), FASTEST (giao nhanh nhất), BALANCED (cân bằng tối ưu).",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {
                        "type": ["integer", "string"],
                        "description": "ID hoặc mã SKU."
                    },
                    "location_id": {
                        "type": ["integer", "string"],
                        "description": "ID hoặc mã kho."
                    },
                    "qty": {
                        "type": "integer",
                        "description": "Số lượng cần đặt."
                    },
                    "needed_by_date": {
                        "type": "string",
                        "description": "Ngày cần hàng có mặt tại kho (YYYY-MM-DD)."
                    },
                    "weights": {
                        "type": "object",
                        "description": "Trọng số tùy chỉnh cho giá, tốc độ và độ tin cậy. Mặc định {'price': 0.4, 'lead_time': 0.3, 'reliability': 0.3}"
                    }
                },
                "required": ["product_id", "location_id", "qty"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_purchase_request",
            "description": "Tạo phiếu đề xuất mua hàng ở trạng thái DRAFT. Tuyệt đối không tạo đơn đặt hàng thực tế.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lines": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "product_id": {"type": "integer"},
                                "location_id": {"type": "integer"},
                                "supplier_id": {"type": "integer"},
                                "quote_id": {"type": "integer"},
                                "option_type": {"type": "string", "enum": ["CHEAPEST", "FASTEST", "BALANCED", "MANUAL"]},
                                "qty": {"type": "integer"},
                                "unit_price": {"type": "number"},
                                "needed_by_date": {"type": "string"},
                                "is_recommended": {"type": "boolean"},
                                "reason": {"type": "string"},
                                "confidence": {"type": "number"},
                                "forecast_run_id": {"type": "integer"}
                            },
                            "required": ["product_id", "location_id", "supplier_id", "qty", "unit_price", "reason"]
                        },
                        "description": "Danh sách các dòng đề xuất (mỗi phương án NCC là 1 dòng)."
                    },
                    "summary": {
                        "type": "string",
                        "description": "Tóm tắt tổng quan lý do và mục tiêu của phiếu đề xuất mua hàng."
                    }
                },
                "required": ["lines", "summary"]
            }
        }
    }
]

TOOL_FUNCTIONS = {
    "get_stock_risk": get_stock_risk,
    "get_forecasts_summary": get_forecasts_summary,
    "get_supplier_quotes": get_supplier_quotes,
    "evaluate_supplier_options": evaluate_supplier_options,
    "create_purchase_request": create_purchase_request,
}


def execute_tool(name: str, arguments: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> str:
    """
    Thực thi hàm tool theo tên và tham số truyền vào, trả về chuỗi JSON kết quả.
    Tự động xử lý ngoại lệ và đưa về JSON error chuẩn.
    """
    if name not in TOOL_FUNCTIONS:
        return json.dumps({"error": f"Tool '{name}' không tồn tại trong hệ thống"}, ensure_ascii=False)

    func = TOOL_FUNCTIONS[name]
    kwargs = dict(arguments)

    # Inject ngữ cảnh hệ thống nếu là hàm create_purchase_request
    if name == "create_purchase_request" and context:
        if "run_id" in context and "run_id" not in kwargs:
            kwargs["run_id"] = context["run_id"]
        if "actor_id" in context and "actor_id" not in kwargs:
            kwargs["actor_id"] = context["actor_id"]

    try:
        result = func(**kwargs)
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": f"Lỗi thực thi tool '{name}': {str(e)}"}, ensure_ascii=False)

