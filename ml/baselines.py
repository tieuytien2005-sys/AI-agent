"""
Module triển khai 4 mô hình Baseline dự báo nhu cầu và các hàm đánh giá chỉ số:
- WAPE (Weighted Absolute Percentage Error)
- RMSSE (Root Mean Squared Scaled Error - chuẩn M5)
- MAE (Mean Absolute Error)
- Bias (Mean Error = mean(y - ŷ))

4 Baseline:
1. Naive: Lặp lại nhu cầu ngày hôm qua cho toàn bộ kỳ dự báo
2. MA-7: Trung bình trượt 7 ngày gần nhất
3. MA-28: Trung bình trượt 28 ngày gần nhất
4. DOW (Day-of-Week): Trung bình các ngày cùng thứ trong 8 tuần gần nhất
"""

import numpy as np
import pandas as pd


# ==============================================================================
# 1. CÁC HÀM TÍNH ĐỘ ĐO (METRICS)
# ==============================================================================

def wape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """
    Tính WAPE = sum(|y - ŷ|) / sum(y).
    Tránh lỗi chia cho 0 khi từng ngày riêng lẻ bằng 0.
    """
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    denominator = np.sum(y_true)
    if denominator == 0:
        return 0.0 if np.sum(np.abs(y_pred)) == 0 else 1.0
    return float(np.sum(np.abs(y_true - y_pred)) / denominator)


def rmsse(y_true: np.ndarray, y_pred: np.ndarray, y_train: np.ndarray) -> float:
    """
    Tính RMSSE (Root Mean Squared Scaled Error - chuẩn cuộc thi Walmart M5):
      Tử số: MSE của dự báo trên kỳ horizon H
      Mẫu số: MSE của 1-step naive forecast trên tập train trong quá khứ
    """
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    y_train = np.asarray(y_train, dtype=float)

    h = len(y_true)
    if h == 0:
        return 0.0

    numerator = np.mean((y_true - y_pred) ** 2)

    # Tính mẫu số: trung bình bình phương sai phân bậc 1 trên tập train
    diffs = np.diff(y_train)
    if len(diffs) == 0:
        return 0.0
    denominator = np.mean(diffs ** 2)

    if denominator == 0:
        # Nếu chuỗi train hoàn toàn phẳng
        return float(np.sqrt(numerator)) if numerator > 0 else 0.0

    return float(np.sqrt(numerator / denominator))


def mae(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Tính MAE = mean(|y - ŷ|)."""
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    return float(np.mean(np.abs(y_true - y_pred)))


def bias(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """
    Tính Bias = mean(y - ŷ).
    - Bias > 0: Mô hình dự báo thấp hơn thực tế (dưới mức bán / under-forecasting).
    - Bias < 0: Mô hình dự báo cao hơn thực tế (vượt mức bán / over-forecasting).
    """
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    return float(np.mean(y_true - y_pred))


def evaluate_forecast(y_true: np.ndarray, y_pred: np.ndarray, y_train: np.ndarray) -> dict:
    """Tổng hợp 4 chỉ số đánh giá cho một chuỗi."""
    return {
        "WAPE": wape(y_true, y_pred),
        "RMSSE": rmsse(y_true, y_pred, y_train),
        "MAE": mae(y_true, y_pred),
        "Bias": bias(y_true, y_pred),
    }


# ==============================================================================
# 2. BỐN MÔ HÌNH DỰ BÁO BASELINE
# ==============================================================================

def predict_naive(y_train: np.ndarray, horizon: int = 28) -> np.ndarray:
    """
    Baseline 1: Naive (Yesterday)
    Lấy giá trị nhu cầu của ngày cuối cùng trong tập train cho tất cả ngày trong horizon.
    """
    last_val = float(y_train[-1]) if len(y_train) > 0 else 0.0
    return np.full(horizon, max(0.0, last_val))


def predict_ma7(y_train: np.ndarray, horizon: int = 28) -> np.ndarray:
    """
    Baseline 2: Trung bình trượt 7 ngày gần nhất
    """
    window = y_train[-7:] if len(y_train) >= 7 else y_train
    mean_val = float(np.mean(window)) if len(window) > 0 else 0.0
    return np.full(horizon, max(0.0, mean_val))


def predict_ma28(y_train: np.ndarray, horizon: int = 28) -> np.ndarray:
    """
    Baseline 3: Trung bình trượt 28 ngày gần nhất
    """
    window = y_train[-28:] if len(y_train) >= 28 else y_train
    mean_val = float(np.mean(window)) if len(window) > 0 else 0.0
    return np.full(horizon, max(0.0, mean_val))


def predict_dow(train_series: pd.DataFrame, val_series: pd.DataFrame, weeks: int = 8) -> np.ndarray:
    """
    Baseline 4: Trung bình theo thứ trong 8 tuần gần nhất
    Với mỗi ngày trong tập validation, lấy trung bình các ngày cùng thứ
    trong 8 tuần (56 ngày) gần nhất của tập train.
    """
    horizon = len(val_series)
    # Lấy tối đa 8 tuần gần nhất của tập train
    recent_train = train_series.tail(weeks * 7).copy()
    
    # Tính trung bình theo thứ (dayofweek hoặc wday)
    dow_means = recent_train.groupby("wday")["sales"].mean().to_dict()
    overall_mean = float(recent_train["sales"].mean()) if len(recent_train) > 0 else 0.0

    preds = []
    for _, r in val_series.iterrows():
        w = r["wday"]
        preds.append(max(0.0, dow_means.get(w, overall_mean)))
    return np.array(preds, dtype=float)


# ==============================================================================
# 3. ĐÁNH GIÁ TỔNG HỢP TRÊN TẬP VALIDATION
# ==============================================================================

BASELINE_FUNCS = {
    "Naive": predict_naive,
    "MA-7": predict_ma7,
    "MA-28": predict_ma28,
    "DOW": predict_dow,
}


def run_all_baselines(df: pd.DataFrame, horizon: int = 28) -> tuple[pd.DataFrame, dict]:
    """
    Chạy và đánh giá cả 4 baseline trên toàn bộ 30 SKU của tập validation.
    Trả về:
      - DataFrame bảng tổng kết metrics theo mô hình (Overall, và theo nhóm hàng)
      - Dictionary chứa kết quả chi tiết từng chuỗi để phân tích sâu
    """
    train_df = df[df["split"] == "train"].copy()
    val_df = df[df["split"] == "val"].copy()

    unique_series = df[["item_id", "store_id", "cat_id"]].drop_duplicates()
    
    records = []
    detailed_results = {m: [] for m in BASELINE_FUNCS}

    for model_name, func in BASELINE_FUNCS.items():
        all_true = []
        all_pred = []
        all_train_scale = []
        
        cat_metrics = {}

        for _, s in unique_series.iterrows():
            item, store, cat = s["item_id"], s["store_id"], s["cat_id"]
            tr = train_df[(train_df["item_id"] == item) & (train_df["store_id"] == store)].sort_values("d_idx")
            vl = val_df[(val_df["item_id"] == item) & (val_df["store_id"] == store)].sort_values("d_idx")

            if len(vl) == 0:
                continue

            y_train = tr["sales"].to_numpy(dtype=float)
            y_val = vl["sales"].to_numpy(dtype=float)

            if model_name == "DOW":
                y_pred = func(tr, vl, weeks=8)
            else:
                y_pred = func(y_train, horizon=len(y_val))

            all_true.extend(y_val)
            all_pred.extend(y_pred)
            
            # Tính RMSSE cho từng chuỗi
            series_rmsse = rmsse(y_val, y_pred, y_train)
            series_wape = wape(y_val, y_pred)
            series_mae = mae(y_val, y_pred)
            series_bias = bias(y_val, y_pred)

            detailed_results[model_name].append({
                "item_id": item,
                "store_id": store,
                "cat_id": cat,
                "wape": series_wape,
                "rmsse": series_rmsse,
                "mae": series_mae,
                "bias": series_bias,
                "y_true": y_val,
                "y_pred": y_pred,
            })

        all_true = np.array(all_true)
        all_pred = np.array(all_pred)

        # Tính WAPE toàn cục (tổng lỗi / tổng thật trên cả 30 SKU)
        overall_wape = wape(all_true, all_pred)
        overall_mae = mae(all_true, all_pred)
        overall_bias = bias(all_true, all_pred)
        # RMSSE trung bình của 30 SKU
        overall_rmsse = float(np.mean([r["rmsse"] for r in detailed_results[model_name]]))

        row = {
            "Mô hình": model_name,
            "WAPE Toàn cục": round(overall_wape, 4),
            "RMSSE TB": round(overall_rmsse, 4),
            "MAE": round(overall_mae, 4),
            "Bias (y - ŷ)": round(overall_bias, 4),
        }

        # Tính theo từng nhóm hàng
        for cat in ["FOODS", "HOUSEHOLD", "HOBBIES"]:
            cat_items = [r for r in detailed_results[model_name] if r["cat_id"] == cat]
            c_true = np.concatenate([r["y_true"] for r in cat_items])
            c_pred = np.concatenate([r["y_pred"] for r in cat_items])
            row[f"WAPE {cat}"] = round(wape(c_true, c_pred), 4)
            row[f"RMSSE {cat}"] = round(float(np.mean([r["rmsse"] for r in cat_items])), 4)

        records.append(row)

    summary_df = pd.DataFrame(records).sort_values("WAPE Toàn cục")
    return summary_df, detailed_results


def main():
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        
    print("=== Đang nạp dữ liệu và đánh giá 4 Baseline trên tập Validation ===")
    from ml.features import load_raw_data
    df = load_raw_data(source="csv")
    summary_df, detailed = run_all_baselines(df, horizon=28)
    
    print("\n" + "=" * 80)
    print("BẢNG SO SÁNH HIỆU NĂNG 4 BASELINE TRÊN TẬP VALIDATION (28 NGÀY)")
    print("=" * 80)
    print(summary_df.to_string(index=False))
    print("=" * 80)
    best_model = summary_df.iloc[0]["Mô hình"]
    best_wape = summary_df.iloc[0]["WAPE Toàn cục"]
    best_rmsse = summary_df.iloc[0]["RMSSE TB"]
    print(f"\n>>> Baseline tốt nhất: [{best_model}] với WAPE={best_wape:.4f}, RMSSE={best_rmsse:.4f} <<<")


if __name__ == "__main__":
    main()

