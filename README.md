# AI Procurement Agent - Local Development Environment

Dự án **AI Procurement Agent** hỗ trợ theo dõi tồn kho, phát hiện nguy cơ cạn hàng/dư hàng, đánh giá nhà cung cấp và lập phiếu đề xuất mua hàng tự động (Human-in-the-loop).

Tài liệu này hướng dẫn thiết lập môi trường phát triển cục bộ (Local Development Environment) sử dụng Docker và PostgreSQL 16.

---

## 1. Cấu trúc Dự án

```text
procurement-agent/
├── backend/                  # (FastAPI - sẽ phát triển ở giai đoạn tiếp theo)
├── frontend/                 # (React - sẽ phát triển ở giai đoạn tiếp theo)
├── db/
│   ├── schema.sql            # DDL tạo 14 bảng, ràng buộc và chỉ mục (PostgreSQL 16)
│   └── permissions.sql       # Phân quyền bảo mật cho role agent_svc
├── docs/                     # Tài liệu kiến trúc, database schema, kế hoạch 10 tuần
├── output/                   # Dữ liệu sạch lọc từ Walmart M5 (30 SKU PoC)
├── procurement_data/         # Dữ liệu mô phỏng chuỗi cung ứng (báo giá, tồn kho, PO)
├── docker-compose.yml        # Cấu hình dịch vụ Docker (PostgreSQL 16)
├── .env.example              # Mẫu biến môi trường
├── .gitignore                # Cấu hình Git bỏ qua file nhạy cảm (.env)
└── README.md                 # Hướng dẫn cài đặt và vận hành
```

---

## 2. Yêu cầu Tiền đề (Prerequisites)

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (hỗ trợ Docker Compose v2+). Đảm bảo Docker Desktop đang chạy.
- Terminal: Bash, Zsh hoặc PowerShell.
- (Tùy chọn) `psql` client hoặc công cụ quản trị GUI như DBeaver, TablePlus, pgAdmin.

---

## 3. Các bước Cài đặt và Khởi động Môi trường

### Bước 1: Thiết lập Biến Môi trường
Sao chép tệp mẫu `.env.example` thành `.env` và cập nhật các mật khẩu bảo mật:

```bash
# Trên Linux / macOS / Git Bash:
cp .env.example .env

# Hoặc trên Windows PowerShell:
Copy-Item .env.example .env
```

Chỉnh sửa tệp `.env`:
- `POSTGRES_DB`: Tên database (mặc định: `procurement`).
- `POSTGRES_USER`: Người dùng chính (mặc định: `app`).
- `DB_PASSWORD`: Mật khẩu cho user `app` (bắt buộc đổi từ placeholder `change_me_db_password`).
- `DB_PORT`: Cổng máy chủ (mặc định: `5432`, có thể đổi thành `5433` nếu cổng 5432 bị chiếm dụng).
- `AGENT_DB_PASSWORD`: Mật khẩu cho role `agent_svc`.

### Bước 2: Khởi động Dịch vụ Cơ sở Dữ liệu

Chạy lệnh Docker Compose để kéo image và khởi chạy container PostgreSQL 16 ở chế độ nền:

```bash
docker compose up -d db
```

> **Lưu ý**: Trong lần khởi động đầu tiên, Docker sẽ tự động mount tệp `./db/schema.sql` vào `/docker-entrypoint-initdb.d/01_schema.sql` và khởi tạo toàn bộ 14 bảng, ràng buộc toàn vẹn và các index tối ưu hóa.

### Bước 3: Kiểm tra Trạng thái Container và Healthcheck

Kiểm tra trạng thái sẵn sàng của container:

```bash
docker compose ps
```

Kết quả hiển thị `procurement_db` với trạng thái `healthy` (sử dụng cơ chế kiểm tra `pg_isready -U app -d procurement` mỗi 5 giây):

```text
NAME             IMAGE         COMMAND                  SERVICE   CREATED         STATUS                    PORTS
procurement_db   postgres:16   "docker-entrypoint.s…"   db        X seconds ago   Up X seconds (healthy)    0.0.0.0:5432->5432/tcp
```

Xem log khởi tạo nếu cần:

```bash
docker compose logs -f db
```

### Bước 4: Chạy Phân quyền cho Agent (`permissions.sql`)

Thư mục `./db` được mount chỉ đọc (`:ro`) vào `/db` trong container. Chạy lệnh sau để thiết lập role `agent_svc` và gán quyền phân tách bảo mật:

```bash
docker compose exec db psql -U app -d procurement -f /db/permissions.sql
```

> **Nguyên tắc Phân quyền (Security Guardrails)**:
> - `agent_svc` có quyền: Đọc tất cả bảng để phân tích; Ghi nhật ký chạy (`agent_runs`), vết công cụ (`agent_tool_calls`), phát hiện cảnh báo (`stock_alerts`), tạo phiếu đề xuất mua hàng nháp (`purchase_requests`, `purchase_request_lines`).
> - `agent_svc` **bị chặn tuyệt đối** (`REVOKE`): Không được tạo đơn mua thực tế (`purchase_orders`), không được sửa hay xóa nhật ký quyết định (`request_decisions`) và nhật ký kiểm toán (`audit_logs`).

---

## 4. Kiểm tra và Truy vấn Dữ liệu

### Truy cập psql CLI trực tiếp trong Container:

```bash
docker compose exec -it db psql -U app -d procurement
```

### Kiểm tra danh sách các bảng đã tạo:

```sql
\dt
```

14 bảng sẽ xuất hiện:
- `users`, `locations`, `products`, `suppliers`, `inventory_policies`
- `inventory_levels`, `demand_history`
- `supplier_quotes`, `quote_price_breaks`, `supplier_evaluations`
- `forecast_runs`, `demand_forecasts`
- `agent_runs`, `agent_tool_calls`, `stock_alerts`, `purchase_requests`, `purchase_request_lines`
- `request_decisions`, `purchase_orders`, `audit_logs`

### Kiểm tra phân quyền của role `agent_svc`:

```sql
\du agent_svc
```

Thoát khỏi psql:
```sql
\q
```

---

## 5. Dừng và Quản lý Dịch vụ

- **Tạm dừng dịch vụ (dữ liệu vẫn được giữ trong volume `pgdata`)**:
  ```bash
  docker compose down
  ```

- **Khởi động lại dịch vụ**:
  ```bash
  docker compose up -d db
  ```

- **Xóa toàn bộ container và xóa sạch dữ liệu volume để khởi tạo lại từ đầu (Reset Data)**:
  ```bash
  docker compose down -v
  ```
