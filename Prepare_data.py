"""
Chuẩn bị dữ liệu M5 cho PoC AI Procurement Agent.

Đầu ra (thư mục OUT_DIR):
  - selected_skus.csv         : danh sách SKU/cửa hàng được chọn kèm thống kê
  - sales_clean_long.csv      : dữ liệu dạng dài, mỗi dòng là (chuỗi, ngày)
  - data_report.txt           : báo cáo lọc (số chuỗi bị loại theo từng lý do)

Cách dùng:
  python prepare_data.py --data-dir /mnt/user-data/uploads --out-dir ./output
"""

import argparse
import os
import numpy as np
import pandas as pd

SEED = 42

# Ngưỡng lọc (có thể chỉnh qua tham số dòng lệnh)
MIN_HISTORY_DAYS = 730          # số ngày tối thiểu kể từ lần bán đầu tiên
MAX_ZERO_SHARE_RECENT = 0.30    # tỷ lệ số 0 tối đa trong 365 ngày gần nhất
MAX_INTERNAL_ZERO_RUN = 14      # đoạn 0 liên tiếp tối đa sau lần bán đầu
STOCKOUT_RUN = 28               # đoạn 0 >= ngưỡng này được coi là hết hàng/ngừng bán

# Phân chia thời gian (theo chỉ số ngày)
TRAIN_END = 1885                # train: d_1 .. d_1885
VAL_END = 1913                  # val: d_1886 .. d_1913
TEST_END = 1941                 # test: d_1914 .. d_1941 (lấy từ file evaluation)


def day_index(col: str) -> int:
    return int(col.split("_")[1])


def zero_runs(arr: np.ndarray):
    """Trả về danh sách (start, length) của các đoạn 0 liên tiếp."""
    runs, start, length = [], None, 0
    for i, v in enumerate(arr):
        if v == 0:
            if start is None:
                start = i
            length += 1
        else:
            if start is not None:
                runs.append((start, length))
                start, length = None, 0
    if start is not None:
        runs.append((start, length))
    return runs


def load_inputs(data_dir: str):
    cal = pd.read_csv(os.path.join(data_dir, "calendar.csv"))
    cal["d_idx"] = cal["d"].map(day_index)
    # Dùng file evaluation vì nó chứa d_1..d_1941 (bao gồm cả tập validation)
    sales = pd.read_csv(os.path.join(data_dir, "sales_train_evaluation.csv"))
    prices = pd.read_csv(os.path.join(data_dir, "sell_prices.csv"))
    return cal, sales, prices


def add_base_id(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["series_id"] = df["id"].str.replace(r"_(validation|evaluation)$", "", regex=True)
    return df


def analyse_series(sales: pd.DataFrame, day_cols: list):
    """Tính thống kê cho từng chuỗi và gắn cờ đủ điều kiện."""
    X = sales[day_cols].to_numpy(dtype=float)
    n_days = X.shape[1]
    rows = []
    for i in range(X.shape[0]):
        r = X[i]
        nz = np.flatnonzero(r)
        if nz.size == 0:
            rows.append({"first_sale": -1, "history_days": 0, "zero_share_recent": 1.0,
                         "max_internal_zero_run": 0, "n_stockout_runs": 0})
            continue
        first = nz[0]
        tail = r[first:]
        runs = zero_runs(tail)
        internal = [l for s, l in runs if s + l < len(tail)]  # bỏ đoạn 0 ở cuối (chưa biết)
        recent = r[max(0, n_days - 365):]
        rows.append({
            "first_sale": first,
            "history_days": n_days - first,
            "zero_share_recent": float((recent == 0).mean()),
            "max_internal_zero_run": max(internal) if internal else 0,
            "n_stockout_runs": sum(1 for l in internal if l >= STOCKOUT_RUN),
        })
    stats = pd.DataFrame(rows, index=sales.index)
    return pd.concat([sales[["id", "series_id", "item_id", "dept_id", "cat_id", "store_id", "state_id"]],
                      stats], axis=1)


def apply_filters(stats: pd.DataFrame, stores: list, report: list):
    s = stats[stats["store_id"].isin(stores)].copy()
    report.append(f"Số chuỗi thuộc cửa hàng {stores}: {len(s)}")

    cond_history = s["history_days"] >= MIN_HISTORY_DAYS
    cond_recent = s["zero_share_recent"] <= MAX_ZERO_SHARE_RECENT
    cond_gap = s["max_internal_zero_run"] < MAX_INTERNAL_ZERO_RUN

    report.append(f"  Loại vì lịch sử < {MIN_HISTORY_DAYS} ngày: {(~cond_history).sum()}")
    report.append(f"  Loại vì tỷ lệ 0 gần đây > {MAX_ZERO_SHARE_RECENT}: "
                  f"{(cond_history & ~cond_recent).sum()}")
    report.append(f"  Loại vì có đoạn 0 >= {MAX_INTERNAL_ZERO_RUN} ngày: "
                  f"{(cond_history & cond_recent & ~cond_gap).sum()}")

    eligible = s[cond_history & cond_recent & cond_gap].copy()
    report.append(f"Số chuỗi đủ điều kiện: {len(eligible)}")
    return eligible


def select_skus(eligible: pd.DataFrame, n_per_cat: dict, report: list):
    """Lấy mẫu ngẫu nhiên có phân tầng theo nhóm hàng để giữ cân bằng."""
    chosen = []
    rng = np.random.default_rng(SEED)
    for cat, n in n_per_cat.items():
        pool = eligible[eligible["cat_id"] == cat]
        if len(pool) < n:
            report.append(f"CẢNH BÁO: nhóm {cat} chỉ có {len(pool)} chuỗi đủ điều kiện, cần {n}")
            n = len(pool)
        idx = rng.choice(pool.index.to_numpy(), size=n, replace=False)
        chosen.append(eligible.loc[idx])
    out = pd.concat(chosen).sort_values(["cat_id", "store_id", "item_id"]).reset_index(drop=True)
    report.append(f"Số chuỗi được chọn cho PoC: {len(out)}")
    return out


def build_long_table(selected: pd.DataFrame, sales: pd.DataFrame, day_cols: list,
                     cal: pd.DataFrame, prices: pd.DataFrame, report: list):
    # Lấy ma trận doanh số của các chuỗi được chọn
    sel_ids = selected["id"].tolist()
    mat = sales.set_index("id").loc[sel_ids, day_cols]
    mat.columns = [day_index(c) for c in day_cols]

    # Chuyển sang dạng dài
    long = (mat.stack().rename("sales").reset_index()
            .rename(columns={"level_0": "id", "level_1": "d_idx"}))
    long = long.merge(selected[["id", "series_id", "item_id", "dept_id", "cat_id",
                                "store_id", "state_id"]], on="id", how="left")
    long["d_idx"] = long["d_idx"].astype(int)

    # Ghép lịch
    cal_cols = ["d_idx", "date", "wm_yr_wk", "wday", "month", "year",
                "event_name_1", "event_type_1", "event_name_2", "event_type_2",
                "snap_CA", "snap_TX", "snap_WI"]
    long = long.merge(cal[cal_cols], on="d_idx", how="left")
    long["snap"] = long.apply(lambda r: r[f"snap_{r['state_id']}"], axis=1)
    long = long.drop(columns=["snap_CA", "snap_TX", "snap_WI"])

    # Ghép giá bán (theo cửa hàng, sản phẩm, tuần)
    keys = selected[["store_id", "item_id"]].drop_duplicates()
    p = prices.merge(keys, on=["store_id", "item_id"], how="inner")
    long = long.merge(p, on=["store_id", "item_id", "wm_yr_wk"], how="left")
    report.append(f"Tỷ lệ dòng có giá bán: {long['sell_price'].notna().mean():.3f}")

    # Gắn cờ hết hàng: đoạn 0 dài >= STOCKOUT_RUN sau lần bán đầu -> không phải nhu cầu thật
    long = long.sort_values(["id", "d_idx"]).reset_index(drop=True)
    long["is_stockout"] = 0
    for sid, grp in long.groupby("id", sort=False):
        arr = grp["sales"].to_numpy()
        nz = np.flatnonzero(arr)
        if nz.size == 0:
            continue
        first = nz[0]
        flag = np.zeros(len(arr), dtype=int)
        for s, l in zero_runs(arr[first:]):
            abs_start = first + s
            if s + l < len(arr) - first and l >= STOCKOUT_RUN:
                flag[abs_start:abs_start + l] = 1
        long.loc[grp.index, "is_stockout"] = flag

    # Loại các ngày trước lần bán đầu (chưa mở bán)
    first_day = long[long["sales"] > 0].groupby("id")["d_idx"].min().rename("first_sale_d")
    long = long.merge(first_day, on="id", how="left")
    before = len(long)
    long = long[long["d_idx"] >= long["first_sale_d"]].drop(columns=["first_sale_d"])
    report.append(f"Đã bỏ {before - len(long)} dòng trước ngày mở bán")

    # Gắn nhãn tập dữ liệu theo thời gian
    long["split"] = np.select(
        [long["d_idx"] <= TRAIN_END, long["d_idx"] <= VAL_END, long["d_idx"] <= TEST_END],
        ["train", "val", "test"], default="unused")
    return long


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=r"C:\Users\Nhat Thanh\Downloads\m5-forecasting-accuracy")
    ap.add_argument("--out-dir", default=r"./output")
    ap.add_argument("--stores", nargs="+", default=["CA_1", "TX_1"])
    ap.add_argument("--n-foods", type=int, default=12)
    ap.add_argument("--n-household", type=int, default=9)
    ap.add_argument("--n-hobbies", type=int, default=9)
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    report = []

    print("Đang đọc dữ liệu...")
    cal, sales, prices = load_inputs(args.data_dir)
    sales = add_base_id(sales)
    day_cols = [c for c in sales.columns if c.startswith("d_")]
    day_cols = [c for c in day_cols if day_index(c) <= TEST_END]
    report.append(f"Số ngày sử dụng: {len(day_cols)} (d_1 đến d_{TEST_END})")

    print("Đang phân tích từng chuỗi...")
    stats = analyse_series(sales, day_cols)

    print("Đang lọc chuỗi...")
    eligible = apply_filters(stats, args.stores, report)

    print("Đang chọn SKU...")
    n_per_cat = {"FOODS": args.n_foods, "HOUSEHOLD": args.n_household, "HOBBIES": args.n_hobbies}
    selected = select_skus(eligible, n_per_cat, report)
    selected.to_csv(os.path.join(args.out_dir, "selected_skus.csv"), index=False)

    print("Đang tạo bảng dạng dài...")
    long = build_long_table(selected, sales, day_cols, cal, prices, report)
    long.to_csv(os.path.join(args.out_dir, "sales_clean_long.csv"), index=False)

    report.append(f"Tổng số dòng đầu ra: {len(long)}")
    report.append("Số dòng theo tập: " + str(long["split"].value_counts().to_dict()))
    report.append(f"Số dòng đánh dấu hết hàng: {int(long['is_stockout'].sum())}")

    with open(os.path.join(args.out_dir, "data_report.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    print("\n".join(report))
    print(f"\nĐã lưu kết quả vào {args.out_dir}")


if __name__ == "__main__":
    main()