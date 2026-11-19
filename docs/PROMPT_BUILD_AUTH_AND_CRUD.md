# Prompt: Xây dựng API Auth và CRUD nghiệp vụ cho AI Procurement Agent

Copy toàn bộ nội dung dưới đây (từ mục "Vai trò" đến hết) và đưa cho agent lập trình.

---

## Vai trò

Bạn là kỹ sư backend Python. Nhiệm vụ là bổ sung API Authentication và CRUD nghiệp vụ vào backend FastAPI đã có. Không viết frontend, không thay đổi schema database, không sửa code Agent đã hoạt động.

---

## Bối cảnh hệ thống

- **Backend hiện có:** FastAPI với 3 endpoint Agent tại `backend/routers/agent.py`, `backend/agent/`, `backend/core/config.py`, `backend/core/database.py`.
- **Database:** PostgreSQL 16 trong Docker. Bảng `users` đã có với cột: `id`, `email`, `full_name`, `password_hash`, `role` (ADMIN/PURCHASER/MANAGER/VIEWER), `is_active`, `created_at`.
- **Dữ liệu hiện tại:** 1 user `system@poc.local` (ADMIN, is_active=FALSE). Cần tạo thêm user thật để test.
- **Schema:** Xem `db/schema.sql`. Các bảng liên quan nhóm 2: `purchase_requests`, `purchase_request_lines`, `request_decisions`, `stock_alerts`, `forecast_runs`, `demand_forecasts`, `suppliers`, `supplier_quotes`, `supplier_evaluations`, `inventory_levels`, `inventory_policies`.
- **Môi trường:** Windows, PowerShell, venv đã có.

---

## Cấu trúc file cần tạo hoặc sửa

```
backend/
├── core/
│   ├── config.py          SỬA: thêm JWT_SECRET_KEY, JWT_EXPIRE_HOURS
│   └── security.py        TẠO MỚI
├── routers/
│   ├── auth.py            TẠO MỚI
│   ├── requests.py        TẠO MỚI
│   ├── alerts.py          TẠO MỚI
│   ├── forecasts.py       TẠO MỚI
│   ├── suppliers.py       TẠO MỚI
│   └── agent.py           SỬA: thêm auth dependency
├── tests/
│   ├── test_auth.py       TẠO MỚI
│   └── test_crud.py       TẠO MỚI
└── main.py                SỬA: include routers mới
```

---

## Nhóm 1: Authentication

### File 1: `backend/core/security.py`

```python
# Các hàm cần implement:

def hash_password(plain: str) -> str:
    # Dùng passlib với bcrypt

def verify_password(plain: str, hashed: str) -> bool:
    # So sánh plain với hash

def create_access_token(user_id: int, role: str) -> str:
    # JWT payload: {"sub": str(user_id), "role": role, "exp": now + JWT_EXPIRE_HOURS}
    # Dùng python-jose

def decode_token(token: str) -> dict:
    # Giải mã JWT, raise 401 nếu hết hạn hoặc không hợp lệ

# FastAPI Dependencies:
async def get_current_user(
    token: str = Depends(OAuth2PasswordBearer(tokenUrl="/api/auth/login"))
) -> dict:
    # Giải mã token, truy vấn DB lấy user, raise 401 nếu không tìm thấy hoặc is_active=False
    # Trả về {"id", "email", "role", "full_name"}

def require_role(*roles: str):
    # Factory tạo dependency kiểm tra role
    # Raise 403 nếu user.role không nằm trong roles
    # Dùng: Depends(require_role("MANAGER", "ADMIN"))
```

### File 2: `backend/routers/auth.py`

**Endpoint 1: `POST /api/auth/login`**
```
Body: {"username": str (email), "password": str}
      Dùng OAuth2PasswordRequestForm để tương thích Swagger UI
Response: {
    "access_token": str,
    "token_type": "bearer",
    "user": {"id", "email", "full_name", "role"}
}
Logic:
  1. Tìm user theo email
  2. verify_password
  3. Kiểm tra is_active=True
  4. Tạo và trả về JWT
Lỗi: 401 nếu sai email/password, 403 nếu is_active=False
```

**Endpoint 2: `POST /api/auth/register`**
```
Body: {"email": str, "full_name": str, "password": str, "role": str}
Quyền: ADMIN only
Response: {"id", "email", "full_name", "role", "created_at"}
Logic:
  1. Kiểm tra email chưa tồn tại (409 nếu trùng)
  2. hash_password
  3. INSERT INTO users
Lưu ý: role phải là một trong ADMIN/PURCHASER/MANAGER/VIEWER
```

**Endpoint 3: `GET /api/auth/me`**
```
Quyền: Đã đăng nhập
Response: {"id", "email", "full_name", "role", "created_at"}
```

**Endpoint 4: `PUT /api/auth/me/password`**
```
Body: {"current_password": str, "new_password": str}
Quyền: Đã đăng nhập
Logic:
  1. verify_password với current_password
  2. hash_password với new_password
  3. UPDATE users SET password_hash
```

### Sửa `backend/core/config.py`

Thêm vào:
```python
JWT_SECRET_KEY   = os.getenv("JWT_SECRET_KEY", "dev_secret_change_in_prod")
JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "8"))
```

Thêm vào `.env.example`:
```
JWT_SECRET_KEY=your_secret_key_here
JWT_EXPIRE_HOURS=8
```

### Sửa `backend/routers/agent.py`

Thêm `get_current_user` vào tất cả endpoint. Lấy `user_id` từ token thay vì từ body:
```python
# Trước:
async def run_agent(body: RunRequest):
    result = await orchestrator.run(body.prompt, body.user_id)

# Sau:
async def run_agent(body: RunRequest,
                    current_user: dict = Depends(get_current_user)):
    result = await orchestrator.run(body.prompt, current_user["id"])
```

---

## Nhóm 2: CRUD nghiệp vụ

### File 3: `backend/routers/requests.py`

**Endpoint 1: `GET /api/requests`**
```
Query params: status (optional), limit=20, offset=0
Quyền: Đã đăng nhập
Response: {
    "total": int,
    "items": [{
        "id", "code", "status", "total_value", "currency",
        "summary", "created_by_run_id", "created_at",
        "submitted_at", "decided_at",
        "lines_count": int,      ← đếm từ purchase_request_lines
        "confidence_avg": float  ← trung bình confidence của các dòng
    }]
}
```

**Endpoint 2: `GET /api/requests/{id}`**
```
Quyền: Đã đăng nhập
Response: {
    ...request fields...,
    "lines": [{
        "id", "product_id", "sku_code", "store_code",
        "supplier_id", "supplier_name", "option_type",
        "qty", "unit_price", "line_value",
        "needed_by_date", "is_recommended", "is_selected",
        "confidence", "reason",
        "supplier_on_time_rate",    ← join supplier_evaluations
        "supplier_defect_rate",
        "supplier_lead_time"
    }],
    "decisions": [{
        "id", "actor_id", "actor_email", "action",
        "reason", "created_at"
    }]
}
```

**Endpoint 3: `POST /api/requests/{id}/submit`**
```
Quyền: PURCHASER, MANAGER, ADMIN
Logic:
  1. Kiểm tra status == 'DRAFT' (422 nếu không)
  2. UPDATE status = 'PENDING_APPROVAL', submitted_at = now()
  3. INSERT request_decisions (action='SUBMIT', snapshot=current_state)
Response: {"id", "code", "status", "submitted_at"}
```

**Endpoint 4: `POST /api/requests/{id}/approve`**
```
Body: {"reason": str (optional)}
Quyền: MANAGER, ADMIN
Logic:
  1. Kiểm tra status == 'PENDING_APPROVAL' (422 nếu không)
  2. UPDATE status = 'APPROVED', decided_at = now(), decided_by = user.id
  3. INSERT request_decisions:
     - action = 'APPROVE'
     - actor_id = current_user.id
     - reason = body.reason
     - snapshot = JSON toàn bộ phiếu và các dòng tại thời điểm đó
  4. KHÔNG tạo purchase_orders
Response: {"id", "code", "status", "decided_at"}
```

**Endpoint 5: `POST /api/requests/{id}/reject`**
```
Body: {"reason": str} ← BẮT BUỘC, 422 nếu thiếu hoặc rỗng
Quyền: MANAGER, ADMIN
Logic:
  1. Kiểm tra status == 'PENDING_APPROVAL'
  2. UPDATE status = 'REJECTED', decided_at = now(), decided_by = user.id
  3. INSERT request_decisions (action='REJECT', reason=body.reason, snapshot=...)
Response: {"id", "code", "status", "decided_at"}
```

**Endpoint 6: `POST /api/requests/{id}/revise`**
```
Body: {"reason": str} ← BẮT BUỘC
Quyền: MANAGER, ADMIN
Logic:
  1. Kiểm tra status == 'PENDING_APPROVAL'
  2. UPDATE status = 'DRAFT' (trả về DRAFT để Agent hoặc người dùng sửa)
  3. INSERT request_decisions (action='REQUEST_CHANGES', reason=body.reason, snapshot=...)
Response: {"id", "code", "status"}
```

**Endpoint 7: `GET /api/requests/{id}/decisions`**
```
Quyền: Đã đăng nhập
Response: [{
    "id", "action", "reason",
    "actor_email", "actor_role",
    "created_at", "snapshot"
}] ← sắp xếp theo created_at ASC
```

**Luồng trạng thái hợp lệ:**
```
DRAFT → PENDING_APPROVAL (submit)
PENDING_APPROVAL → APPROVED   (approve)
PENDING_APPROVAL → REJECTED   (reject)
PENDING_APPROVAL → DRAFT      (revise)
CANCELLED có thể từ bất kỳ trạng thái nào
```

Kiểm tra luồng trong code: nếu gọi sai trạng thái thì trả về 422 với message rõ ràng.

### File 4: `backend/routers/alerts.py`

**Endpoint 1: `GET /api/alerts`**
```
Query params: status (OPEN/IN_REVIEW/RESOLVED/DISMISSED), severity, limit=50
Quyền: Đã đăng nhập
Response: {
    "total": int,
    "items": [{
        "id", "product_id", "sku_code", "store_code",
        "alert_type", "severity", "status",
        "projected_date", "detected_at",
        "details": {
            "on_hand", "reorder_point",
            "days_of_cover", "avg_daily_demand"
        }
    }]
}
```

**Endpoint 2: `GET /api/alerts/{id}`**
```
Quyền: Đã đăng nhập
Response: alert đầy đủ kèm dự báo p10/p50/p90 cho SKU đó
```

**Endpoint 3: `POST /api/alerts/scan`**
```
Quyền: PURCHASER, MANAGER, ADMIN
Logic:
  1. Lấy tất cả cặp (product_id, location_id) từ inventory_levels
  2. Gọi get_stock_risk() cho từng cặp (import từ agent/tools/stock_risk.py)
  3. Với mỗi SKU có risk_level != 'NONE':
     a. Kiểm tra đã có alert OPEN cho SKU này chưa
     b. Nếu chưa: INSERT stock_alerts
     c. Nếu rồi: UPDATE details và severity
  4. Trả về tổng kết
Response: {
    "scanned": 30,
    "new_alerts": int,
    "updated_alerts": int,
    "alerts_by_severity": {"HIGH": n, "MEDIUM": n, "LOW": n}
}
```

**Endpoint 4: `PUT /api/alerts/{id}/dismiss`**
```
Body: {"reason": str (optional)}
Quyền: MANAGER, ADMIN
Logic: UPDATE stock_alerts SET status = 'DISMISSED'
```

### File 5: `backend/routers/forecasts.py`

**Endpoint 1: `GET /api/forecasts`**
```
Quyền: Đã đăng nhập
Response: [{
    "id", "model_name", "model_version",
    "trained_until", "horizon_days",
    "metrics": {wape_overall, rmsse_overall, ...},
    "created_at"
}] ← sắp xếp theo created_at DESC
```

**Endpoint 2: `GET /api/forecasts/latest/summary`**
```
Quyền: Đã đăng nhập
Logic: Lấy run mới nhất có model_version LIKE '%full%'
Response: {
    "run_id", "model_name", "trained_until",
    "wape_overall", "rmsse_overall",
    "forecast_period": {"from": "2016-05-23", "to": "2016-06-19"},
    "skus_covered": 30,
    "total_rows": 840
}
```

**Endpoint 3: `GET /api/forecasts/{run_id}/skus`**
```
Query params: product_id (optional), location_id (optional)
Quyền: Đã đăng nhập
Response: [{
    "sku_code", "store_code",
    "forecasts": [{"date", "p10", "p50", "p90"}],
    "total_p50_28d": float
}]
```

### File 6: `backend/routers/suppliers.py`

**Endpoint 1: `GET /api/suppliers`**
```
Query params: category (optional), is_active (optional)
Quyền: Đã đăng nhập
Response: [{
    "id", "code", "name", "category",
    "lead_time_days", "moq", "payment_terms_days",
    "latest_score": float,     ← từ supplier_evaluations mới nhất
    "on_time_rate": float,
    "defect_rate": float
}]
```

**Endpoint 2: `GET /api/suppliers/{id}`**
```
Quyền: Đã đăng nhập
Response: supplier đầy đủ kèm lịch sử evaluations
```

**Endpoint 3: `GET /api/suppliers/{id}/quotes`**
```
Query params: product_id (optional), as_of_date (optional, default hôm nay)
Quyền: Đã đăng nhập
Response: [{
    "sku_code", "store_code",
    "base_unit_price", "currency",
    "lead_time_days", "moq",
    "valid_from", "valid_to",
    "price_breaks": [{"min_qty", "unit_price"}]
}]
```

---

## Quy tắc chung cho tất cả endpoint

**Xử lý lỗi nhất quán:**
```python
# 400: Request không hợp lệ
# 401: Chưa đăng nhập hoặc token hết hạn
# 403: Không đủ quyền
# 404: Không tìm thấy resource
# 409: Conflict (email trùng, trạng thái không hợp lệ)
# 422: Validation error (thiếu reason khi reject...)
# 500: Lỗi server
```

**Response format nhất quán:**
```python
# Thành công:
{"data": ..., "message": "..."}

# Lỗi:
{"detail": "...", "code": "..."}
```

**SQL dùng parameterized query:**
```python
# Đúng:
execute_sql("SELECT * FROM users WHERE email = %s", {"email": email})
# Sai:
execute_sql(f"SELECT * FROM users WHERE email = '{email}'")
```

**Snapshot cho request_decisions:**
```python
snapshot = {
    "request_id": req["id"],
    "code": req["code"],
    "status_before": req["status"],
    "total_value": req["total_value"],
    "lines": [
        {
            "product_id": l["product_id"],
            "sku_code": l["sku_code"],
            "supplier_id": l["supplier_id"],
            "qty": l["qty"],
            "unit_price": l["unit_price"],
            "option_type": l["option_type"]
        }
        for l in lines
    ],
    "timestamp": datetime.utcnow().isoformat()
}
```

---

## Thư viện cần cài thêm

```bash
pip install python-jose[cryptography] passlib[bcrypt]
```

---

## Quy trình thực hiện

Thực hiện đúng thứ tự. Dừng và báo cáo nếu một bước thất bại.

**Bước 1. Tạo user test trước khi viết code**
```powershell
docker compose exec db psql -U app -d procurement -c "
INSERT INTO users (email, full_name, password_hash, role, is_active)
VALUES
  ('admin@poc.local',    'Admin',           'TEMP', 'ADMIN',     TRUE),
  ('manager@poc.local',  'Manager',         'TEMP', 'MANAGER',   TRUE),
  ('purchaser@poc.local','Purchaser',       'TEMP', 'PURCHASER', TRUE),
  ('viewer@poc.local',   'Viewer',          'TEMP', 'VIEWER',    TRUE)
ON CONFLICT (email) DO NOTHING;"
```

**Bước 2. Cài thư viện**
```powershell
pip install python-jose[cryptography] passlib[bcrypt]
```

**Bước 3. Viết security.py và kiểm tra cú pháp**
```powershell
python -m py_compile backend/core/security.py && echo "OK"
```

**Bước 4. Viết auth.py và test đăng nhập**

Sau khi viết xong, cập nhật password_hash trong DB:
```powershell
# Chạy script nhỏ để hash mật khẩu và cập nhật DB
python -c "
from backend.core.security import hash_password
from backend.core.database import execute_sql
for email, pw in [
    ('admin@poc.local',    'admin123'),
    ('manager@poc.local',  'manager123'),
    ('purchaser@poc.local','purchaser123'),
    ('viewer@poc.local',   'viewer123'),
]:
    h = hash_password(pw)
    execute_sql('UPDATE users SET password_hash = %s WHERE email = %s',
                {'h': h, 'e': email})
print('Done')
"
```

**Bước 5. Khởi động server và test auth**
```powershell
uvicorn backend.main:app --reload --port 8000
```

Test đăng nhập:
```powershell
curl -X POST http://localhost:8000/api/auth/login `
  -H "Content-Type: application/x-www-form-urlencoded" `
  -d "username=manager@poc.local&password=manager123"
```
Kỳ vọng: response có `access_token`.

**Bước 6. Viết 5 router CRUD và include vào main.py**

Thứ tự: requests.py → alerts.py → forecasts.py → suppliers.py

**Bước 7. Chạy test tự động**
```powershell
python -m pytest backend/tests/test_auth.py -v
python -m pytest backend/tests/test_crud.py -v
```

**Bước 8. Kiểm tra luồng phê duyệt end-to-end**
```powershell
# Lấy token manager
$TOKEN = (curl -s -X POST http://localhost:8000/api/auth/login `
  -d "username=manager@poc.local&password=manager123" | ConvertFrom-Json).access_token

# Lấy id phiếu DRAFT mới nhất
$PR_ID = (curl -s http://localhost:8000/api/requests?status=DRAFT `
  -H "Authorization: Bearer $TOKEN" | ConvertFrom-Json).items[0].id

# Submit phiếu
curl -X POST "http://localhost:8000/api/requests/$PR_ID/submit" `
  -H "Authorization: Bearer $TOKEN"

# Approve phiếu
curl -X POST "http://localhost:8000/api/requests/$PR_ID/approve" `
  -H "Authorization: Bearer $TOKEN" `
  -H "Content-Type: application/json" `
  -d '{"reason": "Dữ liệu hợp lệ, duyệt đơn"}'

# Kiểm tra lịch sử quyết định
curl "http://localhost:8000/api/requests/$PR_ID/decisions" `
  -H "Authorization: Bearer $TOKEN"
```

Kỳ vọng lịch sử quyết định có 2 dòng: `SUBMIT` và `APPROVE`.

**Bước 9. Kiểm tra phân quyền**
```powershell
# Lấy token viewer
$VIEWER_TOKEN = (curl -s -X POST http://localhost:8000/api/auth/login `
  -d "username=viewer@poc.local&password=viewer123" | ConvertFrom-Json).access_token

# Viewer không được approve (phải trả về 403)
curl -X POST "http://localhost:8000/api/requests/$PR_ID/approve" `
  -H "Authorization: Bearer $VIEWER_TOKEN"
```
Kỳ vọng: `{"detail": "Không đủ quyền"}` với status 403.

---

## Bộ test cần viết

### `backend/tests/test_auth.py`

```python
# test_login_success:          email/password đúng → trả về token
# test_login_wrong_password:   password sai → 401
# test_login_inactive_user:    is_active=False → 403
# test_register_as_admin:      ADMIN tạo user mới → thành công
# test_register_as_viewer:     VIEWER tạo user → 403
# test_get_me:                 token hợp lệ → trả về user info
# test_change_password:        đổi mật khẩu → đăng nhập lại bằng mật khẩu mới
```

### `backend/tests/test_crud.py`

```python
# test_get_requests_list:      lấy danh sách phiếu → có items
# test_get_request_detail:     chi tiết phiếu có lines và decisions
# test_submit_draft:           DRAFT → PENDING_APPROVAL
# test_approve_as_manager:     MANAGER approve → APPROVED, có decision
# test_reject_requires_reason: reject không có reason → 422
# test_reject_as_viewer:       VIEWER reject → 403
# test_revise_returns_draft:   revise → DRAFT
# test_scan_alerts:            scan 30 SKU → có cảnh báo HIGH
# test_get_forecasts:          danh sách forecast_runs không rỗng
# test_get_suppliers:          danh sách suppliers có điểm đánh giá
```

---

## Ràng buộc

- Không sửa `db/schema.sql`.
- Không tạo `purchase_orders` từ bất kỳ endpoint nào trong nhóm này.
- `request_decisions` chỉ được INSERT, không UPDATE hay DELETE.
- `reason` bắt buộc khi action là REJECT hoặc REQUEST_CHANGES.
- Mật khẩu không được log ra terminal hay lưu vào `audit_logs`.
- Mọi thay đổi trạng thái phiếu phải ghi vào `request_decisions` kèm snapshot.

---

## Mẫu báo cáo kết quả

```
TRẠNG THÁI: THÀNH CÔNG / THẤT BẠI

Các file đã tạo hoặc sửa: (danh sách)

Kết quả test_auth.py:    X/7 pass
Kết quả test_crud.py:    X/10 pass

Kiểm tra luồng phê duyệt:
  - Submit:  DRAFT → PENDING_APPROVAL  : đạt / không đạt
  - Approve: PENDING_APPROVAL → APPROVED : đạt / không đạt
  - request_decisions có snapshot       : đạt / không đạt
  - Viewer bị chặn approve (403)        : đạt / không đạt

Kiểm tra scan alerts:
  - Số SKU scanned: ...
  - Số alert HIGH: ...

Vấn đề gặp phải và cách xử lý: ...
Việc cần làm thủ công sau khi agent xong: ...
```
