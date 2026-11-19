import asyncio
import io
import time
import pytest
from fastapi.testclient import TestClient

from backend.core.database import execute_sql
from backend.core.forecaster import should_retrain
from backend.main import app

client = TestClient(app)


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


def test_import_sales_triggers_forecast(admin_token: str):
    # Làm sạch các bản ghi lớn hơn 2016-05-22 để đảm bảo lần import 2016-06-01 thực sự kích hoạt retrain mới
    execute_sql("DELETE FROM demand_history WHERE sale_date > '2016-05-22'", {})
    execute_sql("DELETE FROM forecast_runs WHERE trained_until >= '2016-06-01'", {})

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
    assert len(sku_rows) == 10

    lines = ["item_id,store_id,date,qty_sold,sell_price"]
    for r in sku_rows:
        lines.append(f"{r['sku_code']},{r['store_code']},2016-06-01,5,2.50")
    csv_bytes = "\n".join(lines).encode("utf-8")

    resp = client.post(
        "/api/import/sales",
        headers={"Authorization": f"Bearer {admin_token}"},
        files={"file": ("sales_2016_06_01.csv", io.BytesIO(csv_bytes), "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["forecast_rerun_triggered"] is True
    assert body["rows_inserted"] == 10

    # Kiểm tra GET /api/forecasts có run mới với trained_until >= 2016-06-01
    found = False
    for _ in range(15):
        fc_resp = client.get(
            "/api/forecasts",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert fc_resp.status_code == 200
        runs = fc_resp.json()
        if any(str(r["trained_until"]) >= "2016-06-01" for r in runs):
            found = True
            break
        time.sleep(1)

    assert found is True


def test_import_same_date_no_retrain(admin_token: str):
    assert asyncio.run(should_retrain("2016-06-01")) is False

    count_before = execute_sql("SELECT COUNT(*) AS cnt FROM forecast_runs", {})[0]["cnt"]

    csv_bytes = b"item_id,store_id,date,qty_sold,sell_price\nFOODS_2_074,CA_1,2016-06-01,7,2.50\n"
    resp = client.post(
        "/api/import/sales",
        headers={"Authorization": f"Bearer {admin_token}"},
        files={"file": ("sales_same_date.csv", io.BytesIO(csv_bytes), "text/csv")},
    )
    assert resp.status_code == 200, resp.text

    count_after = execute_sql("SELECT COUNT(*) AS cnt FROM forecast_runs", {})[0]["cnt"]
    assert count_after == count_before


def test_full_procurement_flow(admin_token: str, manager_token: str):
    # 1. POST /api/import/inventory (cập nhật on_hand thấp)
    inv_csv = b"item_id,store_id,on_hand,on_order\nFOODS_3_795,CA_1,1,0\n"
    inv_resp = client.post(
        "/api/import/inventory",
        headers={"Authorization": f"Bearer {admin_token}"},
        files={"file": ("low_inv.csv", io.BytesIO(inv_csv), "text/csv")},
    )
    assert inv_resp.status_code == 200, inv_resp.text
    assert inv_resp.json()["rows_updated"] == 1

    # 2. POST /api/alerts/scan → có alert HIGH
    scan_resp = client.post(
        "/api/alerts/scan",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert scan_resp.status_code == 200, scan_resp.text
    assert scan_resp.json()["alerts_by_severity"]["HIGH"] >= 1

    # 3. POST /api/agent/run (tạo đề xuất)
    agent_resp = client.post(
        "/api/agent/run",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "prompt": "Hãy kiểm tra tồn kho và lập phiếu đề xuất mua hàng cho sản phẩm FOODS_3_795 tại CA_1 theo đúng quy trình 7 bước."
        },
    )
    assert agent_resp.status_code == 200, agent_resp.text

    # 4. GET /api/requests → có phiếu DRAFT
    reqs_resp = client.get(
        "/api/requests?status=DRAFT",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert reqs_resp.status_code == 200, reqs_resp.text
    draft_items = reqs_resp.json()["items"]
    assert len(draft_items) >= 1
    req_id = draft_items[0]["id"]

    # 5. POST /api/requests/{id}/submit
    sub_resp = client.post(
        f"/api/requests/{req_id}/submit",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert sub_resp.status_code == 200, sub_resp.text
    assert sub_resp.json()["status"] == "PENDING_APPROVAL"

    # 6. POST /api/requests/{id}/approve (token MANAGER)
    app_resp = client.post(
        f"/api/requests/{req_id}/approve",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"reason": "Đồng ý phê duyệt bổ sung tồn kho khẩn cấp"},
    )
    assert app_resp.status_code == 200, app_resp.text
    assert app_resp.json()["status"] == "APPROVED"

    # 7. GET /api/requests/{id}/decisions → 2 dòng (SUBMIT + APPROVE)
    dec_resp = client.get(
        f"/api/requests/{req_id}/decisions",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert dec_resp.status_code == 200, dec_resp.text
    decisions = dec_resp.json()
    actions = [d["action"] for d in decisions]
    assert len(decisions) >= 2
    assert actions[-2:] == ["SUBMIT", "APPROVE"]
