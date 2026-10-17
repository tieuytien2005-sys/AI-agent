"""
Module huấn luyện mô hình LightGBM toàn cục (Global Model) cho 30 chuỗi SKU.
- Tinh chỉnh siêu tham số trên tập Validation bằng Grid Search nhỏ.
- Dự báo đa bước đệ quy (Recursive Multi-Step Forecasting) trong 28 ngày.
- Dự báo phân vị xác suất: p50 (trung vị), p10 (thận trọng), p90 (lạc quan).
"""

import os
import sys
import copy
import numpy as np
import pandas as pd
import lightgbm as lgb

from ml.features import load_raw_data, create_features, get_splits, CATEGORICAL_FEATURES, NUMERICAL_FEATURES, TARGET_COL
from ml.baselines import wape, rmsse, mae, bias


ALL_FEATURES = NUMERICAL_FEATURES + CATEGORICAL_FEATURES


def recursive_forecast(model, train_df: pd.DataFrame, val_df: pd.DataFrame, horizon: int = 28) -> pd.DataFrame:
    """
    Dự báo đệ quy 28 ngày cho 30 chuỗi SKU.
    Tại mỗi bước t trong horizon (1..28):
      - Cập nhật lag_7, lag_14, lag_28, rolling_mean từ các giá trị đã dự báo trước đó.
      - Dự đoán ngày t, lưu vào bộ đệm và lặp lại cho ngày t+1.
    """
    # Ghép 35 ngày cuối của train với val để có đủ cửa sổ tính lag và rolling
    tail_train = train_df.groupby(["store_id", "item_id"]).tail(60).copy()
    combined = pd.concat([tail_train, val_df], ignore_index=True)
    combined["sales"] = combined["sales"].astype(float)
    combined = combined.sort_values(["store_id", "item_id", "date"]).reset_index(drop=True)

    val_dates = sorted(val_df["date"].unique())
    
    # Dự báo từng ngày một
    for step_date in val_dates:
        # Cập nhật lại các đặc trưng động
        grouped = combined.groupby(["store_id", "item_id"])
        combined["lag_7"] = grouped["sales"].shift(7)
        combined["lag_14"] = grouped["sales"].shift(14)
        combined["lag_28"] = grouped["sales"].shift(28)

        sales_shifted = grouped["sales"].shift(1)
        combined["rolling_mean_7"] = sales_shifted.groupby([combined["store_id"], combined["item_id"]]).transform(
            lambda x: x.rolling(7, min_periods=1).mean()
        )
        combined["rolling_mean_28"] = sales_shifted.groupby([combined["store_id"], combined["item_id"]]).transform(
            lambda x: x.rolling(28, min_periods=1).mean()
        )
        combined["rolling_std_7"] = sales_shifted.groupby([combined["store_id"], combined["item_id"]]).transform(
            lambda x: x.rolling(7, min_periods=1).std().fillna(0.0)
        )
        combined["rolling_std_28"] = sales_shifted.groupby([combined["store_id"], combined["item_id"]]).transform(
            lambda x: x.rolling(28, min_periods=1).std().fillna(0.0)
        )

        # Lấy dòng tương ứng ngày hiện tại để dự báo
        mask = combined["date"] == step_date
        current_rows = combined[mask].copy()

        # Chuẩn bị ma trận đặc trưng X
        X_step = current_rows[ALL_FEATURES]
        preds = model.predict(X_step)
        preds = np.clip(preds, 0.0, None) # Không có nhu cầu âm

        # Ghi nhận kết quả dự báo vào cột sales để bước tiếp theo có thể lag
        combined.loc[mask, "sales"] = preds

    # Trích xuất kết quả dự báo của 28 ngày validation
    preds_df = combined[combined["date"].isin(val_dates)][["store_id", "item_id", "cat_id", "date", "sales"]].copy()
    preds_df = preds_df.rename(columns={"sales": "pred_sales"})
    return preds_df


def train_and_tune_lgbm(train_df: pd.DataFrame, val_df: pd.DataFrame) -> tuple[lgb.LGBMRegressor, dict, dict]:
    """
    Huấn luyện và dò siêu tham số bằng lưới nhỏ (Grid Search) trên tập Validation.
    Chọn mô hình có WAPE toàn cục thấp nhất.
    """
    X_train = train_df[ALL_FEATURES]
    y_train = train_df[TARGET_COL]

    # Lưới siêu tham số tinh gọn
    param_grid = [
        {"n_estimators": 150, "learning_rate": 0.05, "max_depth": 5, "num_leaves": 31, "objective": "regression_l1"},
        {"n_estimators": 200, "learning_rate": 0.03, "max_depth": 6, "num_leaves": 31, "objective": "regression_l1"},
        {"n_estimators": 250, "learning_rate": 0.05, "max_depth": 7, "num_leaves": 63, "objective": "regression_l1"},
        {"n_estimators": 200, "learning_rate": 0.05, "max_depth": 5, "num_leaves": 31, "objective": "regression"},
    ]

    best_model = None
    best_params = None
    best_wape = float("inf")
    best_metrics = {}

    y_val_actual = val_df.sort_values(["store_id", "item_id", "date"])["sales"].to_numpy()

    print("--- Bắt đầu Grid Search tinh chỉnh LightGBM trên Validation set ---")
    for idx, params in enumerate(param_grid, 1):
        model = lgb.LGBMRegressor(
            **params,
            random_state=42,
            verbosity=-1,
            n_jobs=-1
        )
        model.fit(X_train, y_train)

        # Chạy dự báo đệ quy trên 28 ngày validation
        preds_df = recursive_forecast(model, train_df, val_df, horizon=28)
        preds_df = preds_df.sort_values(["store_id", "item_id", "date"])
        y_val_pred = preds_df["pred_sales"].to_numpy()

        val_wape = wape(y_val_actual, y_val_pred)
        val_mae = mae(y_val_actual, y_val_pred)
        val_bias = bias(y_val_actual, y_val_pred)

        print(f"  Thử nghiệm {idx}/{len(param_grid)}: {params} -> WAPE: {val_wape:.4f}, MAE: {val_mae:.4f}, Bias: {val_bias:.4f}")

        if val_wape < best_wape:
            best_wape = val_wape
            best_model = model
            best_params = params
            best_metrics = {
                "wape": val_wape,
                "mae": val_mae,
                "bias": val_bias,
                "y_pred": y_val_pred,
            }

    print(f"\n>>> Siêu tham số LightGBM tốt nhất: {best_params} với Validation WAPE = {best_wape:.4f} <<<")
    return best_model, best_params, best_metrics


def train_quantile_models(train_df: pd.DataFrame, best_params: dict) -> tuple[lgb.LGBMRegressor, lgb.LGBMRegressor]:
    """
    Huấn luyện 2 mô hình LightGBM phân vị cho p10 (alpha=0.10) và p90 (alpha=0.90).
    """
    X_train = train_df[ALL_FEATURES]
    y_train = train_df[TARGET_COL]

    q_params = copy.deepcopy(best_params)
    q_params["objective"] = "quantile"

    print("Đang huấn luyện mô hình phân vị p10 (alpha=0.10)...")
    model_p10 = lgb.LGBMRegressor(**q_params, alpha=0.10, random_state=42, verbosity=-1, n_jobs=-1)
    model_p10.fit(X_train, y_train)

    print("Đang huấn luyện mô hình phân vị p90 (alpha=0.90)...")
    model_p90 = lgb.LGBMRegressor(**q_params, alpha=0.90, random_state=42, verbosity=-1, n_jobs=-1)
    model_p90.fit(X_train, y_train)

    return model_p10, model_p90


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    print("=== Nạp dữ liệu và chuẩn bị huấn luyện LightGBM ===")
    raw = load_raw_data("csv")
    feat = create_features(raw)
    train_df, val_df, test_df = get_splits(feat)

    print(f"Số dòng Train: {len(train_df)} | Validation: {len(val_df)} | Test: {len(test_df)}")

    # Huấn luyện và chọn tham số
    best_model, best_params, best_metrics = train_and_tune_lgbm(train_df, val_df)

    # Huấn luyện phân vị
    model_p10, model_p90 = train_quantile_models(train_df, best_params)

    # Lưu model vào thư mục ml/saved_models
    os.makedirs("ml/saved_models", exist_ok=True)
    best_model.booster_.save_model("ml/saved_models/lgbm_p50.txt")
    model_p10.booster_.save_model("ml/saved_models/lgbm_p10.txt")
    model_p90.booster_.save_model("ml/saved_models/lgbm_p90.txt")
    print("Đã lưu các mô hình LightGBM (p50, p10, p90) vào thư mục ml/saved_models/")


if __name__ == "__main__":
    main()
