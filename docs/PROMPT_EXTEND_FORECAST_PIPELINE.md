# Prompt: Mở rộng Pipeline Dự báo - Dự báo bất kỳ ngày nào từ dữ liệu import

Copy toàn bộ nội dung dưới đây (từ mục "Vai trò" đến hết) và đưa cho agent lập trình.

---

## Vai trò

Bạn là kỹ sư ML và backend Python. Nhiệm vụ gồm ba phần:
1. Chuẩn hóa cú pháp SQL trong toàn bộ backend
2. Bổ sung test cho trường hợp báo giá một ngày
3. Mở rộng pipeline ML để dự báo bất kỳ SKU nào, đến bất kỳ ngày nào từ dữ liệu import vào DB

Không viết frontend. Không thay đổi schema database. Mọi test hiện tại phải vẫn pass sau khi thay đổi.

---

## Bối cảnh hệ thống

- **Backend:** FastAPI, `backend/core/database.py` hiện hỗ trợ cả `%(param)s` và `:param`.
- **ML hiện tại:** `ml/save_forecasts.py` đọc từ `output/sales_clean_long.csv` (30 SKU M5), huấn luyện đến `2016-05-22` cố định.
- **Mục tiêu:** Sau khi import file sales CSV bất kỳ, pipeline tự động huấn luyện đến ngày cuối của file đó và dự báo 28 ngày tiếp theo cho mọi SKU đủ điều kiện trong DB.
- **Database:** PostgreSQL 16 trong Docker. Bảng `demand_history` chứa dữ liệu import, `forecast_runs` và `demand_forecasts` chứa kết quả dự báo.
- **Môi trường:** Windows, PowerShell, venv đã có, pandas và lightgbm đã cài.

---

## Phần 1: Chuẩn hóa cú pháp SQL

### Vấn đề

`database.py` hiện có hàm `_format_sql_and_params` xử lý cả hai cú pháp:
- `%(param)s` (psycopg 3 native)
- `:param` (SQLAlchemy style)

Hai cú pháp lẫn lộn gây bug khó tìm. Cần chọn một cú pháp duy nhất.

### Quyết định

**Dùng `%(param)s` làm chuẩn duy nhất** vì đây là cú pháp gốc của psycopg 3.

### Việc cần làm

**Bước 1.1: Sửa `backend/core/database.py`**
```python
# Bỏ hoàn toàn nhánh xử lý :param
# Giữ nguyên:
#   - Hàm execute_sql(query, params) nhận %(param)s
#   - Escape %% trong LIKE khi params không rỗng
#   - get_db() context manager

# Thêm vào docstring của execute_sql:
# Quy tắc:
#   1. Dùng %(param)s cho mọi tham số
#   2. Dấu % trong LIKE phải viết %%
#      Ví dụ: WHERE col LIKE '%%full%%'
#   3. Không dùng :param (SQLAlchemy style)
```

**Bước 1.2: Grep và sửa toàn bộ backend**

Chạy lệnh tìm query dùng `:param`:
```powershell
grep -rn ":\w\+" backend/ --include="*.py" | grep -v "http:" | grep -v "#"
```

Với mỗi file có kết quả:
- Đổi `:param` → `%(param)s`
- Đổi `{"param": value}` sang `{"param": value}` (không đổi dict)
- Chạy lại `python -m py_compile <file>` sau khi sửa

**Bước 1.3: Thêm test kiểm tra**
```python
# Thêm vào backend/tests/test_tools.py:

# test_sql_like_with_params:
#   Truy vấn: WHERE model_version LIKE '%%full%%' AND id = %(id)s
#   Params: {"id": 3}
#   Kỳ vọng: trả về dòng đúng, không lỗi ProgrammingError

# test_sql_no_percent_confusion:
#   Truy vấn LIKE với 3 ký tự % khác nhau trong cùng câu
#   Kỳ vọng: không lỗi, kết quả đúng
```

**Bước 1.4: Thêm vào `README.md`**
```markdown
## Quy tắc SQL (bắt buộc tuân thủ)

- Dùng `%(param)s` cho mọi parameterized query với psycopg 3
- Ký tự `%` trong chuỗi LIKE phải viết `%%`
  - Đúng:  `WHERE col LIKE '%%abc%%'`
  - Sai:   `WHERE col LIKE '%abc%'`
- Không dùng `:param` (SQLAlchemy style)
- Không dùng f-string hay .format() để ghép SQL
```

---

## Phần 2: Test bổ sung cho báo giá một ngày

### Vấn đề

Khi `supplier_quotes` có `valid_from = valid_to` (chỉ hiệu lực một ngày), lệnh đóng báo giá cũ có thể vi phạm `CHECK (valid_to >= valid_from)`. Fix `GREATEST(valid_from, new_valid_from - 1)` đã có nhưng chưa được test.

### Việc cần làm

**Thêm vào `backend/tests/test_import.py`:**

```python
# test_import_quotes_single_day_valid_to:
#   Setup:
#     INSERT supplier_quotes với valid_from='2016-04-01',
#     valid_to='2016-04-01' (chỉ một ngày) cho FOODS_2_074/CA_1
#   Action:
#     Import báo giá mới cho cùng SKU tháng 4/2016
#   Kỳ vọng:
#     - Không có lỗi DB (không vi phạm CHECK)
#     - Báo giá cũ có valid_to được cập nhật đúng
#     - rows_inserted = 1

# test_import_quotes_replace_same_month:
#   Setup:
#     Có báo giá tháng 5/2016 cho FOODS_2_074/CA_1, giá 1.95 USD
#   Action:
#     Import báo giá mới tháng 5/2016, giá 2.10 USD
#   Kỳ vọng:
#     - Báo giá cũ bị đóng (valid_to cập nhật)
#     - Báo giá mới được INSERT với giá 2.10
#     - GET /api/suppliers/{id}/quotes chỉ trả về giá 2.10
#       (không còn 1.95)

# test_import_quotes_overlapping_months:
#   Setup: Không có báo giá tháng 6/2016
#   Action: Import báo giá tháng 6/2016
#   Kỳ vọng:
#     - INSERT thành công
#     - Báo giá tháng 5/2016 không bị ảnh hưởng
```

---

## Phần 3: Mở rộng Pipeline ML

### Mục tiêu

Sau khi người dùng import file CSV sales với dữ liệu đến ngày bất kỳ (ví dụ 2016-06-15 hoặc 2024-11-01), hệ thống tự động:
1. Huấn luyện mô hình đến ngày đó
2. Dự báo 28 ngày tiếp theo
3. SKU mới chưa đủ 90 ngày lịch sử dùng fallback

### File 3.1: Tạo `ml/data_loader.py`

```python
"""
Module đọc dữ liệu cho pipeline ML.
Hai nguồn: CSV (M5, để benchmark) và DB (production).
"""

import pandas as pd

REQUIRED_COLS_CSV = ["item_id", "store_id", "date", "sales",
                     "sell_price", "event_name_1", "snap", "cat_id"]

REQUIRED_COLS_DB  = ["product_id", "location_id", "sale_date",
                     "qty_sold", "sell_price", "is_event", "snap"]


def load_from_csv(clean_dir: str,
                  skus: list = None) -> pd.DataFrame:
    """
    Đọc từ sales_clean_long.csv (30 SKU M5).
    Dùng cho benchmark hoặc môi trường phát triển.

    Chuẩn hóa output:
      - Đổi tên cột: sales → qty_sold, date → sale_date,
        event_name_1 → is_event (bool), item_id → sku_code,
        store_id → store_code
      - Thêm product_id và location_id = None
        (sẽ được map sau nếu cần)
      - Giữ cat_id và dept_id nếu có

    skus: list of "item_id_store_id" để lọc, None = tất cả
    """


def load_from_db(dsn: str,
                 train_until: str,
                 min_history_days: int = 90) -> pd.DataFrame:
    """
    Đọc từ bảng demand_history trong PostgreSQL.
    Dùng khi source=db (production).

    SQL cơ bản:
      SELECT
        p.sku_code,     l.code AS store_code,
        p.category,     dh.product_id, dh.location_id,
        dh.sale_date,   dh.qty_sold,
        dh.sell_price,  dh.is_event,  dh.snap
      FROM demand_history dh
      JOIN products  p ON p.id = dh.product_id
      JOIN locations l ON l.id = dh.location_id
      WHERE dh.sale_date <= %(train_until)s::date
      ORDER BY dh.product_id, dh.location_id, dh.sale_date

    Lọc SKU không đủ min_history_days:
      - Tính số ngày lịch sử của từng (product_id, location_id)
      - Nếu < min_history_days: thêm vào danh sách skipped_skus
      - Không bao gồm trong output DataFrame chính
      - Trả về (df, skipped_skus)

    Trả về: (df: pd.DataFrame, skipped_skus: list[dict])
    skipped_skus: [{"sku_code": str, "store_code": str,
                    "history_days": int, "reason": str}]
    """


def get_eligible_skus(dsn: str,
                      train_until: str,
                      min_history_days: int = 90) -> tuple:
    """
    Trả về:
      eligible: list of {"product_id", "location_id",
                          "sku_code", "store_code", "history_days"}
      ineligible: list of cùng cấu trúc + "reason"
    """
    sql = """
        SELECT
            p.id AS product_id,  l.id AS location_id,
            p.sku_code,          l.code AS store_code,
            COUNT(DISTINCT dh.sale_date) AS history_days
        FROM demand_history dh
        JOIN products  p ON p.id = dh.product_id
        JOIN locations l ON l.id = dh.location_id
        WHERE dh.sale_date <= %(train_until)s::date
        GROUP BY p.id, l.id, p.sku_code, l.code
    """
    # Tách eligible (>= min_history_days) và ineligible


def get_calendar_features(dates: pd.Series) -> pd.DataFrame:
    """
    Sinh đặc trưng lịch từ cột ngày.
    KHÔNG phụ thuộc vào file calendar.csv của M5.
    Hoạt động với bất kỳ ngày nào từ 2000 trở đi.

    Trả về DataFrame với các cột:
      dayofweek (0=Monday..6=Sunday)
      month (1-12)
      year
      day (1-31)
      is_weekend (bool)
      is_month_start (bool)
      is_month_end (bool)
      quarter (1-4)
    """
    df = pd.DataFrame({"date": pd.to_datetime(dates)})
    df["dayofweek"]     = df["date"].dt.dayofweek
    df["month"]         = df["date"].dt.month
    df["year"]          = df["date"].dt.year
    df["day"]           = df["date"].dt.day
    df["is_weekend"]    = df["dayofweek"].isin([5, 6]).astype(int)
    df["is_month_start"] = df["date"].dt.is_month_start.astype(int)
    df["is_month_end"]   = df["date"].dt.is_month_end.astype(int)
    df["quarter"]        = df["date"].dt.quarter
    return df.drop(columns=["date"])
```

### File 3.2: Sửa `ml/features.py`

**Thay đổi chính:**
```python
# Trước (phụ thuộc M5):
#   Dùng cột snap_CA, snap_TX, snap_WI
#   Dùng d_idx (chỉ số ngày M5 1..1941)
#   Merge với calendar.csv để lấy sự kiện

# Sau (độc lập, dùng được với bất kỳ dữ liệu nào):
#   Dùng cột snap (đã chuẩn hóa trong demand_history và data_loader)
#   Dùng sale_date (ngày thực)
#   Lấy đặc trưng lịch từ get_calendar_features()
#   is_event từ cột is_event trong demand_history

def build_features(df: pd.DataFrame,
                   mode: str = "train",
                   label_encoders: dict = None) -> tuple:
    """
    Tạo đặc trưng cho LightGBM.

    mode='train':
      - Tạo label_encoders cho sku_code và store_code
      - Trả về (df_features, label_encoders)

    mode='predict':
      - Dùng label_encoders đã có (truyền vào)
      - SKU/store chưa thấy khi train → encode thành -1
      - Trả về (df_features, label_encoders)

    Đặc trưng:
      lag_7, lag_14, lag_28           (groupby sku+store, shift theo date)
      rolling_mean_7, rolling_mean_28 (shift(1) để tránh leak)
      rolling_std_7                   (shift(1))
      dayofweek, month, is_weekend    (từ get_calendar_features)
      is_month_start, is_month_end, quarter
      snap, is_event                  (từ df đầu vào)
      sell_price                      (giữ nguyên hoặc forward-fill)
      sku_encoded, store_encoded      (label encoded)

    Các cột không dùng làm feature (drop trước khi train):
      sale_date, qty_sold, product_id, location_id,
      sku_code, store_code, cat_id
    """
```

### File 3.3: Sửa `ml/save_forecasts.py`

**Tham số dòng lệnh mới:**
```
--dsn           chuỗi kết nối PostgreSQL (bắt buộc)
--mode          production | eval (mặc định: production)
--source        db | csv (mặc định: db)
--train-until   YYYY-MM-DD (bắt buộc)
--horizon       số ngày dự báo (mặc định: 28)
--min-history   ngày tối thiểu để huấn luyện SKU (mặc định: 90)
--clean-dir     thư mục CSV (chỉ cần khi source=csv)
```

**Luồng khi `--source db` (production):**
```python
def main_production(args):
    # 1. Lấy danh sách SKU đủ điều kiện từ DB
    eligible, ineligible = get_eligible_skus(
        args.dsn, args.train_until, args.min_history
    )
    print(f"SKU đủ điều kiện: {len(eligible)}")
    print(f"SKU dùng fallback: {len(ineligible)}")

    # 2. Đọc dữ liệu lịch sử từ DB
    df, skipped = load_from_db(
        args.dsn, args.train_until, args.min_history
    )

    # 3. Xây đặc trưng
    df_feat, label_encoders = build_features(df, mode="train")

    # 4. Train 3 mô hình (p10, p50, p90)
    models = train_quantile_models(df_feat,
                                   best_params=load_best_params())
    # Lưu model và label_encoders
    save_models(models, label_encoders,
                suffix=f"full_{args.train_until}")

    # 5. Tạo khung ngày tương lai
    forecast_start = (pd.Timestamp(args.train_until)
                      + pd.Timedelta(days=1)).date().isoformat()
    future_df = build_future_frame(
        eligible, args.train_until, args.horizon,
        dsn=args.dsn, label_encoders=label_encoders
    )

    # 6. Dự báo đệ quy
    forecasts = recursive_forecast(
        future_df, models, label_encoders, args.horizon
    )

    # 7. Fallback cho SKU không đủ lịch sử
    fallback_forecasts = []
    for sku in ineligible:
        fb = fallback_forecast(sku, args.dsn,
                               args.train_until, args.horizon)
        fallback_forecasts.append(fb)

    all_forecasts = pd.concat([forecasts] + fallback_forecasts)

    # 8. Lưu vào DB
    metrics = compute_metrics_from_last_test(args.dsn)
    save_to_db(
        dsn=args.dsn,
        forecasts=all_forecasts,
        model_name="lightgbm_global",
        model_version=f"v1.0-full-{args.train_until}",
        trained_until=args.train_until,
        horizon=args.horizon,
        metrics=metrics,
        params={
            "source":       "db",
            "min_history":  args.min_history,
            "eligible_skus": len(eligible),
            "fallback_skus": [
                {"sku_code": s["sku_code"],
                 "store_code": s["store_code"],
                 "history_days": s["history_days"],
                 "reason": s["reason"]}
                for s in ineligible
            ]
        }
    )
    print(f"Đã lưu {len(all_forecasts)} dòng dự báo")
    print(f"Khoảng dự báo: {forecast_start} → "
          f"{args.train_until} + {args.horizon} ngày")
```

**Luồng khi `--source csv` (backward compatible):**
```python
def main_csv(args):
    # Giống như trước, không thay đổi
    # Chỉ đảm bảo vẫn chạy được với sales_clean_long.csv M5
    pass
```

**Hàm `build_future_frame`:**
```python
def build_future_frame(eligible: list,
                       train_until: str,
                       horizon: int,
                       dsn: str,
                       label_encoders: dict) -> pd.DataFrame:
    """
    Tạo DataFrame khung ngày tương lai cho tất cả SKU đủ điều kiện.

    Với mỗi SKU:
    - Sinh horizon ngày từ train_until+1
    - Lấy đặc trưng lịch từ get_calendar_features()
    - Lấy giá bán mới nhất từ demand_history (forward-fill)
    - Lấy snap = 0 (không có dữ liệu tương lai)
    - Lấy 28 ngày lịch sử gần nhất để seed lag features
    """
```

**Hàm `fallback_forecast`:**
```python
def fallback_forecast(sku: dict,
                      dsn: str,
                      train_until: str,
                      horizon: int) -> pd.DataFrame:
    """
    Dự báo đơn giản cho SKU chưa đủ lịch sử.
    Dùng trung bình 7 ngày gần nhất (hoặc toàn bộ nếu ít hơn).

    p50 = mean(qty_sold trong 7 ngày gần nhất)
    p10 = max(0, p50 - std)
    p90 = p50 + std * 1.5

    Tất cả giá trị được làm tròn 2 chữ số thập phân.
    """
```

### File 3.4: Sửa `backend/core/forecaster.py`

```python
async def trigger_forecast_rerun():
    """
    Chạy lại pipeline ML với --source db.
    Dùng ngày cuối dữ liệu trong demand_history làm train_until.
    """
    latest_date = await get_latest_sale_date()
    if latest_date is None:
        return

    if not await should_retrain(latest_date):
        _write_audit("FORECAST_SKIP",
            {"reason": "run already exists",
             "trained_until": latest_date})
        return

    result = subprocess.run([
        "python", "ml/save_forecasts.py",
        "--dsn",         os.environ["DATABASE_URL"],
        "--mode",        "production",
        "--source",      "db",           # ← Thay đổi chính
        "--train-until", latest_date,
        "--horizon",     "28",
        "--min-history", "90",
    ], capture_output=True, text=True, timeout=300)

    if result.returncode == 0:
        _write_audit("FORECAST_RETRAIN", {
            "trained_until":   latest_date,
            "triggered_by":    "import_sales",
            "source":          "db",
            "stdout_tail":     result.stdout[-500:]
        })
    else:
        _write_audit("FORECAST_RETRAIN_FAILED", {
            "trained_until": latest_date,
            "stderr_tail":   result.stderr[-500:]
        })
```

### File 3.5: Tạo `ml/tests/test_data_loader.py`

```python
# test_load_from_db_returns_correct_columns:
#   load_from_db(dsn, "2016-05-22", min_history_days=90)
#   → DataFrame có cột: sku_code, store_code, sale_date,
#     qty_sold, sell_price, is_event, snap

# test_load_from_db_no_future_dates:
#   train_until = "2016-04-01"
#   → Không có dòng nào có sale_date > 2016-04-01

# test_get_eligible_skus_enough_history:
#   SKU có 100 ngày lịch sử, min_history=90 → eligible
#   SKU có 50 ngày lịch sử,  min_history=90 → ineligible
#   ineligible có trường "reason"

# test_get_calendar_features_any_date:
#   dates = ["2024-01-01", "2000-06-15", "1999-12-31"]
#   → Không lỗi, có đủ 8 cột đặc trưng
#   → dayofweek đúng (2024-01-01 là Monday = 0)

# test_load_from_csv_backward_compatible:
#   load_from_csv("./output") với sales_clean_long.csv M5
#   → DataFrame có >= 44000 dòng
#   → Không lỗi, cột chuẩn hóa đúng

# test_get_eligible_skus_matches_load:
#   eligible từ get_eligible_skus phải là tập con của
#   (product_id, location_id) có trong load_from_db output

# test_save_forecasts_source_db_integration:
#   Chạy: python ml/save_forecasts.py
#          --source db --train-until 2016-05-22
#          --min-history 90
#   → forecast_runs tăng thêm 1 run
#   → demand_forecasts tăng thêm 840 dòng (30 SKU × 28 ngày)
#   → Mọi dòng có p10 <= p50 <= p90

# test_fallback_forecast_new_sku:
#   Setup: Import SKU mới với 30 ngày lịch sử (< min_history=90)
#   Chạy save_forecasts.py --source db
#   → SKU mới có 28 dòng trong demand_forecasts
#   → forecast_runs.params có "fallback_skus" chứa SKU đó
#   → p50 > 0 (không phải 0 toàn bộ)

# test_import_then_forecast_new_date:
#   Import 10 dòng sales đến ngày 2016-06-10
#   trigger_forecast_rerun()
#   Chờ tối đa 30 giây
#   → forecast_runs có run với trained_until = 2016-06-10
#   → demand_forecasts có ngày 2016-06-11 đến 2016-07-08
```

---

## Quy trình thực hiện

Thực hiện đúng thứ tự sau. Mọi test hiện tại phải vẫn pass sau mỗi bước.

**Bước 1. Chuẩn hóa SQL**
```powershell
# Tìm file dùng :param
grep -rn ":\w\+" backend/ --include="*.py" | grep -v "http:" | grep -v "#"

# Sửa database.py, sau đó chạy lại toàn bộ test backend
python -m pytest backend/tests/ -v --timeout=120
# Phải vẫn pass tất cả
```

**Bước 2. Thêm test báo giá**
```powershell
python -m pytest backend/tests/test_import.py -v -k "quotes"
# Phải pass 3 test mới + mọi test quotes cũ
```

**Bước 3. Tạo `ml/data_loader.py`**
```powershell
python -m py_compile ml/data_loader.py && echo "OK"
# Test độc lập:
python -c "
from ml.data_loader import get_calendar_features
import pandas as pd
dates = pd.Series(['2024-01-01','2000-06-15'])
print(get_calendar_features(dates))
"
# Phải in ra DataFrame có 8 cột
```

**Bước 4. Sửa `ml/features.py`**
```powershell
# Chạy test features cũ để đảm bảo backward compatible
python -m pytest ml/tests/test_features.py -v
# Tất cả phải pass
```

**Bước 5. Sửa `ml/save_forecasts.py`**
```powershell
# Test backward compatible với CSV:
python ml/save_forecasts.py \
  --dsn "postgresql://app:<pw>@localhost:5432/procurement" \
  --source csv \
  --train-until 2016-05-22 \
  --horizon 28
# Phải tạo run mới trong forecast_runs

# Test với DB:
python ml/save_forecasts.py \
  --dsn "postgresql://app:<pw>@localhost:5432/procurement" \
  --source db \
  --train-until 2016-05-22 \
  --horizon 28 \
  --min-history 90
# Phải tạo run mới, demand_forecasts tăng 840 dòng
```

**Bước 6. Sửa `forecaster.py`**
```powershell
python -m py_compile backend/core/forecaster.py && echo "OK"
```

**Bước 7. Chạy toàn bộ test**
```powershell
# Test ML mới
python -m pytest ml/tests/test_data_loader.py -v --timeout=120

# Test backend không bị hỏng
python -m pytest backend/tests/ -v --timeout=120

# Kiểm tra DB sau khi test
docker compose exec db psql -U app -d procurement -c "
SELECT id, model_name, model_version,
       trained_until, horizon_days,
       jsonb_array_length(
         COALESCE(params->>'fallback_skus', '[]')::jsonb
       ) AS fallback_count,
       created_at
FROM forecast_runs
ORDER BY id DESC LIMIT 5;"
```

**Bước 8. Test luồng import → forecast hoàn chỉnh**
```powershell
# Lấy token ADMIN
$TOKEN = (curl -s -X POST http://localhost:8000/api/auth/login `
  -d "username=admin@poc.local&password=admin123" | ConvertFrom-Json).access_token

# Tải template và import 10 dòng sales đến 2016-06-10
# (sửa ngày trong file template trước khi import)
curl -X POST http://localhost:8000/api/import/sales `
  -H "Authorization: Bearer $TOKEN" `
  -F "file=@template_sales_june.csv"

# Chờ 30 giây để background task chạy xong
Start-Sleep -Seconds 30

# Kiểm tra forecast mới
curl http://localhost:8000/api/forecasts `
  -H "Authorization: Bearer $TOKEN"
# Kỳ vọng: có run với trained_until >= 2016-06-01
```

---

## Ràng buộc

- `--source csv` phải vẫn hoạt động y như trước (backward compatible).
- Mọi test hiện tại (45 test) phải vẫn pass sau khi thay đổi.
- Fallback forecast không được raise exception, chỉ ghi log.
- Không thay đổi schema database.
- Không dùng f-string hay .format() để ghép SQL.
- Label encoders phải được lưu cùng model file để dùng lại khi predict.
- SKU trong fallback_skus phải có đủ 28 dòng trong demand_forecasts.

---

## Mẫu báo cáo kết quả

```
TRẠNG THÁI: THÀNH CÔNG / THẤT BẠI

Phần 1 - Chuẩn hóa SQL:
  Số file sửa: ...
  Test backend sau khi sửa: X/45 pass
  Test SQL mới: X/2 pass

Phần 2 - Test báo giá một ngày:
  test_import_quotes_single_day_valid_to: đạt / không đạt
  test_import_quotes_replace_same_month: đạt / không đạt
  test_import_quotes_overlapping_months: đạt / không đạt

Phần 3 - Pipeline ML mở rộng:
  test_data_loader.py: X/9 pass
  --source csv (backward compat): đạt / không đạt
  --source db với train_until=2016-05-22: đạt / không đạt
    SKU đủ điều kiện: ...
    SKU dùng fallback: ...
    demand_forecasts tăng: ... dòng
  Luồng import → forecast ngày mới: đạt / không đạt
    trained_until sau import: ...
    Ngày đầu dự báo: ...
    Ngày cuối dự báo: ...

Vấn đề gặp phải và cách xử lý: ...
```
