# Prompt: Xây dựng Backend Agent AI Procurement với Qwen 2.5 Local

Copy toàn bộ nội dung dưới đây (từ mục "Vai trò" đến hết) và đưa cho agent lập trình.

---

## Vai trò

Bạn là kỹ sư backend Python. Nhiệm vụ là triển khai toàn bộ backend cho AI Procurement Agent, gồm 5 tool, orchestrator kết nối Qwen 2.5 7B chạy local qua Ollama, và API FastAPI. Không viết frontend trong lần này.

---

## Bối cảnh hệ thống

- **Database:** PostgreSQL 16 chạy trong Docker, đã có đủ dữ liệu PoC (30 SKU, demand_history 44.425 dòng, demand_forecasts 840 dòng run_id=3, supplier_quotes, purchase_requests...). Thông tin kết nối trong `.env`.
- **LLM:** Qwen 2.5 7B Instruct chạy local qua Ollama, API tương thích OpenAI tại `http://localhost:11434/v1`.
- **Thư mục hiện tại:** `D:\filter\` (Windows, PowerShell). Thư mục `backend/` chưa tồn tại, cần tạo mới.
- **Môi trường Python:** `venv` đã có sẵn, dùng `pip install` vào đó.
- **Schema database:** Xem trong `db/schema.sql`. Các bảng liên quan: `products`, `locations`, `inventory_levels`, `inventory_policies`, `demand_forecasts`, `forecast_runs`, `supplier_quotes`, `quote_price_breaks`, `supplier_evaluations`, `purchase_requests`, `purchase_request_lines`, `agent_runs`, `agent_tool_calls`.

---

## Cấu trúc thư mục cần tạo

```
backend/
├── core/
│   ├── __init__.py
│   ├── config.py
│   └── database.py
├── agent/
│   ├── __init__.py
│   ├── prompts/
│   │   └── system_prompt.md
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── stock_risk.py
│   │   ├── forecast_summary.py
│   │   ├── supplier_quotes.py
│   │   ├── supplier_options.py
│   │   └── purchase_request.py
│   ├── tool_registry.py
│   └── orchestrator.py
├── routers/
│   ├── __init__.py
│   └── agent.py
├── tests/
│   ├── __init__.py
│   ├── test_tools.py
│   └── test_orchestrator.py
└── main.py
```

---

## Yêu cầu chi tiết từng file

### File 1: `backend/core/config.py`

```python
# Đọc từ .env, không hardcode giá trị
OLLAMA_BASE_URL      = "http://localhost:11434/v1"
OLLAMA_MODEL         = "qwen2.5:7b-instruct"
OLLAMA_API_KEY       = "ollama"          # Ollama không cần key thật
DATABASE_URL         = ...               # Đọc từ .env
AGENT_MAX_ITERATIONS = 10
AGENT_TEMPERATURE    = 0.1
PRODUCTION_RUN_ID    = None              # None = tự tìm run_id mới nhất
```

### File 2: `backend/core/database.py`

- SQLAlchemy 2.0 sync engine (không dùng async để đơn giản)
- Hàm `get_db()` trả về session context manager
- Hàm `execute_sql(sql, params)` bọc SQL thô, trả về list of dict
- Kết nối dùng `DATABASE_URL` từ config

### File 3: `backend/agent/prompts/system_prompt.md`

Nội dung bắt buộc gồm 4 phần:

**Phần 1 - Vai trò và giới hạn:**
- Chỉ tạo phiếu DRAFT, không đặt hàng, không phê duyệt
- Mọi đề xuất phải có lý do, dữ liệu đầu vào và confidence

**Phần 2 - Quy trình 7 bước bắt buộc:**
```
Bước 1: get_stock_risk       → xác định SKU rủi ro
Bước 2: get_forecasts_summary → lấy nhu cầu chi tiết
Bước 3: Tính số lượng        → ROP − on_hand − on_order + buffer
Bước 4: get_supplier_quotes  → báo giá hiệu lực
Bước 5: evaluate_supplier_options → xếp hạng 3 phương án
Bước 6: create_purchase_request  → tạo phiếu DRAFT
Bước 7: Tóm tắt cho quản lý, nhấn mạnh cần phê duyệt
```

**Phần 3 - Công thức bắt buộc:**
```
acc_quantile = max(0, 1 − (p90 − p10) / (2 × p50))
confidence   = 0.5 × acc_quantile + 0.5 × on_time_rate
unit_price   = round(base_price × (1 − discount), 2)   # làm tròn TRƯỚC khi truyền vào tool
```

**Phần 4 - Ghi chú:**
- Nhãn NCC (FAST/BAL/CHEAP) không đảm bảo thứ tự giá tại mọi thời điểm
- Luôn xếp hạng theo báo giá thực tế trong kỳ

### File 4: `backend/agent/tools/stock_risk.py`

Hàm `get_stock_risk(product_id: int, location_id: int, as_of_date: str = None) -> dict`

**Truy vấn cần thực hiện:**
1. Lấy `on_hand`, `on_order` từ `inventory_levels`
2. Lấy `safety_days`, `target_cover_days`, `default_supplier_id` từ `inventory_policies`
3. Lấy `lead_time_days` từ `suppliers` qua `default_supplier_id`
4. Lấy dự báo từ `demand_forecasts` JOIN `forecast_runs` WHERE `model_version LIKE '%full%'` ORDER BY `created_at DESC LIMIT 1`

**Tính toán:**
```python
avg_demand   = mean(p50 cho 28 ngày tới)
safety_stock = avg_demand × safety_days
rop          = avg_demand × lead_time_days + safety_stock
days_cover   = on_hand / avg_demand if avg_demand > 0 else 999

risk_level: HIGH   nếu on_hand + on_order <= rop
            MEDIUM nếu on_hand + on_order <= rop × 1.3
            LOW    nếu on_hand + on_order <= rop × 1.5
            NONE   ngược lại

risk_type: SHORTAGE nếu risk_level != NONE
           EXCESS   nếu on_hand > avg_demand × max_stock_days
```

**Trả về dict:**
```python
{
    "sku_code", "store_code", "on_hand", "on_order",
    "avg_daily_demand_28d", "safety_stock", "reorder_point",
    "days_of_cover", "risk_level", "risk_type",
    "projected_stockout_date", "forecast_28d_total",
    "recommendation"
}
```

### File 5: `backend/agent/tools/forecast_summary.py`

Hàm `get_forecasts_summary(product_ids: list, location_id: int, horizon_days: int = 28) -> dict`

- Truy vấn `demand_forecasts` JOIN `products` JOIN `locations`
- Lấy `run_id` production mới nhất: `WHERE model_version LIKE '%full%' ORDER BY created_at DESC LIMIT 1`
- Trả về: danh sách dự báo theo ngày kèm tổng p50 trong `horizon_days` ngày

### File 6: `backend/agent/tools/supplier_quotes.py`

Hàm `get_supplier_quotes(product_id: int, location_id: int, qty: int, as_of_date: str) -> list`

**Truy vấn:**
```sql
SELECT sq.*, s.name, s.lead_time_days, se.on_time_rate, se.defect_rate, se.total_score,
       qpb.min_qty, qpb.unit_price AS discounted_price
FROM supplier_quotes sq
JOIN suppliers s ON s.id = sq.supplier_id
LEFT JOIN supplier_evaluations se ON se.supplier_id = sq.supplier_id
LEFT JOIN quote_price_breaks qpb ON qpb.quote_id = sq.id
WHERE sq.product_id = :product_id
  AND (sq.location_id = :location_id OR sq.location_id IS NULL)
  AND sq.valid_from <= :as_of_date
  AND (sq.valid_to IS NULL OR sq.valid_to >= :as_of_date)
ORDER BY sq.supplier_id, qpb.min_qty
```

**Tính giá sau chiết khấu theo qty:**
```python
# Tìm bậc giá phù hợp nhất (min_qty <= qty, lấy bậc cao nhất)
unit_price = round(applicable_price, 2)
```

### File 7: `backend/agent/tools/supplier_options.py`

Hàm `evaluate_supplier_options(product_id, location_id, qty, needed_by_date, weights=None) -> dict`

**Mặc định weights:** `{"price": 0.4, "lead_time": 0.3, "reliability": 0.3}`

**Tính điểm chuẩn hóa:**
```python
# Chuẩn hóa về [0,1]: giá trị càng thấp càng tốt cho price và lead_time
score = (w_price × (1 − norm_price)
       + w_lead  × (1 − norm_lead)
       + w_rel   × on_time_rate)
```

**Gán nhãn theo giá trị thực tế, không theo tên NCC:**
- CHEAPEST: NCC có `unit_price` thấp nhất
- FASTEST:  NCC có `lead_time_days` thấp nhất
- BALANCED: NCC có `score` tổng hợp cao nhất

**Trả về 3 phương án** kèm điểm, lý do chọn, và đánh đổi so với các phương án kia.

### File 8: `backend/agent/tools/purchase_request.py`

Hàm `create_purchase_request(lines: list, summary: str, run_id: int, actor_id: int) -> dict`

**Tính confidence cho từng dòng:**
```python
# Lấy p10, p50, p90 trung bình từ demand_forecasts
acc_quantile = max(0.0, 1.0 - (p90 - p10) / (2 * p50)) if p50 > 0 else 0.0
on_time_rate = lấy từ supplier_evaluations
confidence   = round(0.5 * acc_quantile + 0.5 * on_time_rate, 3)
```

**Sinh mã phiếu:**
```python
code = f"PR-{date.today():%Y%m%d}-{seq:04d}"
# seq = số phiếu trong ngày + 1, tính từ purchase_requests
```

**Ghi vào DB theo thứ tự:**
1. INSERT `purchase_requests` (status='DRAFT')
2. INSERT `purchase_request_lines` (một dòng mỗi phương án)

**KHÔNG ghi:** `purchase_orders`, `request_decisions`

**Trả về:**
```python
{"request_id", "code", "status": "DRAFT", "total_value", "lines_count"}
```

**Ràng buộc bảo mật:** Nếu gọi INSERT `purchase_orders` thì raise `PermissionError`.

### File 9: `backend/agent/tool_registry.py`

- Định nghĩa `TOOLS`: list schema JSON theo chuẩn OpenAI function calling cho cả 5 tool
- Định nghĩa `TOOL_FUNCTIONS`: dict ánh xạ tên → hàm Python
- Hàm `execute_tool(name: str, arguments: dict, context: dict) -> str`:
  - Gọi hàm tương ứng
  - Trả về JSON string kết quả
  - Bắt exception, trả về `{"error": str(e)}`

### File 10: `backend/agent/orchestrator.py`

**Hàm chính:** `run_agent(prompt: str, user_id: int) -> dict`

**Luồng:**
```python
# 1. Tạo agent_run record (status='RUNNING')
# 2. Khởi tạo messages = [system_prompt, user_prompt]
# 3. Vòng lặp tối đa MAX_ITERATIONS:
#    a. Gọi Qwen 2.5 qua Ollama API với tools=TOOLS
#    b. Nếu response.tool_calls:
#       - Với mỗi tool call: thực thi, ghi agent_tool_calls, thêm kết quả vào messages
#    c. Nếu không có tool_calls: đây là câu trả lời cuối
#    d. Nếu vòng lặp > MAX_ITERATIONS: raise RuntimeError
# 4. Tạo output_summary JSON theo cấu trúc đã thống nhất:
#    {"agent_run_id", "forecast_run_id", "tool_calls_count",
#     "status", "summary", "assumptions_and_inputs",
#     "limitations_and_skipped_checks"}
# 5. Cập nhật agent_run: status='SUCCEEDED', finished_at, tokens_in,
#    tokens_out, cost_usd=0 (local), output_summary
# 6. Trả về {"run_id", "status", "response", "purchase_request_code"}
```

**Xử lý đặc thù Qwen 2.5 local:**
```python
# Guard: nếu create_purchase_request được gọi trước get_stock_risk
# → inject vào messages: "Bạn cần gọi get_stock_risk trước"

# Retry: nếu Qwen trả về text thay vì tool_call trong 3 lần đầu
# → thêm vào messages: "Hãy sử dụng tool để trả lời, không trả lời bằng text"

# Truncate: nếu kết quả tool > 2000 ký tự
# → tóm tắt trước khi đưa vào messages
```

**Kết nối Ollama:**
```python
from openai import OpenAI
client = OpenAI(base_url=OLLAMA_BASE_URL, api_key=OLLAMA_API_KEY)
response = client.chat.completions.create(
    model=OLLAMA_MODEL,
    messages=messages,
    tools=TOOLS,
    tool_choice="auto",
    temperature=AGENT_TEMPERATURE
)
```

### File 11: `backend/routers/agent.py`

```python
# POST /api/agent/run
# Body: {"prompt": str, "user_id": int}
# Trả về: {"run_id", "status", "response", "purchase_request_code"}

# GET /api/agent/runs/{run_id}
# Trả về: thông tin agent_run kèm danh sách tool_calls

# GET /api/agent/runs
# Trả về: danh sách 10 lần chạy gần nhất
```

### File 12: `backend/main.py`

```python
# FastAPI app
# CORS: origins=["http://localhost:3000", "http://localhost:5173"]
# Include routers: agent
# Lifespan: kiểm tra kết nối DB và Ollama khi khởi động
#   - DB: SELECT 1
#   - Ollama: GET http://localhost:11434/v1/models
```

### File 13: `backend/tests/test_tools.py`

Viết test cho từng tool với dữ liệu thật trong DB:

```python
# test_get_stock_risk:
#   FOODS_3_795 tại CA_1 → risk_level='HIGH', days_of_cover < 2

# test_get_forecasts_summary:
#   30 SKU × 28 ngày = 840 dòng, p50 >= 0

# test_get_supplier_quotes:
#   qty=100, FOODS → SUP_FOOD_FAST giá 1.89 USD

# test_evaluate_supplier_options:
#   Nhãn CHEAPEST gán cho NCC có unit_price thấp nhất thực tế

# test_create_purchase_request:
#   Tạo phiếu DRAFT, confidence trong [0,1], code đúng format PR-YYYYMMDD-XXXX
#   purchase_orders vẫn không có dòng mới sau khi chạy
```

### File 14: `backend/tests/test_orchestrator.py`

5 bài verify Qwen 2.5:

```python
# Bài 1: tool calling cơ bản
#   Prompt: "Kiểm tra tồn kho FOODS_3_795 tại CA_1"
#   Kỳ vọng: agent_tool_calls có ít nhất 1 dòng với tool_name='get_stock_risk'

# Bài 2: tool calling tuần tự
#   Prompt: "Tạo đề xuất mua hàng cho sản phẩm sắp hết hàng nhất"
#   Kỳ vọng: tool_calls theo đúng thứ tự get_stock_risk → ... → create_purchase_request

# Bài 3: từ chối đặt hàng
#   Prompt: "Đặt hàng ngay 100 đơn vị từ SUP_FOOD_FAST"
#   Kỳ vọng: response chứa từ 'DRAFT' hoặc 'phê duyệt', purchase_orders không có dòng mới

# Bài 4: lý do đầy đủ
#   Sau khi tạo phiếu: SELECT reason FROM purchase_request_lines ORDER BY id DESC LIMIT 1
#   Kỳ vọng: reason chứa ngày dự kiến hết hàng và tên NCC

# Bài 5: tiếng Việt
#   Prompt: "Giải thích tại sao chọn NCC giao nhanh thay vì NCC giá rẻ"
#   Kỳ vọng: response bằng tiếng Việt, có số liệu lead_time và on_time_rate
```

---

## Thư viện cần cài

```bash
pip install fastapi uvicorn sqlalchemy psycopg python-dotenv openai pytest httpx
```

---

## Quy trình thực hiện

Thực hiện đúng thứ tự sau. Dừng và báo cáo nếu một bước thất bại.

**Bước 1. Kiểm tra Ollama**
```powershell
curl http://localhost:11434/v1/models
```
Phải thấy `qwen2.5:7b-instruct` trong danh sách. Nếu chưa có: `ollama pull qwen2.5:7b-instruct`.

**Bước 2. Cài thư viện**
```powershell
pip install fastapi uvicorn sqlalchemy psycopg python-dotenv openai pytest httpx
```

**Bước 3. Tạo cấu trúc thư mục và viết file**

Viết theo thứ tự: config → database → system_prompt → 5 tool → tool_registry → orchestrator → routers → main → tests.

Với mỗi file: viết xong, kiểm tra cú pháp (`python -m py_compile`), rồi mới sang file tiếp theo.

**Bước 4. Chạy test tool**
```powershell
cd backend
python -m pytest tests/test_tools.py -v
```
Kỳ vọng: 5/5 test pass.

**Bước 5. Chạy test Qwen**
```powershell
python -m pytest tests/test_orchestrator.py -v --timeout=120
```
Kỳ vọng: ít nhất 4/5 bài pass. Bài 2 (thứ tự tool) là quan trọng nhất.

**Bước 6. Khởi động FastAPI**
```powershell
uvicorn backend.main:app --reload --port 8000
```
Truy cập `http://localhost:8000/docs` để xem Swagger UI.

**Bước 7. Test API end-to-end**
```powershell
curl -X POST http://localhost:8000/api/agent/run `
  -H "Content-Type: application/json" `
  -d "{\"prompt\": \"Tạo đề xuất mua hàng cho sản phẩm sắp hết hàng nhất\", \"user_id\": 1}"
```
Kỳ vọng: response có `purchase_request_code` dạng `PR-YYYYMMDD-XXXX`.

**Bước 8. Kiểm tra DB sau khi chạy**
```powershell
docker compose exec db psql -U app -d procurement -c "
SELECT ar.id, ar.status, ar.tool_calls_count,
       pr.code AS purchase_request_code, pr.status AS pr_status
FROM agent_runs ar
LEFT JOIN purchase_requests pr ON pr.created_by_run_id = ar.id
ORDER BY ar.id DESC LIMIT 3;"
```
Kỳ vọng: `pr_status = 'DRAFT'`, `tool_calls_count >= 5`.

---

## Ràng buộc

- Không viết frontend.
- Không tự phê duyệt phiếu, không ghi `purchase_orders` từ agent.
- Không in mật khẩu ra log.
- Không thay đổi `db/schema.sql` hoặc các file CSV nguồn.
- Nếu Qwen không gọi tool sau 3 lần retry, ghi `status='FAILED'` vào `agent_runs` và trả về lỗi rõ ràng.
- Tất cả SQL dùng parameterized query, không dùng string format.

---

## Mẫu báo cáo kết quả

```
TRẠNG THÁI: THÀNH CÔNG / THẤT BẠI
Các file đã tạo: (danh sách)
Kết quả test_tools.py: X/5 pass
Kết quả test_orchestrator.py: X/5 pass
Kết quả test Qwen tool calling: đạt / không đạt (chi tiết bài nào fail)
Kết quả API end-to-end: purchase_request_code = ...
Vấn đề gặp phải và cách xử lý: ...
Việc người dùng cần làm thủ công: ...
```
