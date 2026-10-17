"""
Module lưu kết quả dự báo và đánh giá mô hình vào PostgreSQL:
- Bảng forecast_runs: lưu thông tin mô hình, phiên bản, metrics (WAPE, RMSSE), params.
- Bảng demand_forecasts: lưu chi tiết dự báo phân vị (p10, p50, p90) cho 30 SKU trong 28 ngày (840 dòng).
"""

import os
import sys
import json
import numpy as np
import pandas as pd
import psycopg
from psycopg.types.json import Jsonb
import lightgbm as lgb

from ml.features import load_raw_data, create_features, get_splits, ALL_FEATURES
from ml.train_lgbm import recursive_forecast
from ml.evaluate import evaluate_lgbm


def get_db_connection(dsn: str = None):
    dsn = dsn or os.environ.get("DATABASE_URL")
    if not dsn:
        dsn = "postgresql://app:postgreSQL123@localhost:5432/procurement"
    return psycopg.connect(dsn)


def save_run_and_forecasts(dsn: str = None, save_baseline: bool = True):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    print("=== Đang nạp dữ liệu và tải mô hình để lưu vào PostgreSQL ===")
    raw = load_raw_data("csv")
    feat = create_features(raw)
    train_df, val_df, test_df = get_splits(feat)

    # 1. Tải các mô hình LightGBM đã huấn luyện
    p50_path = "ml/saved_models/lgbm_p50.txt"
    p10_path = "ml/saved_models/lgbm_p10.txt"
    p90_path = "ml/saved_models/lgbm_p90.txt"

    if not os.path.exists(p50_path):
        from ml.train_lgbm import train_and_tune_lgbm, train_quantile_models
        m50, params, _ = train_and_tune_lgbm(train_df, val_df)
        m10, m90 = train_quantile_models(train_df, params)
        os.makedirs("ml/saved_models", exist_ok=True)
        m50.booster_.save_model(p50_path)
        m10.booster_.save_model(p10_path)
        m90.booster_.save_model(p90_path)

    b50 = lgb.Booster(model_file=p50_path)
    b10 = lgb.Booster(model_file=p10_path)
    b90 = lgb.Booster(model_file=p90_path)

    m50 = lgb.LGBMRegressor()
    m50._Booster = b50
    m50.fitted_ = True

    m10 = lgb.LGBMRegressor()
    m10._Booster = b10
    m10.fitted_ = True

    m90 = lgb.LGBMRegressor()
    m90._Booster = b90
    m90.fitted_ = True

    # 2. Sinh dự báo đệ quy cho cả 3 phân vị trên tập Validation (28 ngày)
    print("Đang sinh dự báo đệ quy cho p50, p10, p90...")
    pred_p50 = recursive_forecast(m50, train_df, val_df, horizon=28)
    pred_p10 = recursive_forecast(m10, train_df, val_df, horizon=28)
    pred_p90 = recursive_forecast(m90, train_df, val_df, horizon=28)

    pred_df = pred_p50.rename(columns={"pred_sales": "p50"}).copy()
    pred_df["p10"] = pred_p10["pred_sales"].values
    pred_df["p90"] = pred_p90["pred_sales"].values

    # Chuẩn hóa ràng buộc: 0 <= p10 <= p50 <= p90
    pred_df["p50"] = pred_df["p50"].round(2)
    pred_df["p10"] = np.minimum(pred_df["p10"].round(2), pred_df["p50"])
    pred_df["p10"] = np.maximum(0.0, pred_df["p10"])
    pred_df["p90"] = np.maximum(pred_df["p90"].round(2), pred_df["p50"])

    # Tính metrics của LightGBM
    lgbm_row, _ = evaluate_lgbm(m50, train_df, val_df, "Validation")

    metrics_json = {
        "wape_overall": lgbm_row["WAPE Toàn cục"],
        "rmsse_overall": lgbm_row["RMSSE TB"],
        "mae_overall": lgbm_row["MAE"],
        "bias_overall": lgbm_row["Bias (y - ŷ)"],
        "by_category": {
            "FOODS": {"wape": lgbm_row["WAPE FOODS"], "rmsse": lgbm_row["RMSSE FOODS"]},
            "HOUSEHOLD": {"wape": lgbm_row["WAPE HOUSEHOLD"], "rmsse": lgbm_row["RMSSE HOUSEHOLD"]},
            "HOBBIES": {"wape": lgbm_row["WAPE HOBBIES"], "rmsse": lgbm_row["RMSSE HOBBIES"]},
        }
    }
    params_json = {
        "n_estimators": 200,
        "learning_rate": 0.03,
        "max_depth": 6,
        "num_leaves": 31,
        "objective": "regression_l1"
    }

    # 3. Kết nối database và lưu kết quả
    with get_db_connection(dsn) as conn:
        with conn.cursor() as cur:
            # Tra cứu id của products và locations
            cur.execute("SELECT sku_code, id FROM products;")
            prod_map = dict(cur.fetchall())
            cur.execute("SELECT code, id FROM locations;")
            loc_map = dict(cur.fetchall())

            # A. Ghi vào forecast_runs cho LightGBM
            print("Đang ghi bản ghi vào forecast_runs...")
            cur.execute(
                """
                INSERT INTO forecast_runs (model_name, model_version, trained_until, horizon_days, metrics, params)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id;
                """,
                ("lightgbm_global", "v1.0-poc", "2016-03-27", 28, Jsonb(metrics_json), Jsonb(params_json))
            )
            run_id = cur.fetchone()[0]
            print(f"  -> Đã tạo forecast_runs ID = {run_id} cho [lightgbm_global]")

            # B. Ghi 840 bản ghi vào demand_forecasts
            print("Đang ghi 840 bản ghi phân vị (p10, p50, p90) vào demand_forecasts...")
            forecast_rows = []
            for _, r in pred_df.iterrows():
                p_id = prod_map[r["item_id"]]
                l_id = loc_map[r["store_id"]]
                f_date = str(pd.to_datetime(r["date"]).date())
                forecast_rows.append((
                    run_id, p_id, l_id, f_date,
                    float(r["p10"]), float(r["p50"]), float(r["p90"])
                ))

            cur.executemany(
                """
                INSERT INTO demand_forecasts (run_id, product_id, location_id, forecast_date, p10, p50, p90)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id, product_id, location_id, forecast_date) DO UPDATE
                SET p10 = EXCLUDED.p10, p50 = EXCLUDED.p50, p90 = EXCLUDED.p90;
                """,
                forecast_rows
            )
            print(f"  -> Đã lưu thành công {len(forecast_rows)} dòng vào demand_forecasts")

            # C. Tùy chọn: Lưu cả baseline tốt nhất (MA-28) vào forecast_runs để đối chiếu
            if save_baseline:
                from ml.baselines import run_all_baselines
                b_summary, _ = run_all_baselines(raw, horizon=28)
                ma28_row = b_summary[b_summary["Mô hình"] == "MA-28"].iloc[0]
                ma28_metrics = {
                    "wape_overall": float(ma28_row["WAPE Toàn cục"]),
                    "rmsse_overall": float(ma28_row["RMSSE TB"]),
                    "mae_overall": float(ma28_row["MAE"]),
                    "bias_overall": float(ma28_row["Bias (y - ŷ)"]),
                    "by_category": {
                        "FOODS": {"wape": float(ma28_row["WAPE FOODS"]), "rmsse": float(ma28_row["RMSSE FOODS"])},
                        "HOUSEHOLD": {"wape": float(ma28_row["WAPE HOUSEHOLD"]), "rmsse": float(ma28_row["RMSSE HOUSEHOLD"])},
                        "HOBBIES": {"wape": float(ma28_row["WAPE HOBBIES"]), "rmsse": float(ma28_row["RMSSE HOBBIES"])},
                    }
                }
                cur.execute(
                    """
                    INSERT INTO forecast_runs (model_name, model_version, trained_until, horizon_days, metrics, params)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id;
                    """,
                    ("baseline_ma28", "v1.0-baseline", "2016-03-27", 28, Jsonb(ma28_metrics), Jsonb({"window_days": 28}))
                )
                b_run_id = cur.fetchone()[0]
                print(f"  -> Đã tạo forecast_runs ID = {b_run_id} cho [baseline_ma28]")

        conn.commit()
    print("\n>>> ĐÃ HOÀN TẤT LƯU KẾT QUẢ VÀO POSTGRESQL (COMMIT THÀNH CÔNG) <<<")


if __name__ == "__main__":
    save_run_and_forecasts()
