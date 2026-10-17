"""
Unit tests cho các hàm tính metrics: WAPE, RMSSE, MAE, Bias.
Kiểm tra đối chiếu với các giá trị tính tay (hand-calculated values) và các trường hợp biên.
"""

import math
import numpy as np
import pytest

from ml.baselines import wape, rmsse, mae, bias, evaluate_forecast


def test_wape_hand_calculated():
    # y = [10, 20, 30], sum(y) = 60
    # y_pred = [12, 18, 33]
    # |y - ŷ| = [2, 2, 3] -> sum = 7
    # WAPE = 7 / 60 = 0.1166666...
    y_true = np.array([10, 20, 30])
    y_pred = np.array([12, 18, 33])
    res = wape(y_true, y_pred)
    assert math.isclose(res, 7.0 / 60.0, rel_tol=1e-5)


def test_mae_hand_calculated():
    y_true = np.array([10, 20, 30])
    y_pred = np.array([12, 18, 33])
    res = mae(y_true, y_pred)
    assert math.isclose(res, 7.0 / 3.0, rel_tol=1e-5)


def test_bias_hand_calculated():
    # Bias = mean(y - ŷ)
    # y - ŷ = [-2, 2, -3] -> mean = -3 / 3 = -1.0
    y_true = np.array([10, 20, 30])
    y_pred = np.array([12, 18, 33])
    res = bias(y_true, y_pred)
    assert math.isclose(res, -1.0, rel_tol=1e-5)


def test_rmsse_hand_calculated():
    # y_train = [10, 12, 10, 12, 10] (T = 5)
    # diffs = [2, -2, 2, -2] -> mean(diffs^2) = (4 + 4 + 4 + 4) / 4 = 4.0
    # y_true = [10, 12], y_pred = [11, 11] (H = 2)
    # errors = [-1, 1] -> mean(errors^2) = (1 + 1) / 2 = 1.0
    # RMSSE = sqrt(1.0 / 4.0) = sqrt(0.25) = 0.5
    y_train = np.array([10, 12, 10, 12, 10])
    y_true = np.array([10, 12])
    y_pred = np.array([11, 11])
    res = rmsse(y_true, y_pred, y_train)
    assert math.isclose(res, 0.5, rel_tol=1e-5)


def test_metrics_perfect_forecast():
    y_true = np.array([5, 10, 15, 20])
    y_pred = np.array([5, 10, 15, 20])
    y_train = np.array([2, 4, 6, 8, 10])
    assert wape(y_true, y_pred) == 0.0
    assert mae(y_true, y_pred) == 0.0
    assert bias(y_true, y_pred) == 0.0
    assert rmsse(y_true, y_pred, y_train) == 0.0


def test_wape_zero_actuals():
    # Trường hợp tất cả ngày thực tế bằng 0
    y_true = np.array([0, 0, 0])
    y_pred = np.array([0, 0, 0])
    assert wape(y_true, y_pred) == 0.0

    y_pred_nonzero = np.array([1, 0, 2])
    assert wape(y_true, y_pred_nonzero) == 1.0


def test_evaluate_forecast_dict():
    y_train = np.array([10, 12, 10, 12, 10])
    y_true = np.array([10, 20, 30])
    y_pred = np.array([12, 18, 33])
    res = evaluate_forecast(y_true, y_pred, y_train)
    assert "WAPE" in res
    assert "RMSSE" in res
    assert "MAE" in res
    assert "Bias" in res
    assert math.isclose(res["WAPE"], 7.0 / 60.0, rel_tol=1e-5)
