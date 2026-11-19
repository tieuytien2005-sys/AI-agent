import io
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.core.database import execute_sql
from backend.core.validators import (
    validate_products_json,
    validate_sales_csv,
)
from backend.main import app

client = TestClient(app)

TEST_SKUS = ["FOODS_TEST_IMP_1", "HOUSEHOLD_TEST_IMP_2"]
TEST_DATES = ["2010-01-01", "2010-01-02", "2010-01-03", "2010-01-04", "2010-01-05"]


@pytest.fixture(scope="module")
def admin_token() -> str:
    resp = client.post(
        "/api/auth/login",
        data={"username": "admin@poc.local", "password": "admin123"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.fixture(scope="module")
def manager_token() -> str:
    resp = client.post(
        "/api/auth/login",
        data={"username": "manager@poc.local", "password": "manager123"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def test_validate_products_valid():
    data = [
        {"sku_code": "FOODS_NEW_001", "name": "Thực phẩm 1", "category": "FOODS", "unit": "pcs"},
        {"sku_code": "HOUSEHOLD_NEW_001", "name": "Gia dụng 1", "category": "HOUSEHOLD"},
    ]
    is_valid, errors = validate_products_json(data)
    assert is_valid is True
    assert errors == []


def test_validate_products_invalid_category():
    data = [
        {"sku_code": "FOODS_OK_001", "name": "Hợp lệ", "category": "FOODS"},
        {"sku_code": "BAD_CAT_002", "name": "Sai danh mục", "category": "UNKNOWN"},
    ]
    is_valid, errors = validate_products_json(data)
    assert is_valid is False
    assert len(errors) == 1
    assert errors[0]["row"] == 3
    assert errors[0]["column"] == "category"


def test_import_products_insert(admin_token: str):
    for sku in TEST_SKUS:
        execute_sql("DELETE FROM products WHERE sku_code = %(sku)s", {"sku": sku})

    count_before = execute_sql("SELECT COUNT(*) AS cnt FROM products", {})[0]["cnt"]

    payload = [
        {"sku_code": TEST_SKUS[0], "name": "Sản phẩm Test 1", "category": "FOODS", "unit": "pcs"},
        {"sku_code": TEST_SKUS[1], "name": "Sản phẩm Test 2", "category": "HOUSEHOLD", "unit": "box"},
    ]
    resp = client.post(
        "/api/import/products",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=payload,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rows_inserted"] == 2
    assert body["rows_updated"] == 0

    count_after = execute_sql("SELECT COUNT(*) AS cnt FROM products", {})[0]["cnt"]
    assert count_after == count_before + 2


def test_import_products_upsert(admin_token: str):
    payload = [
        {"sku_code": TEST_SKUS[0], "name": "Sản phẩm Test 1 Updated", "category": "FOODS", "unit": "pcs"},
        {"sku_code": TEST_SKUS[1], "name": "Sản phẩm Test 2 Updated", "category": "HOUSEHOLD", "unit": "box"},
    ]
    resp = client.post(
        "/api/import/products",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=payload,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rows_updated"] == 2
    assert body["rows_inserted"] == 0


def test_validate_sales_unknown_sku():
    df = pd.DataFrame(
        [
            {
                "item_id": "NONEXISTENT",
                "store_id": "CA_1",
                "date": "2016-05-01",
                "qty_sold": 5,
                "sell_price": 2.5,
            }
        ]
    )
    is_valid, errors = validate_sales_csv(df)
    assert is_valid is False
    sku_errors = [e for e in errors if e["column"] == "item_id"]
    assert len(sku_errors) == 1
    assert "/api/import/products" in (sku_errors[0]["hint"] or "")


def test_validate_sales_negative_qty():
    df = pd.DataFrame(
        [
            {
                "item_id": "FOODS_2_074",
                "store_id": "CA_1",
                "date": "2016-05-01",
                "qty_sold": -1,
                "sell_price": 2.5,
            }
        ]
    )
    is_valid, errors = validate_sales_csv(df)
    assert is_valid is False
    assert any(e["column"] == "qty_sold" for e in errors)


def test_import_sales_insert(admin_token: str):
    for d in TEST_DATES:
        execute_sql(
            """
            DELETE FROM demand_history
            WHERE sale_date = %(d)s::date
              AND product_id = (SELECT id FROM products WHERE sku_code = 'FOODS_2_074')
              AND location_id = (SELECT id FROM locations WHERE code = 'CA_1')
            """,
            {"d": d},
        )

    count_before = execute_sql("SELECT COUNT(*) AS cnt FROM demand_history", {})[0]["cnt"]

    lines = ["item_id,store_id,date,qty_sold,sell_price"]
    for d in TEST_DATES:
        lines.append(f"FOODS_2_074,CA_1,{d},4,2.50")
    csv_bytes = "\n".join(lines).encode("utf-8")

    resp = client.post(
        "/api/import/sales",
        headers={"Authorization": f"Bearer {admin_token}"},
        files={"file": ("sales_new.csv", io.BytesIO(csv_bytes), "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rows_inserted"] == 5
    assert body["rows_updated"] == 0

    count_after = execute_sql("SELECT COUNT(*) AS cnt FROM demand_history", {})[0]["cnt"]
    assert count_after == count_before + 5


def test_import_sales_upsert(admin_token: str):
    count_before = execute_sql("SELECT COUNT(*) AS cnt FROM demand_history", {})[0]["cnt"]

    lines = ["item_id,store_id,date,qty_sold,sell_price"]
    for d in TEST_DATES:
        lines.append(f"FOODS_2_074,CA_1,{d},9,2.75")
    csv_bytes = "\n".join(lines).encode("utf-8")

    resp = client.post(
        "/api/import/sales",
        headers={"Authorization": f"Bearer {admin_token}"},
        files={"file": ("sales_upsert.csv", io.BytesIO(csv_bytes), "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rows_updated"] == 5
    assert body["rows_inserted"] == 0

    count_after = execute_sql("SELECT COUNT(*) AS cnt FROM demand_history", {})[0]["cnt"]
    assert count_after == count_before


def test_import_inventory_update(admin_token: str):
    csv_bytes = b"item_id,store_id,on_hand,on_order\nFOODS_2_074,CA_1,99,5\n"
    resp = client.post(
        "/api/import/inventory",
        headers={"Authorization": f"Bearer {admin_token}"},
        files={"file": ("inv.csv", io.BytesIO(csv_bytes), "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rows_updated"] == 1

    rows = execute_sql(
        """
        SELECT il.on_hand
        FROM inventory_levels il
        JOIN products p ON il.product_id = p.id
        JOIN locations l ON il.location_id = l.id
        WHERE p.sku_code = 'FOODS_2_074' AND l.code = 'CA_1'
        """,
        {},
    )
    assert rows[0]["on_hand"] == 99


def test_import_inventory_unknown_sku(admin_token: str):
    before_rows = execute_sql(
        "SELECT SUM(on_hand)::bigint AS total_oh FROM inventory_levels", {}
    )
    total_before = before_rows[0]["total_oh"]

    csv_bytes = b"item_id,store_id,on_hand,on_order\nNONEXISTENT,CA_1,50,0\n"
    resp = client.post(
        "/api/import/inventory",
        headers={"Authorization": f"Bearer {admin_token}"},
        files={"file": ("inv_unknown.csv", io.BytesIO(csv_bytes), "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rows_skipped"] == 1
    assert body["rows_updated"] == 0

    after_rows = execute_sql(
        "SELECT SUM(on_hand)::bigint AS total_oh FROM inventory_levels", {}
    )
    assert after_rows[0]["total_oh"] == total_before


def test_get_template_sales(admin_token: str):
    resp = client.get(
        "/api/import/template/sales",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200, resp.text
    assert "text/csv" in resp.headers.get("content-type", "")
    header_line = resp.text.splitlines()[0]
    for col in ("item_id", "store_id", "date", "qty_sold"):
        assert col in header_line


def test_import_requires_admin(manager_token: str):
    csv_bytes = b"item_id,store_id,date,qty_sold,sell_price\nFOODS_2_074,CA_1,2016-05-01,3,2.50\n"
    resp = client.post(
        "/api/import/sales",
        headers={"Authorization": f"Bearer {manager_token}"},
        files={"file": ("sales.csv", io.BytesIO(csv_bytes), "text/csv")},
    )
    assert resp.status_code == 403


def test_import_invalid_csv_format(admin_token: str):
    txt_bytes = b"not a csv file"
    resp = client.post(
        "/api/import/sales",
        headers={"Authorization": f"Bearer {admin_token}"},
        files={"file": ("invalid.txt", io.BytesIO(txt_bytes), "text/plain")},
    )
    assert resp.status_code == 422
    assert ".csv" in str(resp.json().get("detail", "")).lower()


def _setup_quote_test_entities() -> tuple[int, int, int]:
    execute_sql(
        """
        INSERT INTO suppliers (code, name, category, lead_time_days, moq, payment_terms_days, is_active)
        VALUES ('SUP_QUOTE_TEST', 'NCC Test Báo Giá', 'FOODS', 5, 20, 30, TRUE)
        ON CONFLICT (code) DO UPDATE SET is_active = TRUE
        """,
        {},
    )
    sid = execute_sql("SELECT id FROM suppliers WHERE code = 'SUP_QUOTE_TEST'", {})[0]["id"]
    pid = execute_sql("SELECT id FROM products WHERE sku_code = 'FOODS_2_074'", {})[0]["id"]
    lid = execute_sql("SELECT id FROM locations WHERE code = 'CA_1'", {})[0]["id"]
    return sid, pid, lid


def test_import_quotes_single_day_valid_to(admin_token: str):
    sid, pid, lid = _setup_quote_test_entities()
    execute_sql(
        "DELETE FROM supplier_quotes WHERE supplier_id = %(sid)s AND product_id = %(pid)s AND location_id = %(lid)s",
        {"sid": sid, "pid": pid, "lid": lid},
    )

    old_rows = execute_sql(
        """
        INSERT INTO supplier_quotes
          (supplier_id, product_id, location_id, base_unit_price, lead_time_days, moq, valid_from, valid_to)
        VALUES (%(sid)s, %(pid)s, %(lid)s, 1.80, 5, 20, '2016-04-01'::date, '2016-04-01'::date)
        RETURNING id
        """,
        {"sid": sid, "pid": pid, "lid": lid},
    )
    old_id = old_rows[0]["id"]

    csv_bytes = (
        b"supplier_code,item_id,store_id,unit_cost,month,lead_time_days,moq\n"
        b"SUP_QUOTE_TEST,FOODS_2_074,CA_1,1.85,2016-04,5,20\n"
    )
    resp = client.post(
        "/api/import/quotes",
        headers={"Authorization": f"Bearer {admin_token}"},
        files={"file": ("quotes_apr.csv", io.BytesIO(csv_bytes), "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rows_inserted"] == 1

    old_q = execute_sql(
        "SELECT valid_from, valid_to FROM supplier_quotes WHERE id = %(id)s",
        {"id": old_id},
    )[0]
    assert old_q["valid_to"] >= old_q["valid_from"]
    assert str(old_q["valid_to"]) == "2016-04-01"


def test_import_quotes_replace_same_month(admin_token: str):
    sid, pid, lid = _setup_quote_test_entities()
    execute_sql(
        """
        DELETE FROM supplier_quotes
        WHERE supplier_id = %(sid)s AND product_id = %(pid)s AND location_id = %(lid)s
          AND valid_from >= '2016-05-01'::date
        """,
        {"sid": sid, "pid": pid, "lid": lid},
    )

    old_rows = execute_sql(
        """
        INSERT INTO supplier_quotes
          (supplier_id, product_id, location_id, base_unit_price, lead_time_days, moq, valid_from, valid_to)
        VALUES (%(sid)s, %(pid)s, %(lid)s, 1.95, 5, 20, '2016-05-01'::date, '2016-05-31'::date)
        RETURNING id
        """,
        {"sid": sid, "pid": pid, "lid": lid},
    )
    old_id = old_rows[0]["id"]

    csv_bytes = (
        b"supplier_code,item_id,store_id,unit_cost,month,lead_time_days,moq\n"
        b"SUP_QUOTE_TEST,FOODS_2_074,CA_1,2.10,2016-05,5,20\n"
    )
    resp = client.post(
        "/api/import/quotes",
        headers={"Authorization": f"Bearer {admin_token}"},
        files={"file": ("quotes_may.csv", io.BytesIO(csv_bytes), "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["rows_inserted"] == 1

    old_q = execute_sql(
        "SELECT valid_to FROM supplier_quotes WHERE id = %(id)s",
        {"id": old_id},
    )[0]
    assert str(old_q["valid_to"]) == "2016-05-01"

    q_resp = client.get(
        f"/api/suppliers/{sid}/quotes?product_id={pid}&as_of_date=2016-05-15",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert q_resp.status_code == 200, q_resp.text
    quotes = q_resp.json()
    assert len(quotes) == 1
    assert float(quotes[0]["base_unit_price"]) == 2.10


def test_import_quotes_overlapping_months(admin_token: str):
    sid, pid, lid = _setup_quote_test_entities()
    execute_sql(
        """
        DELETE FROM supplier_quotes
        WHERE supplier_id = %(sid)s AND product_id = %(pid)s AND location_id = %(lid)s
          AND valid_from >= '2016-06-01'::date
        """,
        {"sid": sid, "pid": pid, "lid": lid},
    )

    may_quotes_before = execute_sql(
        """
        SELECT id, valid_from, valid_to, base_unit_price
        FROM supplier_quotes
        WHERE supplier_id = %(sid)s AND product_id = %(pid)s AND location_id = %(lid)s
          AND valid_from = '2016-05-01'::date AND valid_to = '2016-05-31'::date
        """,
        {"sid": sid, "pid": pid, "lid": lid},
    )
    assert len(may_quotes_before) >= 1
    may_id = may_quotes_before[0]["id"]

    csv_bytes = (
        b"supplier_code,item_id,store_id,unit_cost,month,lead_time_days,moq\n"
        b"SUP_QUOTE_TEST,FOODS_2_074,CA_1,2.25,2016-06,5,20\n"
    )
    resp = client.post(
        "/api/import/quotes",
        headers={"Authorization": f"Bearer {admin_token}"},
        files={"file": ("quotes_jun.csv", io.BytesIO(csv_bytes), "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["rows_inserted"] == 1

    may_after = execute_sql(
        "SELECT valid_from, valid_to, base_unit_price FROM supplier_quotes WHERE id = %(id)s",
        {"id": may_id},
    )[0]
    assert str(may_after["valid_from"]) == "2016-05-01"
    assert str(may_after["valid_to"]) == "2016-05-31"
    assert float(may_after["base_unit_price"]) == 2.10


def teardown_module(module):
    execute_sql(
        "DELETE FROM supplier_quotes WHERE supplier_id IN (SELECT id FROM suppliers WHERE code = 'SUP_QUOTE_TEST')",
        {},
    )
    execute_sql("DELETE FROM suppliers WHERE code = 'SUP_QUOTE_TEST'", {})
    for sku in TEST_SKUS:
        execute_sql("DELETE FROM products WHERE sku_code = %(sku)s", {"sku": sku})
    execute_sql("DELETE FROM demand_history WHERE sale_date < '2011-01-01'::date", {})
