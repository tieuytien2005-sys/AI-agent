import asyncio
import subprocess
import sys
import pandas as pd
import pytest

from backend.core.config import DATABASE_URL
from backend.core.database import _clean_dsn, execute_sql
from backend.core.forecaster import trigger_forecast_rerun
from backend.core.importers import import_sales
from ml.data_loader import (
    get_calendar_features,
    get_eligible_skus,
    load_from_csv,
    load_from_db,
)

DSN = _clean_dsn(DATABASE_URL)


def test_load_from_db_returns_correct_columns():
    df, skipped = load_from_db(DSN, "2016-05-22", min_history_days=90)
    expected_cols = {
        "sku_code",
        "store_code",
        "sale_date",
        "qty_sold",
        "sell_price",
        "is_event",
        "snap",
    }
    assert expected_cols.issubset(set(df.columns))
    assert len(df) > 0


def test_load_from_db_no_future_dates():
    cutoff = "2016-04-01"
    df, _ = load_from_db(DSN, cutoff, min_history_days=90)
    assert len(df) > 0
    max_date = pd.to_datetime(df["sale_date"]).max()
    assert max_date <= pd.Timestamp(cutoff)


def test_get_eligible_skus_enough_history():
    sku_short = "FOODS_SHORT_HIST_50"
    execute_sql("DELETE FROM demand_history WHERE product_id IN (SELECT id FROM products WHERE sku_code = %(s)s)", {"s": sku_short})
    execute_sql("DELETE FROM products WHERE sku_code = %(s)s", {"s": sku_short})

    p_rows = execute_sql(
        """
        INSERT INTO products (sku_code, name, category, unit, is_active)
        VALUES (%(s)s, 'SKU 50 ngày lịch sử', 'FOODS', 'pcs', TRUE)
        RETURNING id
        """,
        {"s": sku_short},
    )
    short_pid = p_rows[0]["id"]
    ca1_lid = execute_sql("SELECT id FROM locations WHERE code = 'CA_1'", {})[0]["id"]

    try:
        dates_50 = pd.date_range(end="2016-05-22", periods=50, freq="D")
        for d in dates_50:
            execute_sql(
                """
                INSERT INTO demand_history (product_id, location_id, sale_date, qty_sold, sell_price)
                VALUES (%(pid)s, %(lid)s, %(d)s::date, 4, 2.0)
                """,
                {"pid": short_pid, "lid": ca1_lid, "d": d.date().isoformat()},
            )

        eligible, ineligible = get_eligible_skus(DSN, "2016-05-22", min_history_days=90)
        eligible_codes = {e["sku_code"] for e in eligible}
        ineligible_map = {i["sku_code"]: i for i in ineligible}

        assert "FOODS_2_074" in eligible_codes
        assert sku_short in ineligible_map
        assert ineligible_map[sku_short]["history_days"] == 50
        assert "reason" in ineligible_map[sku_short]
        assert len(ineligible_map[sku_short]["reason"]) > 0
    finally:
        execute_sql("DELETE FROM demand_history WHERE product_id = %(pid)s", {"pid": short_pid})
        execute_sql("DELETE FROM products WHERE id = %(pid)s", {"pid": short_pid})


def test_get_calendar_features_any_date():
    dates = ["2024-01-01", "2000-06-15", "1999-12-31"]
    cal_df = get_calendar_features(pd.Series(dates))
    expected_cols = {
        "dayofweek",
        "month",
        "year",
        "day",
        "is_weekend",
        "is_month_start",
        "is_month_end",
        "quarter",
    }
    assert set(cal_df.columns) == expected_cols
    assert len(cal_df) == 3
    # 2024-01-01 là Monday = 0
    assert int(cal_df.iloc[0]["dayofweek"]) == 0
    assert int(cal_df.iloc[0]["is_month_start"]) == 1
    assert int(cal_df.iloc[2]["is_month_end"]) == 1


def test_load_from_csv_backward_compatible():
    df = load_from_csv("./output")
    assert len(df) >= 44000
    for col in ("sku_code", "store_code", "sale_date", "qty_sold", "sell_price", "is_event", "snap"):
        assert col in df.columns


def test_get_eligible_skus_matches_load():
    eligible, _ = get_eligible_skus(DSN, "2016-05-22", min_history_days=90)
    df, _ = load_from_db(DSN, "2016-05-22", min_history_days=90)

    eligible_pairs = {(e["product_id"], e["location_id"]) for e in eligible}
    loaded_pairs = set(
        zip(df["product_id"].astype(int), df["location_id"].astype(int))
    )
    assert eligible_pairs.issubset(loaded_pairs)
    assert eligible_pairs == loaded_pairs


def test_save_forecasts_source_db_integration():
    runs_before = execute_sql("SELECT COUNT(*) AS cnt FROM forecast_runs", {})[0]["cnt"]
    fc_before = execute_sql("SELECT COUNT(*) AS cnt FROM demand_forecasts", {})[0]["cnt"]

    res = subprocess.run(
        [
            sys.executable,
            "ml/save_forecasts.py",
            "--dsn",
            DSN,
            "--source",
            "db",
            "--train-until",
            "2016-05-22",
            "--min-history",
            "90",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert res.returncode == 0, f"stderr: {res.stderr}\nstdout: {res.stdout}"

    runs_after = execute_sql("SELECT COUNT(*) AS cnt FROM forecast_runs", {})[0]["cnt"]
    fc_after = execute_sql("SELECT COUNT(*) AS cnt FROM demand_forecasts", {})[0]["cnt"]
    assert runs_after == runs_before + 1
    assert fc_after == fc_before + 840

    latest_run_id = execute_sql("SELECT MAX(id) AS id FROM forecast_runs", {})[0]["id"]
    bad_rows = execute_sql(
        """
        SELECT COUNT(*) AS cnt
        FROM demand_forecasts
        WHERE run_id = %(rid)s AND NOT (p10 <= p50 AND p50 <= p90)
        """,
        {"rid": latest_run_id},
    )[0]["cnt"]
    assert bad_rows == 0


def test_fallback_forecast_new_sku():
    sku_fb = "FOODS_FB_NEW_30"
    execute_sql("DELETE FROM demand_forecasts WHERE product_id IN (SELECT id FROM products WHERE sku_code = %(s)s)", {"s": sku_fb})
    execute_sql("DELETE FROM demand_history WHERE product_id IN (SELECT id FROM products WHERE sku_code = %(s)s)", {"s": sku_fb})
    execute_sql("DELETE FROM products WHERE sku_code = %(s)s", {"s": sku_fb})

    p_rows = execute_sql(
        """
        INSERT INTO products (sku_code, name, category, unit, is_active)
        VALUES (%(s)s, 'SKU Fallback 30 ngày', 'FOODS', 'pcs', TRUE)
        RETURNING id
        """,
        {"s": sku_fb},
    )
    fb_pid = p_rows[0]["id"]
    ca1_lid = execute_sql("SELECT id FROM locations WHERE code = 'CA_1'", {})[0]["id"]

    try:
        dates_30 = pd.date_range(end="2016-05-22", periods=30, freq="D")
        for d in dates_30:
            execute_sql(
                """
                INSERT INTO demand_history (product_id, location_id, sale_date, qty_sold, sell_price)
                VALUES (%(pid)s, %(lid)s, %(d)s::date, 6, 3.0)
                """,
                {"pid": fb_pid, "lid": ca1_lid, "d": d.date().isoformat()},
            )

        res = subprocess.run(
            [
                sys.executable,
                "ml/save_forecasts.py",
                "--dsn",
                DSN,
                "--source",
                "db",
                "--train-until",
                "2016-05-22",
                "--min-history",
                "90",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert res.returncode == 0, f"stderr: {res.stderr}"

        latest_run = execute_sql(
            "SELECT id, params FROM forecast_runs ORDER BY id DESC LIMIT 1", {}
        )[0]
        run_id = latest_run["id"]
        params = latest_run["params"] or {}
        fb_list = params.get("fallback_skus", [])
        assert any(item["sku_code"] == sku_fb for item in fb_list)

        fb_fc = execute_sql(
            """
            SELECT forecast_date, p10, p50, p90
            FROM demand_forecasts
            WHERE run_id = %(rid)s AND product_id = %(pid)s
            ORDER BY forecast_date ASC
            """,
            {"rid": run_id, "pid": fb_pid},
        )
        assert len(fb_fc) == 28
        assert all(float(r["p50"]) > 0 for r in fb_fc)
    finally:
        execute_sql("DELETE FROM demand_forecasts WHERE product_id = %(pid)s", {"pid": fb_pid})
        execute_sql("DELETE FROM demand_history WHERE product_id = %(pid)s", {"pid": fb_pid})
        execute_sql("DELETE FROM products WHERE id = %(pid)s", {"pid": fb_pid})


def test_import_then_forecast_new_date():
    execute_sql("DELETE FROM demand_history WHERE sale_date > '2016-05-22'::date", {})
    execute_sql("DELETE FROM forecast_runs WHERE trained_until = '2016-06-10'::date", {})

    sku_rows = execute_sql(
        """
        SELECT p.sku_code, l.code AS store_code
        FROM inventory_levels il
        JOIN products p ON il.product_id = p.id
        JOIN locations l ON il.location_id = l.id
        ORDER BY p.id LIMIT 10
        """,
        {},
    )
    sales_df = pd.DataFrame(
        [
            {
                "item_id": r["sku_code"],
                "store_id": r["store_code"],
                "date": "2016-06-10",
                "qty_sold": 5,
                "sell_price": 2.50,
            }
            for r in sku_rows
        ]
    )
    imp_res = import_sales(sales_df, user_id=1)
    assert imp_res.rows_inserted == 10

    asyncio.run(trigger_forecast_rerun())

    runs = execute_sql(
        """
        SELECT id, trained_until::text AS trained_until
        FROM forecast_runs
        WHERE trained_until = '2016-06-10'::date
        ORDER BY id DESC LIMIT 1
        """,
        {},
    )
    assert len(runs) == 1
    run_id = runs[0]["id"]

    date_bounds = execute_sql(
        """
        SELECT MIN(forecast_date)::text AS min_d,
               MAX(forecast_date)::text AS max_d,
               COUNT(*) AS cnt
        FROM demand_forecasts
        WHERE run_id = %(rid)s
        """,
        {"rid": run_id},
    )[0]
    assert date_bounds["min_d"] == "2016-06-11"
    assert date_bounds["max_d"] == "2016-07-08"
    assert date_bounds["cnt"] == 840
