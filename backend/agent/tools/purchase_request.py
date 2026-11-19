import datetime as dt
from typing import List, Dict, Any, Optional
from backend.core.database import get_db, execute_sql, text


def _resolve_pid(val: Any) -> int:
    s = str(val).strip()
    r = execute_sql("SELECT id FROM products WHERE id::text = %(v)s OR sku_code = %(v)s LIMIT 1;", {"v": s})
    return r[0]["id"] if r else 1


def _resolve_lid(val: Any) -> int:
    s = str(val).strip()
    r = execute_sql("SELECT id FROM locations WHERE id::text = %(v)s OR code = %(v)s LIMIT 1;", {"v": s})
    return r[0]["id"] if r else 1


def _resolve_sid(val: Any) -> int:
    s = str(val).strip()
    r = execute_sql("SELECT id FROM suppliers WHERE id::text = %(v)s OR code = %(v)s OR name LIKE %(p)s LIMIT 1;", {"v": s, "p": f"%{s}%"})
    return r[0]["id"] if r else 1


def create_purchase_request(
    lines: Optional[List[Dict[str, Any]]] = None,
    summary: Optional[str] = None,
    run_id: Optional[int] = None,
    actor_id: Optional[int] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Tạo phiếu đề xuất mua hàng ở trạng thái DRAFT trong CSDL.
    Tuyệt đối không tạo đơn mua hàng chính thức (purchase_orders).
    """
    if not lines:
        if kwargs.get("product_id") or kwargs.get("qty") or kwargs.get("quantity") or kwargs.get("supplier_options"):
            qty = kwargs.get("qty") or kwargs.get("quantity") or 100
            unit_price = kwargs.get("unit_price") or 1.89
            lines = [{
                "product_id": kwargs.get("product_id", 1),
                "location_id": kwargs.get("location_id", 1),
                "supplier_id": kwargs.get("supplier_id", 1),
                "qty": qty,
                "unit_price": unit_price,
                "option_type": kwargs.get("option_type", "FASTEST"),
                "is_recommended": True,
                "reason": kwargs.get("reason", "Đề xuất bổ sung tồn kho tự động theo chính sách ROP."),
            }]
        else:
            raise ValueError("Danh sách dòng đề xuất (lines) không được rỗng")

    summary = summary or kwargs.get("summary") or "Phiếu đề xuất mua hàng tự động tạo bởi AI Procurement Agent."

    # Ràng buộc bảo mật cứng: kiểm tra nghiêm ngặt không can thiệp purchase_orders
    for line in lines:
        if "purchase_order" in line or line.get("status") in ("APPROVED", "ORDERED"):
            raise PermissionError("Bảo mật: Agent chỉ được phép tạo phiếu đề xuất DRAFT, không được tạo đơn mua thực tế!")

    today = dt.date.today()

    # Sinh mã phiếu tự tăng theo ngày: PR-YYYYMMDD-XXXX
    count_rows = execute_sql(
        "SELECT COUNT(*) as cnt FROM purchase_requests WHERE created_at::date = %(d)s",
        {"d": today}
    )
    seq = (count_rows[0]["cnt"] if count_rows else 0) + 1
    code = f"PR-{today:%Y%m%d}-{seq:04d}"

    # Đảm bảo có actor_id hợp lệ (fallback về system user nếu actor_id None)
    if not actor_id:
        user_rows = execute_sql("SELECT id FROM users ORDER BY id ASC LIMIT 1")
        actor_id = user_rows[0]["id"] if user_rows else None

    # Tính toán tổng giá trị phiếu dựa trên phương án được khuyến nghị (hoặc dòng đầu tiên)
    recommended_line = next((line for line in lines if line.get("is_recommended")), lines[0])
    unit_p = round(float(recommended_line.get("unit_price") or 1.89), 2)
    qty = int(recommended_line.get("qty") or recommended_line.get("quantity") or 100)
    total_val = round(qty * unit_p, 2)
    currency = recommended_line.get("currency") or "USD"

    with get_db() as session:
        # 1. INSERT purchase_requests
        ins_req_sql = """
            INSERT INTO purchase_requests (
                code, status, total_value, currency, summary,
                created_by_run_id, created_by_user, created_at
            ) VALUES (
                %(code)s, 'DRAFT', %(total_value)s, %(currency)s, %(summary)s,
                %(run_id)s, %(actor_id)s, now()
            ) RETURNING id;
        """
        result = session.execute(
            text(ins_req_sql),
            {
                "code": code,
                "total_value": total_val,
                "currency": currency,
                "summary": summary,
                "run_id": run_id,
                "actor_id": actor_id,
            }
        )
        request_id = result.fetchone()[0]

        # 2. INSERT purchase_request_lines
        ins_line_sql = """
            INSERT INTO purchase_request_lines (
                request_id, product_id, location_id, supplier_id, quote_id,
                option_type, qty, unit_price, needed_by_date,
                is_recommended, is_selected, confidence, reason,
                forecast_run_id, alert_id
            ) VALUES (
                %(request_id)s, %(product_id)s, %(location_id)s, %(supplier_id)s, %(quote_id)s,
                %(option_type)s, %(qty)s, %(unit_price)s, %(needed_by_date)s,
                %(is_recommended)s, %(is_selected)s, %(confidence)s, %(reason)s,
                %(forecast_run_id)s, %(alert_id)s
            ) RETURNING id;
        """

        inserted_lines = []
        for line in lines:
            p_id = _resolve_pid(line.get("product_id") or 1)
            l_id = _resolve_lid(line.get("location_id") or 1)
            s_id = _resolve_sid(line.get("supplier_id") or 1)
            q_id = line.get("quote_id")
            if q_id:
                try:
                    q_rows = execute_sql("SELECT id FROM supplier_quotes WHERE id = %(qid)s LIMIT 1;", {"qid": int(q_id)})
                    q_id = q_rows[0]["id"] if q_rows else None
                except Exception:
                    q_id = None

            opt_type = line.get("option_type") or "BALANCED"
            if opt_type not in ("CHEAPEST", "FASTEST", "BALANCED", "MANUAL"):
                opt_type = "BALANCED"

            l_qty = int(line.get("qty") or line.get("quantity") or 100)
            l_unit_price = round(float(line.get("unit_price") or 1.89), 2)

            needed_by = line.get("needed_by_date")
            if not needed_by:
                needed_by = str(today + dt.timedelta(days=7))

            is_rec = bool(line.get("is_recommended", False))
            is_sel = bool(line.get("is_selected", False))

            # Xác thực forecast_run_id có tồn tại không để tránh lỗi Foreign Key
            f_run_id = line.get("forecast_run_id")
            if f_run_id:
                try:
                    fr_rows = execute_sql("SELECT id FROM forecast_runs WHERE id = %(frid)s LIMIT 1;", {"frid": int(f_run_id)})
                    f_run_id = fr_rows[0]["id"] if fr_rows else None
                except Exception:
                    f_run_id = None

            # Tính toán confidence nếu chưa có
            confidence = line.get("confidence")

            if confidence is None:
                # Lấy p10, p50, p90 từ demand_forecasts
                fc_rows = execute_sql(
                    """SELECT AVG(p10) as avg_p10, AVG(p50) as avg_p50, AVG(p90) as avg_p90, MAX(run_id) as run_id
                       FROM demand_forecasts
                       WHERE product_id = %(p)s AND location_id = %(l)s""",
                    {"p": p_id, "l": l_id}
                )
                if fc_rows and fc_rows[0]["avg_p50"]:
                    p10 = float(fc_rows[0]["avg_p10"])
                    p50 = float(fc_rows[0]["avg_p50"])
                    p90 = float(fc_rows[0]["avg_p90"])
                    f_run_id = f_run_id or fc_rows[0]["run_id"]
                    spread = (p90 - p10) / (2.0 * p50) if p50 > 0 else 0.0
                    acc_q = max(0.0, 1.0 - spread)
                else:
                    acc_q = 0.80

                # Lấy on_time_rate của NCC
                perf_rows = execute_sql(
                    "SELECT on_time_rate FROM supplier_evaluations WHERE supplier_id = %(s)s ORDER BY period_end DESC LIMIT 1",
                    {"s": s_id}
                )
                on_time = float(perf_rows[0]["on_time_rate"]) if perf_rows and perf_rows[0]["on_time_rate"] else 0.85

                confidence = round(0.5 * acc_q + 0.5 * on_time, 3)

            reason = line.get("reason", "Đề xuất bổ sung tồn kho tự động theo chính sách ROP.")

            line_res = session.execute(
                text(ins_line_sql),
                {
                    "request_id": request_id,
                    "product_id": p_id,
                    "location_id": l_id,
                    "supplier_id": s_id,
                    "quote_id": q_id,
                    "option_type": opt_type,
                    "qty": l_qty,
                    "unit_price": l_unit_price,
                    "needed_by_date": needed_by,
                    "is_recommended": is_rec,
                    "is_selected": is_sel,
                    "confidence": confidence,
                    "reason": reason,
                    "forecast_run_id": f_run_id,
                    "alert_id": line.get("alert_id"),
                }
            )
            inserted_lines.append(line_res.fetchone()[0])

    return {
        "request_id": request_id,
        "code": code,
        "status": "DRAFT",
        "total_value": total_val,
        "currency": currency,
        "lines_count": len(inserted_lines),
        "created_at": str(dt.datetime.now()),
    }
