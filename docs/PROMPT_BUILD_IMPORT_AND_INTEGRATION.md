# Prompt: Xây dựng API Import CSV và Tích hợp Dự báo

Copy toàn bộ nội dung dưới đây (từ mục "Vai trò" đến hết) và đưa cho agent lập trình.

---

## Vai trò

Bạn là kỹ sư backend Python. Nhiệm vụ là xây dựng hệ thống import dữ liệu CSV và tích hợp trigger chạy lại dự báo sau khi import. Không viết frontend, không thay đổi schema database, không sửa code Agent đã hoạt động.

---

## Bối cảnh hệ thống

- **Backend hiện có:** FastAPI với đầy đủ auth, agent, requests, alerts, forecasts, suppliers.
- **Database:** PostgreSQL 16 trong Docker. Các bảng liên quan: `products`, `locations`, `suppliers`, `demand_history`, `inventory_levels`, `supplier_quotes`, `quote_price_breaks`, `audit_logs`, `forecast_runs`.
- **ML pipeline:** `ml/save_forecasts.py` đã có, nhận tham số `--mode production --train-until YYYY-MM-DD --horizon 28`.
- **Môi trường:** Windows, PowerShell, venv đã có, pandas đã cài.

---

## Cấu trúc file cần tạo

```
backend/
├── core/
│   ├── validators.py        TẠO MỚI
│   ├── importers.py         TẠO MỚI
│   └── forecaster.py        TẠO MỚI
├── routers/
│   └── imports.py           TẠO MỚI
├── tests/
│   ├── test_import.py       TẠO MỚI
│   └── test_integration.py  TẠO MỚI
└── main.py                  SỬA: include router imports
```

---

## Nguyên tắc quan trọng về ID mapping

Hệ thống dùng hai loại định danh:

```
Định danh người dùng (trong file CSV):
  item_id  = "FOODS_2_074"   ← sku_code trong bảng products
  store_id = "CA_1"          ← code trong bảng locations
  supplier_code = "SUP_001"  ← code trong bảng suppliers

Định danh nội bộ DB (dùng trong quan hệ):
  product_id  = 7    ← products.id
  location_id = 1    ← locations.id
  supplier_id = 3    ← suppliers.id
```

**Mọi hàm import đều phải map từ định danh người dùng sang ID nội bộ trước khi ghi DB.** Nếu không tìm thấy mapping → ghi vào errors, không import dòng đó, không raise exception.

**Luồng import đúng thứ tự:**
```
Bước 1: /api/import/locations  (nếu có kho mới)
         /api/import/products   (nếu có SKU mới)
         /api/import/suppliers  (nếu có NCC mới)

Bước 2: /api/import/quotes     (báo giá)

Bước 3: /api/import/inventory  (tồn kho hiện tại)

Bước 4: /api/import/sales      (lịch sử → trigger forecast)
```

---

## File 1: `backend/core/validators.py`

### Hàm dùng chung

```python
def _check_required_columns(df, required: list) -> list:
    """Kiểm tra cột bắt buộc, trả về list cột thiếu."""

def _build_error(row_idx: int, column: str,
                 value, message: str, hint: str = None) -> dict:
    return {
        "row": row_idx + 2,  # +2 vì dòng 1 là header, index bắt đầu từ 0
        "column": column,
        "value": str(value),
        "message": message,
        "hint": hint
    }
```

### `validate_products_json(data: list) -> tuple[bool, list]`
```
Input: list of dict từ request body
Kiểm tra mỗi item:
  - sku_code: bắt buộc, không rỗng, không chứa ký tự đặc biệt
  - name: bắt buộc, không rỗng
  - category: bắt buộc, phải là FOODS | HOUSEHOLD | HOBBIES
  - unit: tùy chọn, mặc định 'pcs'
Trả về: (is_valid, errors)
```

### `validate_locations_json(data: list) -> tuple[bool, list]`
```
Input: list of dict từ request body
Kiểm tra mỗi item:
  - code: bắt buộc, không rỗng, không chứa khoảng trắng
  - name: bắt buộc, không rỗng
  - region: tùy chọn
Trả về: (is_valid, errors)
```

### `validate_sales_csv(df) -> tuple[bool, list]`
```
Cột bắt buộc: item_id, store_id, date, qty_sold
Cột tùy chọn: sell_price

Kiểm tra theo thứ tự:
1. Cột bắt buộc có đủ không
2. date đúng định dạng YYYY-MM-DD (thử parse, bắt ValueError)
3. date không trong tương lai (> ngày hôm nay)
4. qty_sold >= 0 và là số (thử int(), bắt ValueError)
5. sell_price > 0 nếu có giá trị
6. item_id tồn tại trong products.sku_code (truy vấn DB một lần,
   lấy tập hợp hợp lệ, rồi kiểm tra từng dòng)
   → Nếu không tồn tại:
     message: "SKU '{item_id}' chưa có trong hệ thống"
     hint: "Gọi POST /api/import/products trước"
7. store_id tồn tại trong locations.code
   → Nếu không tồn tại:
     message: "Cửa hàng '{store_id}' chưa có trong hệ thống"
     hint: "Gọi POST /api/import/locations trước"
8. Không có dòng trùng (item_id, store_id, date) trong chính file
```

### `validate_inventory_csv(df) -> tuple[bool, list]`
```
Cột bắt buộc: item_id, store_id, on_hand, on_order

Kiểm tra:
1. on_hand >= 0, on_order >= 0, cả hai là số nguyên
2. item_id tồn tại trong products.sku_code
   → hint: "Gọi POST /api/import/products trước"
3. store_id tồn tại trong locations.code
   → hint: "Gọi POST /api/import/locations trước"
4. Cặp (item_id, store_id) đã có trong inventory_levels
   → Nếu chưa có:
     message: "Chưa có bản ghi tồn kho cho cặp này"
     hint: "Chỉ cập nhật SKU đã có, không tạo mới tự động"
5. Không có dòng trùng (item_id, store_id) trong chính file
```

### `validate_suppliers_csv(df) -> tuple[bool, list]`
```
Cột bắt buộc: supplier_code, name, lead_time_days, moq
Cột tùy chọn: category, payment_terms_days

Kiểm tra:
1. lead_time_days > 0, là số nguyên
2. moq > 0, là số nguyên
3. payment_terms_days >= 0 nếu có
4. supplier_code không chứa ký tự đặc biệt (chỉ chữ, số, _, -)
5. Không có dòng trùng supplier_code trong chính file
```

### `validate_quotes_csv(df) -> tuple[bool, list]`
```
Cột bắt buộc: supplier_code, item_id, store_id, unit_cost, month
Cột tùy chọn: lead_time_days, moq,
              tier1_min_qty, tier1_discount,
              tier2_min_qty, tier2_discount

Kiểm tra:
1. unit_cost > 0
2. month đúng định dạng YYYY-MM (thử parse với "%Y-%m")
3. tier1_discount, tier2_discount trong [0, 1] nếu có
4. tier1_min_qty < tier2_min_qty nếu cả hai có
5. supplier_code tồn tại trong suppliers.code
   → hint: "Gọi POST /api/import/suppliers trước"
6. item_id tồn tại trong products.sku_code
   → hint: "Gọi POST /api/import/products trước"
7. store_id tồn tại trong locations.code
   → hint: "Gọi POST /api/import/locations trước"
8. Không có dòng trùng (supplier_code, item_id, store_id, month)
```

---

## File 2: `backend/core/importers.py`

### Class ImportResult

```python
from dataclasses import dataclass, field

@dataclass
class ImportResult:
    rows_total:    int = 0
    rows_inserted: int = 0
    rows_updated:  int = 0
    rows_skipped:  int = 0
    errors:        list = field(default_factory=list)
    duration_ms:   int = 0

    def to_dict(self) -> dict:
        return {
            "rows_total":    self.rows_total,
            "rows_inserted": self.rows_inserted,
            "rows_updated":  self.rows_updated,
            "rows_skipped":  self.rows_skipped,
            "errors":        self.errors,
            "duration_ms":   self.duration_ms
        }
```

### Hàm dùng chung

```python
def _get_product_map() -> dict:
    """Trả về {sku_code: product_id} cho tất cả products."""
    rows = execute_sql("SELECT id, sku_code FROM products", {})
    return {r["sku_code"]: r["id"] for r in rows}

def _get_location_map() -> dict:
    """Trả về {code: location_id} cho tất cả locations."""
    rows = execute_sql("SELECT id, code FROM locations", {})
    return {r["code"]: r["id"] for r in rows}

def _get_supplier_map() -> dict:
    """Trả về {code: supplier_id} cho tất cả suppliers."""
    rows = execute_sql("SELECT id, code FROM suppliers", {})
    return {r["code"]: r["id"] for r in rows}

def _write_audit_log(user_id: int, action: str,
                     entity: str, after_data: dict):
    execute_sql("""
        INSERT INTO audit_logs
          (actor_type, actor_id, action, entity, after_data)
        VALUES ('USER', %(uid)s, %(action)s, %(entity)s, %(data)s)
    """, {"uid": user_id, "action": action,
          "entity": entity, "data": json.dumps(after_data)})
```

### `import_products(data: list, user_id: int) -> ImportResult`
```
Input: list of dict đã validate

Với mỗi item:
  INSERT INTO products (sku_code, name, category, unit, is_active)
  VALUES (%(sku_code)s, %(name)s, %(category)s,
          %(unit)s, TRUE)
  ON CONFLICT (sku_code)
  DO UPDATE SET name     = EXCLUDED.name,
                category = EXCLUDED.category,
                unit     = EXCLUDED.unit
  RETURNING (xmax = 0) AS inserted
  → xmax = 0 nghĩa là INSERT, xmax != 0 nghĩa là UPDATE

Ghi audit_logs sau khi xong.
```

### `import_locations(data: list, user_id: int) -> ImportResult`
```
Input: list of dict đã validate

INSERT INTO locations (code, name, region)
VALUES (%(code)s, %(name)s, %(region)s)
ON CONFLICT (code)
DO UPDATE SET name = EXCLUDED.name, region = EXCLUDED.region
RETURNING (xmax = 0) AS inserted

Ghi audit_logs sau khi xong.
```

### `import_suppliers(df, user_id: int) -> ImportResult`
```
Gọi _get_supplier_map() để kiểm tra trùng
INSERT INTO suppliers (code, name, category,
  lead_time_days, moq, payment_terms_days, is_active)
ON CONFLICT (code)
DO UPDATE SET name = EXCLUDED.name,
              lead_time_days = EXCLUDED.lead_time_days,
              moq = EXCLUDED.moq,
              payment_terms_days = EXCLUDED.payment_terms_days
RETURNING (xmax = 0) AS inserted

Ghi audit_logs.
```

### `import_sales(df, user_id: int) -> ImportResult`
```
1. Gọi _get_product_map() và _get_location_map() một lần
2. Với mỗi dòng:
   a. Map item_id → product_id, store_id → location_id
   b. Nếu không tìm thấy: result.rows_skipped += 1, tiếp tục
   c. INSERT INTO demand_history
        (product_id, location_id, sale_date, qty_sold, sell_price)
      VALUES (%(pid)s, %(lid)s, %(date)s, %(qty)s, %(price)s)
      ON CONFLICT (product_id, location_id, sale_date)
      DO UPDATE SET qty_sold    = EXCLUDED.qty_sold,
                    sell_price  = EXCLUDED.sell_price
      RETURNING (xmax = 0) AS inserted
   d. Dựa vào inserted: tăng rows_inserted hoặc rows_updated
3. Ghi audit_logs: {"rows": rows_inserted + rows_updated,
                     "date_range": [min_date, max_date]}
```

### `import_inventory(df, user_id: int) -> ImportResult`
```
1. Gọi _get_product_map() và _get_location_map()
2. Với mỗi dòng:
   a. Map item_id → product_id, store_id → location_id
   b. Nếu không tìm thấy: rows_skipped += 1, tiếp tục
   c. UPDATE inventory_levels
      SET on_hand    = %(on_hand)s,
          on_order   = %(on_order)s,
          updated_at = now()
      WHERE product_id  = %(pid)s
        AND location_id = %(lid)s
   d. Nếu UPDATE ảnh hưởng 0 dòng:
      result.rows_skipped += 1
      result.errors.append({
          "row": i + 2,
          "column": "item_id,store_id",
          "value": f"{row['item_id']},{row['store_id']}",
          "message": "Chưa có bản ghi tồn kho, bỏ qua",
          "hint": "inventory_levels chỉ cập nhật SKU đã có"
      })
   e. Nếu UPDATE ảnh hưởng 1 dòng: rows_updated += 1
3. Ghi audit_logs.
```

### `import_quotes(df, user_id: int) -> ImportResult`
```
1. Gọi _get_supplier_map(), _get_product_map(), _get_location_map()
2. Với mỗi dòng:
   a. Map các code → ID
   b. Nếu không tìm thấy: rows_skipped += 1, tiếp tục
   c. Tính valid_from = ngày đầu tháng (month + "-01")
      Tính valid_to   = ngày cuối tháng
   d. Vô hiệu hóa báo giá cũ cùng kỳ:
      UPDATE supplier_quotes SET valid_to = %(valid_from)s::date - 1
      WHERE supplier_id = %(sid)s
        AND product_id  = %(pid)s
        AND location_id = %(lid)s
        AND valid_from <= %(valid_from)s::date
        AND (valid_to IS NULL OR valid_to >= %(valid_from)s::date)
   e. INSERT supplier_quotes mới → lấy quote_id
   f. INSERT quote_price_breaks:
      Bậc 1: (quote_id, 1, unit_cost)
      Bậc 2: (quote_id, tier1_min_qty, unit_cost*(1-tier1_disc))
              nếu tier1_min_qty có giá trị
      Bậc 3: (quote_id, tier2_min_qty, unit_cost*(1-tier2_disc))
              nếu tier2_min_qty có giá trị
      Làm tròn 2 chữ số thập phân trước khi lưu
   g. rows_inserted += 1
3. Ghi audit_logs.
```

---

## File 3: `backend/routers/imports.py`

### Endpoint 1: `POST /api/import/products`
```
Body: [{"sku_code": str, "name": str,
        "category": str, "unit": str}]
Quyền: ADMIN
Logic:
  1. validate_products_json(data)
  2. Nếu errors → 422 kèm danh sách lỗi
  3. import_products(data, current_user.id)
Response: ImportResult + danh sách sku_code đã tạo/cập nhật
```

### Endpoint 2: `POST /api/import/locations`
```
Body: [{"code": str, "name": str, "region": str}]
Quyền: ADMIN
Logic: validate_locations_json → import_locations
Response: ImportResult + danh sách code đã tạo/cập nhật
```

### Endpoint 3: `POST /api/import/suppliers`
```
Content-Type: multipart/form-data, file CSV
Quyền: ADMIN
Logic: đọc CSV → validate_suppliers_csv → import_suppliers
Response: ImportResult
```

### Endpoint 4: `POST /api/import/quotes`
```
Content-Type: multipart/form-data, file CSV
Quyền: ADMIN
Giới hạn: file <= 10MB, chỉ .csv
Logic: đọc CSV → validate_quotes_csv → import_quotes
Response: ImportResult
```

### Endpoint 5: `POST /api/import/inventory`
```
Content-Type: multipart/form-data, file CSV
Quyền: ADMIN
Logic: đọc CSV → validate_inventory_csv → import_inventory
Response: ImportResult
Lưu ý: Không trigger forecast rerun
```

### Endpoint 6: `POST /api/import/sales`
```
Content-Type: multipart/form-data, file CSV
Quyền: ADMIN
Giới hạn: file <= 10MB, chỉ .csv
Logic:
  1. Đọc file vào DataFrame (pandas)
     Encode: thử UTF-8 trước, fallback UTF-8-BOM, rồi latin-1
  2. validate_sales_csv(df)
  3. Nếu có lỗi validation:
     → 422, trả về errors, KHÔNG import bất kỳ dòng nào
  4. import_sales(df, current_user.id)
  5. Nếu rows_inserted + rows_updated > 0:
     background_tasks.add_task(trigger_forecast_rerun)
     forecast_rerun_triggered = True
  6. Trả về ImportResult + forecast_rerun_triggered

Response:
{
  "status": "SUCCESS" | "PARTIAL" | "FAILED",
  "file": "sales_june2016.csv",
  "rows_total": 840,
  "rows_inserted": 820,
  "rows_updated": 15,
  "rows_skipped": 5,
  "errors": [],
  "forecast_rerun_triggered": true,
  "duration_ms": 1240
}

status:
  SUCCESS: rows_skipped = 0 và errors = []
  PARTIAL: có rows_skipped hoặc errors nhưng vẫn import được
  FAILED:  rows_inserted + rows_updated = 0
```

### Endpoint 7: `GET /api/import/template/{type}`
```
type: products | locations | suppliers | quotes | inventory | sales
Quyền: Đã đăng nhập
Response: file CSV tải về

Content-Type: text/csv; charset=utf-8
Content-Disposition: attachment; filename="template_{type}.csv"

Nội dung template (3 dòng dữ liệu mẫu):

template_products.csv:
  sku_code,name,category,unit
  FOODS_NEW_001,Sản phẩm thực phẩm mới,FOODS,pcs
  HOUSEHOLD_NEW_001,Sản phẩm gia dụng mới,HOUSEHOLD,pcs

template_locations.csv:
  code,name,region
  CA_3,California Store 3,CA
  TX_2,Texas Store 2,TX

template_suppliers.csv:
  supplier_code,name,lead_time_days,moq,category,payment_terms_days
  NEW_SUP_001,Nhà cung cấp A,5,20,FOODS,30
  NEW_SUP_002,Nhà cung cấp B,10,50,HOUSEHOLD,45

template_quotes.csv:
  supplier_code,item_id,store_id,unit_cost,month,lead_time_days,moq,tier1_min_qty,tier1_discount,tier2_min_qty,tier2_discount
  NEW_SUP_001,FOODS_2_074,CA_1,1.75,2016-06,5,20,100,0.03,500,0.06

template_inventory.csv:
  item_id,store_id,on_hand,on_order
  FOODS_2_074,CA_1,45,0
  FOODS_3_795,CA_1,10,20

template_sales.csv:
  item_id,store_id,date,qty_sold,sell_price
  FOODS_2_074,CA_1,2016-06-01,3,2.50
  FOODS_2_074,CA_1,2016-06-02,5,2.50
  FOODS_2_074,CA_1,2016-06-03,2,2.50
```

---

## File 4: `backend/core/forecaster.py`

```python
import subprocess, asyncio
from backend.core.database import execute_sql

async def get_latest_sale_date() -> str | None:
    """Trả về MAX(sale_date) từ demand_history."""
    rows = execute_sql(
        "SELECT MAX(sale_date)::text AS d FROM demand_history", {}
    )
    return rows[0]["d"] if rows else None

async def should_retrain(latest_date: str) -> bool:
    """
    Trả về True nếu chưa có forecast_run với
    trained_until = latest_date VÀ model_version LIKE '%full%'.
    """
    rows = execute_sql("""
        SELECT id FROM forecast_runs
        WHERE trained_until = %(d)s::date
          AND model_version LIKE '%%full%%'
        LIMIT 1
    """, {"d": latest_date})
    return len(rows) == 0

async def trigger_forecast_rerun():
    """
    Chạy lại pipeline ML sau khi import dữ liệu mới.
    Chạy trong BackgroundTasks, không block request.
    """
    try:
        latest_date = await get_latest_sale_date()
        if latest_date is None:
            return

        if not await should_retrain(latest_date):
            # Đã có run với ngày này, không cần chạy lại
            _write_audit("FORECAST_SKIP",
                {"reason": "run already exists",
                 "trained_until": latest_date})
            return

        # Chạy save_forecasts.py trong subprocess với timeout 300 giây
        result = subprocess.run(
            ["python", "ml/save_forecasts.py",
             "--dsn", os.environ["DATABASE_URL"],
             "--mode", "production",
             "--train-until", latest_date,
             "--horizon", "28"],
            capture_output=True, text=True, timeout=300
        )

        if result.returncode == 0:
            _write_audit("FORECAST_RETRAIN",
                {"trained_until": latest_date,
                 "triggered_by": "import_sales",
                 "stdout": result.stdout[-500:]})
        else:
            _write_audit("FORECAST_RETRAIN_FAILED",
                {"trained_until": latest_date,
                 "stderr": result.stderr[-500:]})

    except subprocess.TimeoutExpired:
        _write_audit("FORECAST_RETRAIN_TIMEOUT",
            {"timeout_seconds": 300})
    except Exception as e:
        _write_audit("FORECAST_RETRAIN_ERROR",
            {"error": str(e)})

def _write_audit(action: str, data: dict):
    execute_sql("""
        INSERT INTO audit_logs
          (actor_type, action, entity, after_data)
        VALUES ('SYSTEM', %(action)s, 'forecast_runs', %(data)s)
    """, {"action": action, "data": json.dumps(data)})
```

---

## File 5: `backend/tests/test_import.py`

```python
# Chuẩn bị: đọc token ADMIN từ fixture, tạo DataFrame mẫu

# test_validate_products_valid:
#   Data đúng → is_valid=True, errors=[]

# test_validate_products_invalid_category:
#   category = "UNKNOWN" → is_valid=False, lỗi chỉ đúng dòng

# test_import_products_insert:
#   POST /api/import/products với 2 SKU mới
#   → products tăng 2, rows_inserted=2

# test_import_products_upsert:
#   POST lại cùng sku_code với name khác
#   → rows_updated=2, rows_inserted=0

# test_validate_sales_unknown_sku:
#   item_id = "NONEXISTENT" → errors có hint chứa /api/import/products

# test_validate_sales_negative_qty:
#   qty_sold = -1 → errors có column="qty_sold"

# test_import_sales_insert:
#   Import 5 dòng mới cho SKU đã có
#   → demand_history tăng 5, rows_inserted=5

# test_import_sales_upsert:
#   Import lại cùng ngày với qty khác
#   → rows_updated=5, không tăng thêm dòng mới

# test_import_inventory_update:
#   Import on_hand=99 cho SKU đã có
#   → inventory_levels.on_hand = 99

# test_import_inventory_unknown_sku:
#   item_id = "NONEXISTENT"
#   → rows_skipped=1, inventory_levels không đổi

# test_get_template_sales:
#   GET /api/import/template/sales
#   → Content-Type: text/csv, có cột item_id,store_id,date,qty_sold

# test_import_requires_admin:
#   MANAGER gọi POST /api/import/sales → 403

# test_import_invalid_csv_format:
#   File không phải CSV (upload file .txt)
#   → 422 với message rõ ràng
```

---

## File 6: `backend/tests/test_integration.py`

```python
# test_import_sales_triggers_forecast:
#   1. Import 10 dòng sales mới (ngày 2016-06-01)
#   2. Response có forecast_rerun_triggered=True
#   3. Chờ 15 giây (Qwen 7B cần thời gian)
#   4. GET /api/forecasts → có run mới với trained_until >= 2016-06-01

# test_import_same_date_no_retrain:
#   Import dữ liệu đến ngày đã có forecast run
#   → should_retrain() = False
#   → Sau 10 giây: forecast_runs không tăng

# test_full_procurement_flow:
#   1. POST /api/import/inventory (cập nhật on_hand thấp)
#   2. POST /api/alerts/scan → có alert HIGH
#   3. POST /api/agent/run (tạo đề xuất)
#   4. GET /api/requests → có phiếu DRAFT
#   5. POST /api/requests/{id}/submit
#   6. POST /api/requests/{id}/approve (token MANAGER)
#   7. GET /api/requests/{id}/decisions → 2 dòng (SUBMIT + APPROVE)
#   Toàn bộ luồng pass không lỗi
```

---

## Quy trình thực hiện

Thực hiện đúng thứ tự sau. Dừng và báo cáo nếu một bước thất bại.

**Bước 1. Cài thư viện nếu chưa có**
```powershell
pip install pandas python-multipart
```

**Bước 2. Tạo validators.py và kiểm tra cú pháp**
```powershell
python -m py_compile backend/core/validators.py && echo "OK"
```

**Bước 3. Tạo importers.py và kiểm tra cú pháp**
```powershell
python -m py_compile backend/core/importers.py && echo "OK"
```

**Bước 4. Tạo forecaster.py và kiểm tra cú pháp**
```powershell
python -m py_compile backend/core/forecaster.py && echo "OK"
```

**Bước 5. Tạo router imports.py và include vào main.py**
```powershell
python -m py_compile backend/routers/imports.py && echo "OK"
```

**Bước 6. Khởi động server**
```powershell
uvicorn backend.main:app --reload --port 8000
```

**Bước 7. Test thủ công luồng import products → sales**
```powershell
# Lấy token ADMIN
$TOKEN = (curl -s -X POST http://localhost:8000/api/auth/login `
  -d "username=admin@poc.local&password=admin123" | ConvertFrom-Json).access_token

# Tải template sales
curl -o template_sales.csv `
  http://localhost:8000/api/import/template/sales `
  -H "Authorization: Bearer $TOKEN"

# Import sales (dùng file template vừa tải)
curl -X POST http://localhost:8000/api/import/sales `
  -H "Authorization: Bearer $TOKEN" `
  -F "file=@template_sales.csv"
```

Kỳ vọng: `forecast_rerun_triggered: true`, `rows_inserted > 0`.

**Bước 8. Chạy test tự động**
```powershell
python -m pytest backend/tests/test_import.py -v
python -m pytest backend/tests/test_integration.py -v --timeout=60
```

**Bước 9. Kiểm tra DB sau khi import**
```powershell
docker compose exec db psql -U app -d procurement -c "
SELECT action, after_data->>'rows' AS rows,
       after_data->>'triggered_by' AS triggered_by,
       created_at
FROM audit_logs
WHERE action IN ('IMPORT_SALES','IMPORT_INVENTORY',
                 'FORECAST_RETRAIN','FORECAST_SKIP')
ORDER BY created_at DESC LIMIT 5;"
```

---

## Ràng buộc

- Không sửa `db/schema.sql`.
- import_inventory chỉ UPDATE, không INSERT row mới vào inventory_levels.
- import_sales dùng ON CONFLICT upsert, không tạo trùng dòng.
- Nếu validation thất bại thì không import bất kỳ dòng nào (all-or-nothing).
- trigger_forecast_rerun chạy trong background, không block request.
- Nếu ML pipeline lỗi, ghi vào audit_logs nhưng không raise exception ra client.
- Mọi SQL dùng parameterized query với %(param)s.
- Escape ký tự % trong LIKE bằng %% (psycopg 3).

---

## Mẫu báo cáo kết quả

```
TRẠNG THÁI: THÀNH CÔNG / THẤT BẠI

Các file đã tạo: (danh sách)

Kết quả test_import.py:      X/13 pass
Kết quả test_integration.py: X/3  pass

Kiểm tra import products:
  - Thêm SKU mới:     đạt / không đạt
  - Upsert SKU cũ:    đạt / không đạt

Kiểm tra import sales:
  - Insert dòng mới:  đạt / không đạt
  - Upsert ngày cũ:   đạt / không đạt
  - Lỗi SKU không tồn tại có hint: đạt / không đạt
  - forecast_rerun_triggered: đạt / không đạt

Kiểm tra import inventory:
  - Update on_hand:   đạt / không đạt
  - Skip SKU lạ:      đạt / không đạt

Luồng end-to-end:
  - Import → scan → agent → approve: đạt / không đạt

Vấn đề gặp phải và cách xử lý: ...
```
