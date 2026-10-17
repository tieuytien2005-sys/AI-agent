"""
Unit tests kiểm tra tính đúng đắn của đặc trưng và đảm bảo KHÔNG rò rỉ dữ liệu tương lai (No Data Leakage).
"""

import numpy as np
import pandas as pd
import pytest

from ml.features import create_features, get_splits


def make_dummy_sales():
    # Tạo chuỗi dữ liệu giả lập 100 ngày cho 1 SKU
    dates = pd.date_range("2016-01-01", periods=100, freq="D")
    df = pd.DataFrame({
        "store_id": ["CA_1"] * 100,
        "item_id": ["FOODS_1_001"] * 100,
        "cat_id": ["FOODS"] * 100,
        "dept_id": ["FOODS_1"] * 100,
        "date": dates,
        "d_idx": range(1, 101),
        "sales": [float(i) for i in range(1, 101)], # 1, 2, 3, ..., 100
        "sell_price": [2.5] * 100,
        "is_stockout": [0] * 100,
        "is_event": [0] * 100,
        "snap": [0] * 100,
        "split": ["train"] * 80 + ["val"] * 10 + ["test"] * 10
    })
    return df


def test_lag_values():
    df = make_dummy_sales()
    feat = create_features(df)
    
    # Tại ngày index 30 (d_idx=31, sales=31):
    # lag_7 phải là sales tại ngày 31 - 7 = 24 (index 23, sales=24)
    row_30 = feat.iloc[30]
    assert row_30["lag_7"] == feat.iloc[30 - 7]["sales"]
    assert row_30["lag_14"] == feat.iloc[30 - 14]["sales"]
    assert row_30["lag_28"] == feat.iloc[30 - 28]["sales"]


def test_no_leakage_rolling_mean():
    df = make_dummy_sales()
    feat = create_features(df)

    # Tại index 10 (sales=11):
    # rolling_mean_7 phải là trung bình của 7 ngày TRƯỚC ĐÓ: sales[3:10] (từ 4 đến 10)
    expected_mean_7 = np.mean([float(i) for i in range(4, 11)])
    actual_mean_7 = feat.iloc[10]["rolling_mean_7"]
    assert np.isclose(actual_mean_7, expected_mean_7)

    # KIỂM TRA CHỐNG RÒ RỈ:
    # Nếu thay đổi giá trị sales tại ngày hiện tại (index 10) từ 11 thành 9999
    # Thì rolling_mean_7 tại ngày index 10 TUYỆT ĐỐI KHÔNG ĐƯỢC THAY ĐỔI
    df_tampered = df.copy()
    df_tampered.loc[10, "sales"] = 9999.0
    feat_tampered = create_features(df_tampered)
    assert feat_tampered.iloc[10]["rolling_mean_7"] == actual_mean_7


def test_splits_and_horizon_lengths():
    # Kiểm tra hàm split trên dữ liệu thực tế
    from ml.features import load_raw_data
    raw = load_raw_data(source="csv")
    feat = create_features(raw)
    train, val, test = get_splits(feat)

    # Validation phải đúng 28 ngày x 30 chuỗi = 840 dòng
    assert len(val) == 840
    assert val["date"].nunique() == 28
    assert str(val["date"].min().date()) == "2016-03-28"
    assert str(val["date"].max().date()) == "2016-04-24"

    # Test phải đúng 28 ngày x 30 chuỗi = 840 dòng
    assert len(test) == 840
    assert test["date"].nunique() == 28
    assert str(test["date"].min().date()) == "2016-04-25"
    assert str(test["date"].max().date()) == "2016-05-22"

    # Train kết thúc trước tập validation
    assert train["date"].max() < val["date"].min()
    assert str(train["date"].max().date()) == "2016-03-27"
