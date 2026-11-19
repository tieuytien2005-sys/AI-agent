"""
Module tạo đặc trưng (Feature Engineering) cho mô hình dự báo nhu cầu.
Tuân thủ nguyên tắc nghiêm ngặt:
- KHÔNG rò rỉ dữ liệu tương lai (No Data Leakage).
- Mọi lag và rolling mean đều được shift hợp lệ (chỉ dùng dữ liệu t' < t).
- Tương thích dữ liệu từ cả PostgreSQL (demand_history) và CSV (sales_clean_long.csv).
"""

import os
import numpy as np
import pandas as pd


# Danh sách các đặc trưng chuẩn bị cho LightGBM
CATEGORICAL_FEATURES = ["store_id", "item_id", "cat_id", "dept_id", "dayofweek", "month"]
NUMERICAL_FEATURES = [
    "lag_7", "lag_14", "lag_28",
    "rolling_mean_7", "rolling_mean_28",
    "rolling_std_7", "rolling_std_28",
    "sell_price", "price_change_7d",
    "is_weekend", "is_event", "snap"
]
ALL_FEATURES = NUMERICAL_FEATURES + CATEGORICAL_FEATURES
TARGET_COL = "sales"

DB_FEATURE_COLS = [
    "lag_7",
    "lag_14",
    "lag_28",
    "rolling_mean_7",
    "rolling_mean_28",
    "rolling_std_7",
    "dayofweek",
    "month",
    "is_weekend",
    "is_month_start",
    "is_month_end",
    "quarter",
    "snap",
    "is_event",
    "sell_price",
    "sku_encoded",
    "store_encoded",
]

NON_FEATURE_COLS = [
    "sale_date",
    "qty_sold",
    "product_id",
    "location_id",
    "sku_code",
    "store_code",
    "cat_id",
    "category",
]


def load_raw_data(source: str = "csv", csv_path: str = "output/sales_clean_long.csv", dsn: str = None) -> pd.DataFrame:
    """
    Nạp dữ liệu chuỗi thời gian sạch từ CSV hoặc trực tiếp từ PostgreSQL.
    """
    if source == "db" and dsn:
        import psycopg
        query = """
            SELECT 
                dh.sale_date AS date,
                dh.qty_sold AS sales,
                dh.sell_price,
                dh.is_stockout::int AS is_stockout,
                dh.is_event::int AS is_event,
                p.sku_code AS item_id,
                p.category AS cat_id,
                SPLIT_PART(p.sku_code, '_', 1) || '_' || SPLIT_PART(p.sku_code, '_', 2) AS dept_id,
                l.code AS store_id,
                l.region AS state_id
            FROM demand_history dh
            JOIN products p ON dh.product_id = p.id
            JOIN locations l ON dh.location_id = l.id
            ORDER BY l.code, p.sku_code, dh.sale_date;
        """
        with psycopg.connect(dsn) as conn:
            df = pd.read_sql(query, conn)
        df["date"] = pd.to_datetime(df["date"])
        # Tạo d_idx dựa trên mốc d_1 = 2011-01-29
        base_date = pd.to_datetime("2011-01-29")
        df["d_idx"] = (df["date"] - base_date).dt.days + 1
        df["wday"] = df["date"].dt.dayofweek + 1
        df["snap"] = 0
        df["split"] = np.select(
            [df["d_idx"] <= 1885, df["d_idx"] <= 1913, df["d_idx"] <= 1941],
            ["train", "val", "test"], default="unused"
        )
        return df

    # Mặc định nạp từ CSV
    if not os.path.exists(csv_path):
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        csv_path = os.path.join(base_dir, csv_path)
    df = pd.read_csv(csv_path)
    df["date"] = pd.to_datetime(df["date"])
    return df


def create_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Tạo các đặc trưng chuỗi thời gian:
    1. Lag nhu cầu: 7, 14, 28 ngày
    2. Rolling means & std: 7 và 28 ngày (đã shift 1 ngày để không dùng chính ngày t)
    3. Calendar: dayofweek, is_weekend, month, day, is_event, snap
    4. Price: sell_price, price_change_7d
    """
    df = df.copy().sort_values(["store_id", "item_id", "date"]).reset_index(drop=True)

    # 1. Calendar Features
    df["dayofweek"] = df["date"].dt.dayofweek # 0=Monday, 6=Sunday
    df["is_weekend"] = df["dayofweek"].isin([5, 6]).astype(int)
    df["month"] = df["date"].dt.month
    df["day"] = df["date"].dt.day

    if "is_event" not in df.columns:
        if "event_name_1" in df.columns:
            df["is_event"] = df["event_name_1"].notna().astype(int)
        else:
            df["is_event"] = 0
    else:
        df["is_event"] = df["is_event"].astype(int)

    if "snap" not in df.columns:
        df["snap"] = 0
    else:
        df["snap"] = df["snap"].fillna(0).astype(int)

    # 2. Lag Features theo từng chuỗi (store_id, item_id)
    grouped = df.groupby(["store_id", "item_id"])

    df["lag_7"] = grouped["sales"].shift(7)
    df["lag_14"] = grouped["sales"].shift(14)
    df["lag_28"] = grouped["sales"].shift(28)

    # 3. Rolling Features (Shift 1 ngày để chống rò rỉ thông tin ngày hiện tại)
    # rolling(7) của shift(1) = mean(sales[t-7 : t-1])
    sales_shifted = grouped["sales"].shift(1)
    df["rolling_mean_7"] = sales_shifted.groupby([df["store_id"], df["item_id"]]).transform(
        lambda x: x.rolling(7, min_periods=1).mean()
    )
    df["rolling_mean_28"] = sales_shifted.groupby([df["store_id"], df["item_id"]]).transform(
        lambda x: x.rolling(28, min_periods=1).mean()
    )
    df["rolling_std_7"] = sales_shifted.groupby([df["store_id"], df["item_id"]]).transform(
        lambda x: x.rolling(7, min_periods=1).std().fillna(0.0)
    )
    df["rolling_std_28"] = sales_shifted.groupby([df["store_id"], df["item_id"]]).transform(
        lambda x: x.rolling(28, min_periods=1).std().fillna(0.0)
    )

    # 4. Price Features
    if "sell_price" in df.columns:
        df["sell_price"] = df["sell_price"].ffill().bfill()
        price_lag_7 = grouped["sell_price"].shift(7)
        # Tỷ lệ thay đổi giá 7 ngày: (price_t - price_{t-7}) / price_{t-7}
        df["price_change_7d"] = (df["sell_price"] - price_lag_7) / price_lag_7
        df["price_change_7d"] = df["price_change_7d"].fillna(0.0).replace([np.inf, -np.inf], 0.0)
    else:
        df["sell_price"] = 1.0
        df["price_change_7d"] = 0.0

    # Ép kiểu dữ liệu danh mục Categorical
    for col in CATEGORICAL_FEATURES:
        if col in df.columns:
            df[col] = df[col].astype("category")

    return df


def get_splits(df_feat: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Tách dữ liệu thành 3 tập: Train, Validation, Test theo đúng chỉ số d_idx.
    - Train: d_idx <= 1885 (kết thúc 2016-03-27)
    - Val: 1886 <= d_idx <= 1913 (28 ngày: 2016-03-28 đến 2016-04-24)
    - Test: 1914 <= d_idx <= 1941 (28 ngày: 2016-04-25 đến 2016-05-22)
    """
    train = df_feat[df_feat["split"] == "train"].dropna(subset=["lag_28"]).copy()
    val = df_feat[df_feat["split"] == "val"].copy()
    test = df_feat[df_feat["split"] == "test"].copy()
    return train, val, test


def build_features(
    df: pd.DataFrame,
    mode: str = "train",
    label_encoders: dict = None,
) -> tuple[pd.DataFrame, dict]:
    """
    Tạo đặc trưng cho LightGBM (độc lập với M5, dùng được với dữ liệu DB bất kỳ).

    mode='train':
      - Tạo label_encoders cho sku_code và store_code
      - Trả về (df_features, label_encoders)

    mode='predict':
      - Dùng label_encoders đã có (truyền vào)
      - SKU/store chưa thấy khi train → encode thành -1
      - Trả về (df_features, label_encoders)
    """
    from ml.data_loader import get_calendar_features

    out = df.copy()
    out["sale_date"] = pd.to_datetime(out["sale_date"])
    out = out.sort_values(["store_code", "sku_code", "sale_date"]).reset_index(drop=True)

    grouped = out.groupby(["store_code", "sku_code"])
    out["lag_7"] = grouped["qty_sold"].shift(7)
    out["lag_14"] = grouped["qty_sold"].shift(14)
    out["lag_28"] = grouped["qty_sold"].shift(28)

    qty_shifted = grouped["qty_sold"].shift(1)
    out["rolling_mean_7"] = qty_shifted.groupby([out["store_code"], out["sku_code"]]).transform(
        lambda x: x.rolling(7, min_periods=1).mean()
    )
    out["rolling_mean_28"] = qty_shifted.groupby([out["store_code"], out["sku_code"]]).transform(
        lambda x: x.rolling(28, min_periods=1).mean()
    )
    out["rolling_std_7"] = qty_shifted.groupby([out["store_code"], out["sku_code"]]).transform(
        lambda x: x.rolling(7, min_periods=1).std().fillna(0.0)
    )

    cal = get_calendar_features(out["sale_date"])
    for col in ("dayofweek", "month", "is_weekend", "is_month_start", "is_month_end", "quarter"):
        out[col] = cal[col].values

    if "snap" in out.columns:
        out["snap"] = out["snap"].fillna(0).astype(int)
    else:
        out["snap"] = 0

    if "is_event" in out.columns:
        out["is_event"] = out["is_event"].fillna(0).astype(int)
    else:
        out["is_event"] = 0

    if "sell_price" in out.columns:
        out["sell_price"] = pd.to_numeric(out["sell_price"], errors="coerce")
        out["sell_price"] = out.groupby(["store_code", "sku_code"])["sell_price"].transform(
            lambda x: x.ffill().bfill()
        )
        out["sell_price"] = out["sell_price"].fillna(1.0).astype(float)
    else:
        out["sell_price"] = 1.0

    if mode == "train" or label_encoders is None:
        sku_list = sorted(out["sku_code"].astype(str).unique())
        store_list = sorted(out["store_code"].astype(str).unique())
        label_encoders = {
            "sku_code": {val: idx for idx, val in enumerate(sku_list)},
            "store_code": {val: idx for idx, val in enumerate(store_list)},
        }

    sku_map = label_encoders.get("sku_code", {})
    store_map = label_encoders.get("store_code", {})
    out["sku_encoded"] = out["sku_code"].astype(str).map(lambda x: sku_map.get(x, -1)).astype(int)
    out["store_encoded"] = out["store_code"].astype(str).map(lambda x: store_map.get(x, -1)).astype(int)

    return out, label_encoders
