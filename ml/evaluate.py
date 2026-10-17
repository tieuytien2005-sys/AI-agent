"""
Module đánh giá toàn diện và so sánh các mô hình:
- 4 Mô hình Baseline (Naive, MA-7, MA-28, DOW)
- 1 Mô hình Toàn cục LightGBM (Global LightGBM)
Xuất bảng so sánh tổng hợp các chỉ số WAPE, RMSSE, MAE, Bias toàn cục và theo từng nhóm hàng.
"""

import os
import sys
import numpy as np
import pandas as pd
import lightgbm as lgb

from ml.features import load_raw_data, create_features, get_splits, ALL_FEATURES
from ml.baselines import wape, rmsse, mae, bias, run_all_baselines
from ml.train_lgbm import recursive_forecast


def evaluate_lgbm(model, train_df: pd.DataFrame, eval_df: pd.DataFrame, eval_name: str = "Validation") -> tuple[dict, list]:
    """
    Chạy dự báo và tính toán đầy đủ các chỉ số của LightGBM trên tập eval_df (Validation hoặc Test).
    """
    preds_df = recursive_forecast(model, train_df, eval_df, horizon=len(eval_df["date"].unique()))
    preds_df = preds_df.sort_values(["store_id", "item_id", "date"]).reset_index(drop=True)
    eval_sorted = eval_df.sort_values(["store_id", "item_id", "date"]).reset_index(drop=True)

    y_true_all = eval_sorted["sales"].to_numpy(dtype=float)
    y_pred_all = preds_df["pred_sales"].to_numpy(dtype=float)

    unique_series = eval_sorted[["item_id", "store_id", "cat_id"]].drop_duplicates()
    
    detailed = []
    for _, s in unique_series.iterrows():
        item, store, cat = s["item_id"], s["store_id"], s["cat_id"]
        tr = train_df[(train_df["item_id"] == item) & (train_df["store_id"] == store)].sort_values("d_idx")
        vl = eval_sorted[(eval_sorted["item_id"] == item) & (eval_sorted["store_id"] == store)].sort_values("d_idx")
        pr = preds_df[(preds_df["item_id"] == item) & (preds_df["store_id"] == store)].sort_values("date")

        y_tr = tr["sales"].to_numpy(dtype=float)
        y_val = vl["sales"].to_numpy(dtype=float)
        y_pr = pr["pred_sales"].to_numpy(dtype=float)

        detailed.append({
            "item_id": item,
            "store_id": store,
            "cat_id": cat,
            "wape": wape(y_val, y_pr),
            "rmsse": rmsse(y_val, y_pr, y_tr),
            "mae": mae(y_val, y_pr),
            "bias": bias(y_val, y_pr),
            "y_true": y_val,
            "y_pred": y_pr,
        })

    row = {
        "Mô hình": "LightGBM",
        "WAPE Toàn cục": round(wape(y_true_all, y_pred_all), 4),
        "RMSSE TB": round(float(np.mean([d["rmsse"] for d in detailed])), 4),
        "MAE": round(mae(y_true_all, y_pred_all), 4),
        "Bias (y - ŷ)": round(bias(y_true_all, y_pred_all), 4),
    }

    for cat in ["FOODS", "HOUSEHOLD", "HOBBIES"]:
        cat_items = [d for d in detailed if d["cat_id"] == cat]
        c_true = np.concatenate([d["y_true"] for d in cat_items])
        c_pred = np.concatenate([d["y_pred"] for d in cat_items])
        row[f"WAPE {cat}"] = round(wape(c_true, c_pred), 4)
        row[f"RMSSE {cat}"] = round(float(np.mean([d["rmsse"] for d in cat_items])), 4)

    return row, detailed


def run_full_evaluation():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    print("=== Nạp dữ liệu đánh giá toàn diện ===")
    raw = load_raw_data("csv")
    feat = create_features(raw)
    train_df, val_df, test_df = get_splits(feat)

    # 1. Chạy 4 Baselines trên Validation
    print("Đang đánh giá 4 mô hình Baseline...")
    base_summary, base_details = run_all_baselines(raw, horizon=28)

    # 2. Đánh giá LightGBM trên Validation
    print("Đang đánh giá mô hình LightGBM...")
    model_path = "ml/saved_models/lgbm_p50.txt"
    if not os.path.exists(model_path):
        from ml.train_lgbm import train_and_tune_lgbm, train_quantile_models
        model, params, _ = train_and_tune_lgbm(train_df, val_df)
        os.makedirs("ml/saved_models", exist_ok=True)
        model.booster_.save_model(model_path)
    else:
        booster = lgb.Booster(model_file=model_path)
        model = lgb.LGBMRegressor()
        model._Booster = booster
        model.fitted_ = True

    lgbm_row, lgbm_details = evaluate_lgbm(model, train_df, val_df, "Validation")

    # Ghép bảng so sánh
    all_rows = base_summary.to_dict(orient="records")
    all_rows.append(lgbm_row)
    comparison_df = pd.DataFrame(all_rows).sort_values("WAPE Toàn cục").reset_index(drop=True)

    print("\n" + "=" * 90)
    print("BẢNG SO SÁNH TỔNG HỢP BASELINES VÀ LIGHTGBM TRÊN TẬP VALIDATION (28 NGÀY)")
    print("=" * 90)
    print(comparison_df.to_string(index=False))
    print("=" * 90)

    # Đánh giá cuối cùng trên tập Test (Final Evaluation)
    print("\n" + "=" * 90)
    print("ĐÁNH GIÁ CUỐI CÙNG TRÊN TẬP TEST (28 NGÀY: 2016-04-25 ĐẾN 2016-05-22)")
    print("=" * 90)
    train_val = pd.concat([train_df, val_df], ignore_index=True)
    test_row, _ = evaluate_lgbm(model, train_val, test_df, "Test")
    test_df_summary = pd.DataFrame([test_row])
    print(test_df_summary.to_string(index=False))
    print("=" * 90)

    return comparison_df, test_df_summary


if __name__ == "__main__":
    run_full_evaluation()
