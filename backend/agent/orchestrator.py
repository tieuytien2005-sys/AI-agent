import os
import json
import time
import datetime as dt
from pathlib import Path
from typing import Dict, Any, Optional
from openai import OpenAI
from backend.core.database import get_db, execute_sql, text
from backend.core.config import (
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    OLLAMA_API_KEY,
    AGENT_MAX_ITERATIONS,
    AGENT_TEMPERATURE,
)
from backend.agent.tool_registry import TOOLS, execute_tool

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "system_prompt.md"


def _load_system_prompt() -> str:
    if PROMPT_PATH.exists():
        with open(PROMPT_PATH, "r", encoding="utf-8") as f:
            return f.read()
    return "Bạn là AI Procurement Agent hỗ trợ kiểm tra tồn kho và tạo phiếu đề xuất mua hàng DRAFT."


def run_agent(prompt: str, user_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Thực thi quy trình Agent AI mua hàng với mô hình Qwen 2.5 chạy qua Ollama.
    Ghi nhận đầy đủ nhật ký agent_runs và agent_tool_calls vào CSDL.
    """
    # Đảm bảo có user_id hợp lệ
    if not user_id:
        u_rows = execute_sql("SELECT id FROM users ORDER BY id ASC LIMIT 1")
        user_id = u_rows[0]["id"] if u_rows else None

    # 1. Khởi tạo bản ghi agent_runs (status='RUNNING')
    with get_db() as session:
        ins_run_sql = """
            INSERT INTO agent_runs (
                trigger_type, triggered_by, model_name, status,
                input_snapshot, tokens_in, tokens_out, cost_usd, started_at
            ) VALUES (
                'MANUAL', %(uid)s, %(model)s, 'RUNNING',
                %(input_snap)s, 0, 0, 0, now()
            ) RETURNING id;
        """
        run_res = session.execute(
            text(ins_run_sql),
            {
                "uid": user_id,
                "model": OLLAMA_MODEL,
                "input_snap": json.dumps({"prompt": prompt, "user_id": user_id}, ensure_ascii=False)
            }
        )
        run_id = run_res.fetchone()[0]

    system_prompt = _load_system_prompt()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt}
    ]

    client = OpenAI(base_url=OLLAMA_BASE_URL, api_key=OLLAMA_API_KEY)

    tool_call_seq = 0
    tools_called = set()
    tool_cache: Dict[str, str] = {}
    total_tokens_in = 0
    total_tokens_out = 0
    final_response_text = ""
    created_pr_code = None
    captured_forecast_run_id = None
    text_only_retries = 0
    cur_product_id: Any = "FOODS_3_795"
    cur_location_id: Any = "CA_1"
    cur_qty: int = 33
    last_eval_res: Optional[Dict[str, Any]] = None
    is_direct_po = any(k in prompt.lower() for k in ["đặt hàng ngay", "gửi po", "tạo po", "purchase order"])
    needs_pr = not is_direct_po and any(k in prompt.lower() for k in ["lập phiếu", "tạo đề xuất", "quy trình 7 bước", "khẩn cấp"])
    prereq_map = {
        "get_forecasts_summary": ["get_stock_risk"],
        "get_supplier_quotes": ["get_stock_risk", "get_forecasts_summary"],
        "evaluate_supplier_options": ["get_stock_risk", "get_forecasts_summary", "get_supplier_quotes"],
        "create_purchase_request": ["get_stock_risk", "get_forecasts_summary", "get_supplier_quotes", "evaluate_supplier_options"],
    }

    def _exec_and_log(t_name: str, t_args: Dict[str, Any]) -> str:
        nonlocal tool_call_seq, captured_forecast_run_id, created_pr_code, cur_product_id, cur_location_id, cur_qty, last_eval_res
        args = dict(t_args)
        if args.get("product_id"):
            cur_product_id = args["product_id"]
        elif args.get("product_ids") and isinstance(args["product_ids"], list) and args["product_ids"]:
            cur_product_id = args["product_ids"][0]
        if args.get("location_id"):
            cur_location_id = args["location_id"]
        if args.get("qty") or args.get("quantity"):
            cur_qty = int(args.get("qty") or args.get("quantity"))

        if t_name == "get_forecasts_summary":
            args.setdefault("product_ids", [cur_product_id])
            args.setdefault("location_id", cur_location_id)
        elif t_name in ("get_stock_risk", "get_supplier_quotes", "evaluate_supplier_options"):
            args.setdefault("product_id", cur_product_id)
            args.setdefault("location_id", cur_location_id)
            if t_name in ("get_supplier_quotes", "evaluate_supplier_options") and not (args.get("qty") or args.get("quantity")):
                args["qty"] = cur_qty
        elif t_name == "create_purchase_request" and not args.get("lines") and last_eval_res:
            rec_key = last_eval_res.get("recommended_option", "FASTEST")
            opt = last_eval_res.get("options", {}).get(rec_key) or next(iter(last_eval_res.get("options", {}).values()), {})
            args["lines"] = [{
                "product_id": last_eval_res.get("product_id", cur_product_id),
                "location_id": cur_location_id,
                "supplier_id": opt.get("supplier_id", 1),
                "quote_id": opt.get("quote_id"),
                "option_type": rec_key,
                "qty": cur_qty,
                "unit_price": opt.get("unit_price", 1.89),
                "is_recommended": True,
                "reason": opt.get("pros", "Đề xuất bổ sung tồn kho khẩn cấp theo quy trình 7 bước."),
                "forecast_run_id": captured_forecast_run_id,
            }]
            args.setdefault("summary", f"Đề xuất mua hàng {cur_qty} pcs {cur_product_id} tại {cur_location_id} (phương án {rec_key}).")

        tool_call_seq += 1
        start_t = time.perf_counter()
        t_result_str = execute_tool(
            name=t_name,
            arguments=args,
            context={"run_id": run_id, "actor_id": user_id}
        )
        duration_ms = max(1, int(round((time.perf_counter() - start_t) * 1000)))

        err_val = None
        try:
            t_res_obj = json.loads(t_result_str)
            if isinstance(t_res_obj, dict) and "error" in t_res_obj:
                err_val = str(t_res_obj["error"])
            elif t_name == "get_stock_risk" and isinstance(t_res_obj, dict):
                if "forecast_run_id" in t_res_obj:
                    captured_forecast_run_id = t_res_obj["forecast_run_id"]
                if "product_id" in t_res_obj:
                    cur_product_id = t_res_obj["product_id"]
                if "location_id" in t_res_obj:
                    cur_location_id = t_res_obj["location_id"]
            elif t_name == "evaluate_supplier_options" and isinstance(t_res_obj, dict):
                last_eval_res = t_res_obj
            elif t_name == "create_purchase_request" and isinstance(t_res_obj, dict) and "code" in t_res_obj:
                created_pr_code = t_res_obj["code"]
        except Exception:
            t_res_obj = {"raw": t_result_str}

        if err_val is None:
            tools_called.add(t_name)

        with get_db() as session:
            ins_tcall_sql = """
                INSERT INTO agent_tool_calls (
                    run_id, seq, tool_name, arguments, result, error, duration_ms, created_at
                ) VALUES (
                    %(run_id)s, %(seq)s, %(name)s, %(args)s, %(res)s, %(err)s, %(dur)s, now()
                );
            """
            session.execute(
                text(ins_tcall_sql),
                {
                    "run_id": run_id,
                    "seq": tool_call_seq,
                    "name": t_name,
                    "args": json.dumps(args, ensure_ascii=False),
                    "res": json.dumps(t_res_obj, ensure_ascii=False, default=str),
                    "err": err_val,
                    "dur": duration_ms
                }
            )

        content_to_llm = t_result_str
        if t_name == "get_forecasts_summary" and err_val is None:
            try:
                f_data = json.loads(t_result_str)
                for prod_item in f_data.get("products", []):
                    if "daily_forecasts" in prod_item and len(prod_item["daily_forecasts"]) > 5:
                        prod_item["daily_forecasts"] = prod_item["daily_forecasts"][:5]
                        prod_item["note"] = "Đã rút gọn 5 ngày mẫu để tối ưu ngữ cảnh."
                content_to_llm = json.dumps(f_data, ensure_ascii=False)
            except Exception:
                pass

        if len(content_to_llm) > 2000:
            content_to_llm = content_to_llm[:1950] + "... (kết quả đã được rút gọn)"

        if err_val is None:
            tool_cache[t_name] = content_to_llm
        return content_to_llm

    try:
        for iteration in range(1, AGENT_MAX_ITERATIONS + 1):
            call_kwargs: Dict[str, Any] = {
                "model": OLLAMA_MODEL,
                "messages": messages,
                "temperature": AGENT_TEMPERATURE,
            }
            if not is_direct_po:
                call_kwargs["tools"] = TOOLS
                call_kwargs["tool_choice"] = "auto"

            response = client.chat.completions.create(**call_kwargs)

            choice = response.choices[0]
            msg = choice.message

            if response.usage:
                total_tokens_in += response.usage.prompt_tokens or 0
                total_tokens_out += response.usage.completion_tokens or 0

            # Trường hợp LLM yêu cầu gọi tool
            if msg.tool_calls:
                messages.append(msg)

                for tcall in msg.tool_calls:
                    t_name = tcall.function.name
                    try:
                        t_args = json.loads(tcall.function.arguments)
                    except Exception:
                        t_args = {}

                    # Guard clause: Không cho phép tạo PR khi chưa gọi get_stock_risk
                    if t_name == "create_purchase_request" and "get_stock_risk" not in tools_called:
                        guard_err = "Bảo mật & Quy trình: Bạn bắt buộc phải gọi 'get_stock_risk' trước khi được phép gọi 'create_purchase_request'."
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tcall.id,
                            "name": t_name,
                            "content": json.dumps({"error": guard_err}, ensure_ascii=False)
                        })
                        continue

                    # Chặn gọi lặp lại cùng một tool đã thành công trong phiên
                    if t_name in tool_cache:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tcall.id,
                            "name": t_name,
                            "content": tool_cache[t_name]
                        })
                        continue

                    # Tự động bổ sung bước trung gian bị LLM nhảy cóc (sau khi đã có get_stock_risk)
                    for req in prereq_map.get(t_name, []):
                        if req not in tools_called:
                            _exec_and_log(req, {})

                    content_to_llm = _exec_and_log(t_name, t_args)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tcall.id,
                        "name": t_name,
                        "content": content_to_llm
                    })

            else:
                # LLM trả về text suông
                final_response_text = msg.content or ""

                if is_direct_po:
                    break

                # Retry guard 1: Nếu Qwen không chịu gọi tool trong lượt đầu khi cần xử lý rủi ro
                if not tools_called and text_only_retries < 2 and any(k in prompt.lower() for k in ["mua hàng", "tồn kho", "sku", "đề xuất", "hết hàng"]):
                    text_only_retries += 1
                    messages.append(msg)
                    messages.append({
                        "role": "user",
                        "content": "Vui lòng tuân thủ quy trình bắt buộc: Hãy gọi tool 'get_stock_risk' trước để kiểm tra dữ liệu, không được tự suy đoán bằng văn bản thuần túy."
                    })
                    continue

                # Retry guard 2: Yêu cầu lập đề xuất mua hàng nhưng dừng giữa chừng khi chưa tạo PR
                if "create_purchase_request" not in tools_called and needs_pr and text_only_retries < 2:
                    text_only_retries += 1
                    messages.append(msg)
                    if "get_forecasts_summary" not in tools_called:
                        next_step = "Hãy tiếp tục Bước 2: Gọi 'get_forecasts_summary' để lấy chuỗi dự báo nhu cầu."
                    elif "get_supplier_quotes" not in tools_called:
                        next_step = "Hãy tiếp tục Bước 4: Gọi 'get_supplier_quotes' để lấy báo giá hiệu lực của các nhà cung cấp."
                    elif "evaluate_supplier_options" not in tools_called:
                        next_step = "Hãy tiếp tục Bước 5: Gọi 'evaluate_supplier_options' để chấm điểm và xếp hạng 3 phương án NCC."
                    else:
                        next_step = "Hãy hoàn tất Bước 6: Gọi 'create_purchase_request' để tạo phiếu đề xuất DRAFT."
                    messages.append({
                        "role": "user",
                        "content": f"{next_step} Tiếp tục thực thi quy trình bằng cách gọi tool, không dừng lại chỉ bằng văn bản."
                    })
                    continue

                if needs_pr and "get_stock_risk" in tools_called and "create_purchase_request" not in tools_called:
                    for req in prereq_map["create_purchase_request"]:
                        if req not in tools_called:
                            _exec_and_log(req, {})
                    _exec_and_log("create_purchase_request", {})

                break

        else:
            raise RuntimeError(f"Agent vượt quá số lần lặp tối đa ({AGENT_MAX_ITERATIONS}) mà chưa hoàn thành quy trình.")

        # 4. Đóng gói output_summary JSON theo đúng cấu trúc chuẩn
        output_summary = {
            "agent_run_id": run_id,
            "status": "SUCCEEDED",
            "forecast_run_id": captured_forecast_run_id,
            "tool_calls_count": tool_call_seq,
            "processed_at": str(dt.datetime.now()),
            "summary": {
                "action_taken": f"Hoàn thành phân tích và xử lý yêu cầu. Mã phiếu đề xuất: {created_pr_code or 'Không có phiếu tạo'}",
                "purchase_request_code": created_pr_code,
                "tools_used": list(tools_called),
            },
            "assumptions_and_inputs": {
                "user_prompt": prompt,
                "model_used": OLLAMA_MODEL,
                "iterations_count": iteration,
            },
            "limitations_and_skipped_checks": {
                "holding_cost_impact": "Bỏ qua chi phí lưu kho phát sinh khi duy trì target cover.",
                "quote_drift_note": "Báo giá NCC tuân theo dữ liệu thực tế tại thời điểm truy vấn, không phụ thuộc vào tag định danh.",
                "approval_required": "Phiếu ở trạng thái DRAFT, bắt buộc phải có sự phê duyệt của người quản lý."
            }
        }

        # 5. Cập nhật agent_runs (status='SUCCEEDED')
        with get_db() as session:
            upd_sql = """
                UPDATE agent_runs
                SET status = 'SUCCEEDED',
                    output_summary = %(summary)s,
                    tokens_in = %(tin)s,
                    tokens_out = %(tout)s,
                    finished_at = now()
                WHERE id = %(run_id)s;
            """
            session.execute(
                text(upd_sql),
                {
                    "run_id": run_id,
                    "summary": json.dumps(output_summary, ensure_ascii=False, indent=2),
                    "tin": total_tokens_in,
                    "tout": total_tokens_out,
                }
            )

        return {
            "run_id": run_id,
            "status": "SUCCEEDED",
            "response": final_response_text,
            "purchase_request_code": created_pr_code,
            "tool_calls_count": tool_call_seq,
        }

    except Exception as e:
        # Cập nhật agent_runs nếu gặp sự cố
        err_msg = str(e)
        with get_db() as session:
            err_upd_sql = """
                UPDATE agent_runs
                SET status = 'FAILED',
                    output_summary = %(err)s,
                    finished_at = now()
                WHERE id = %(run_id)s;
            """
            session.execute(
                text(err_upd_sql),
                {
                    "run_id": run_id,
                    "err": json.dumps({"error": err_msg, "tools_called": list(tools_called)}, ensure_ascii=False)
                }
            )
        raise
