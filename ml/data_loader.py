"""
Module đọc dữ liệu cho pipeline ML.
Hai nguồn: CSV (M5, để benchmark) và DB (production).
"""

import os
from typing import List, Optional, Tuple
import pandas as pd
import psycopg
from psycopg.rows import dict_row

REQUIRED_COLS_CSV = [
    "item_id",
    "store_id",
    "date",
    "sales",
    "sell_price",
    "event_name_1",
    "snap",
    "cat_id",
]

REQUIRED_COLS_DB = [
    "product_id",
    "location_id",
    "sale_date",
    "qty_sold",
    "sell_price",
    "is_event",
    "snap",
]


def _clean_dsn(dsn: Optional[str] = None) -> str:
    url = dsn or os.environ.get("DATABASE_URL") or "postgresql://app:postgreSQL123@localhost:5432/procurement"
    if url.startswith("postgresql+psycopg://"):
        url = url.replace("postgresql+psycopg://", "postgresql://", 1)
    return url


def load_from_csv(clean_dir: str = "./output", skus: Optional[List[str]] = None) -> pd.DataFrame:
    """
    Đọc từ sales_clean_long.csv (30 SKU M5).
    Dùng cho benchmark hoặc môi trường phát triển.

    Chuẩn hóa output:
      - Đổi tên cột: sales → qty_sold, date → sale_date,
        event_name_1 → is_event (bool), item_id → sku_code,
        store_id → store_code
      - Thêm product_id và location_id = None
      - Giữ cat_id và dept_id nếu có

    skus: list of "item_id_store_id" để lọc, None = tất cả
    """
    if os.path.isdir(clean_dir):
        csv_path = os.path.join(clean_dir, "sales_clean_long.csv")
    else:
        csv_path = clean_dir

    if not os.path.exists(csv_path):
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        csv_path = os.path.join(base_dir, clean_dir, "sales_clean_long.csv")

    df = pd.read_csv(csv_path)

    if skus:
        sku_set = set(skus)
        combined_key = df["item_id"].astype(str) + "_" + df["store_id"].astype(str)
        df = df[combined_key.isin(sku_set) | df["item_id"].isin(sku_set)].copy()

    if "event_name_1" in df.columns:
        df["is_event"] = df["event_name_1"].notna() & (df["event_name_1"].astype(str).str.strip() != "")
    elif "is_event" in df.columns:
        df["is_event"] = df["is_event"].fillna(0).astype(bool)
    else:
        df["is_event"] = False

    if "snap" not in df.columns:
        df["snap"] = 0
    else:
        df["snap"] = df["snap"].fillna(0).astype(int)

    df = df.rename(
        columns={
            "sales": "qty_sold",
            "date": "sale_date",
            "item_id": "sku_code",
            "store_id": "store_code",
        }
    )
    df["sale_date"] = pd.to_datetime(df["sale_date"])
    df["product_id"] = None
    df["location_id"] = None
    return df


def get_eligible_skus(
    dsn: str,
    train_until: str,
    min_history_days: int = 90,
) -> Tuple[List[dict], List[dict]]:
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
        ORDER BY p.id, l.id
    """
    clean_url = _clean_dsn(dsn)
    with psycopg.connect(clean_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, {"train_until": train_until})
            rows = cur.fetchall()

    eligible: List[dict] = []
    ineligible: List[dict] = []
    for r in rows:
        h_days = int(r["history_days"])
        item = {
            "product_id": int(r["product_id"]),
            "location_id": int(r["location_id"]),
            "sku_code": r["sku_code"],
            "store_code": r["store_code"],
            "history_days": h_days,
        }
        if h_days >= min_history_days:
            eligible.append(item)
        else:
            item["reason"] = f"Chỉ có {h_days} ngày lịch sử (< {min_history_days} ngày tối thiểu)"
            ineligible.append(item)

    return eligible, ineligible


def load_from_db(
    dsn: str,
    train_until: str,
    min_history_days: int = 90,
) -> Tuple[pd.DataFrame, List[dict]]:
    """
    Đọc từ bảng demand_history trong PostgreSQL.
    Dùng khi source=db (production).

    Trả về: (df: pd.DataFrame, skipped_skus: list[dict])
    """
    eligible, ineligible = get_eligible_skus(dsn, train_until, min_history_days)
    eligible_pairs = {(e["product_id"], e["location_id"]) for e in eligible}

    sql = """
        SELECT
            p.sku_code,     l.code AS store_code,
            p.category,     dh.product_id, dh.location_id,
            dh.sale_date,   dh.qty_sold,
            dh.sell_price,  dh.is_event,  0 AS snap
        FROM demand_history dh
        JOIN products  p ON p.id = dh.product_id
        JOIN locations l ON l.id = dh.location_id
        WHERE dh.sale_date <= %(train_until)s::date
        ORDER BY dh.product_id, dh.location_id, dh.sale_date
    """
    clean_url = _clean_dsn(dsn)
    with psycopg.connect(clean_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, {"train_until": train_until})
            rows = cur.fetchall()

    if not rows:
        empty_df = pd.DataFrame(
            columns=[
                "sku_code",
                "store_code",
                "category",
                "product_id",
                "location_id",
                "sale_date",
                "qty_sold",
                "sell_price",
                "is_event",
                "snap",
            ]
        )
        return empty_df, ineligible

    df = pd.DataFrame(rows)
    df["sale_date"] = pd.to_datetime(df["sale_date"])
    df["qty_sold"] = df["qty_sold"].astype(float)
    df["sell_price"] = pd.to_numeric(df["sell_price"], errors="coerce")

    if eligible_pairs:
        mask = df.apply(lambda r: (int(r["product_id"]), int(r["location_id"])) in eligible_pairs, axis=1)
        df = df[mask].reset_index(drop=True)
    else:
        df = df.iloc[0:0].copy()

    return df, ineligible


def get_calendar_features(dates: pd.Series) -> pd.DataFrame:
    """
    Sinh đặc trưng lịch từ cột ngày.
    KHÔNG phụ thuộc vào file calendar.csv của M5.
    Hoạt động với bất kỳ ngày nào từ 2000 trở đi (và trước đó).

    Trả về DataFrame với các cột:
      dayofweek (0=Monday..6=Sunday)
      month (1-12)
      year
      day (1-31)
      is_weekend (int/bool)
      is_month_start (int/bool)
      is_month_end (int/bool)
      quarter (1-4)
    """
    if isinstance(dates, pd.Series):
        s = pd.to_datetime(dates)
        df = pd.DataFrame({"date": s}, index=dates.index)
    else:
        s = pd.Series(pd.to_datetime(dates))
        df = pd.DataFrame({"date": s})

    df["dayofweek"] = df["date"].dt.dayofweek
    df["month"] = df["date"].dt.month
    df["year"] = df["date"].dt.year
    df["day"] = df["date"].dt.day
    df["is_weekend"] = df["dayofweek"].isin([5, 6]).astype(int)
    df["is_month_start"] = df["date"].dt.is_month_start.astype(int)
    df["is_month_end"] = df["date"].dt.is_month_end.astype(int)
    df["quarter"] = df["date"].dt.quarter
    return df.drop(columns=["date"])
