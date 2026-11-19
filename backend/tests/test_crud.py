import uuid
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.core.database import execute_sql

client = TestClient(app)


def _get_token(email: str, password: str) -> str:
    resp = client.post(
        "/api/auth/login",
        data={"username": email, "password": password},
    )
    assert resp.status_code == 200, f"Login failed for {email}: {resp.text}"
    return resp.json()["access_token"]


@pytest.fixture(scope="module")
def tokens():
    return {
        "admin": _get_token("admin@poc.local", "admin123"),
        "manager": _get_token("manager@poc.local", "manager123"),
        "purchaser": _get_token("purchaser@poc.local", "purchaser123"),
        "viewer": _get_token("viewer@poc.local", "viewer123"),
    }


def _create_draft_request() -> int:
    code = f"PR-T-{uuid.uuid4().hex[:8]}"
    rows = execute_sql(
        """INSERT INTO purchase_requests (code, status, total_value, currency, summary, created_by_user)
           VALUES (%(code)s, 'DRAFT', 500.0, 'VND', 'Phiếu test tự động', 3)
           RETURNING id""",
        {"code": code},
    )
    req_id = rows[0]["id"]
    execute_sql(
        """INSERT INTO purchase_request_lines
           (request_id, product_id, location_id, supplier_id, option_type, qty, unit_price, needed_by_date, reason)
           VALUES (%(rid)s, 1, 1, 1, 'FASTEST', 50, 10.0, CURRENT_DATE + 7, 'Test auto')""",
        {"rid": req_id},
    )
    return req_id


def test_get_requests_list(tokens):
    resp = client.get("/api/requests", headers={"Authorization": f"Bearer {tokens['viewer']}"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "total" in data
    assert "items" in data
    assert len(data["items"]) > 0
    item = data["items"][0]
    assert "lines_count" in item
    assert "confidence_avg" in item


def test_get_request_detail(tokens):
    req_id = _create_draft_request()
    resp = client.get(f"/api/requests/{req_id}", headers={"Authorization": f"Bearer {tokens['viewer']}"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["id"] == req_id
    assert "lines" in data
    assert len(data["lines"]) >= 1
    assert "decisions" in data


def test_submit_draft(tokens):
    req_id = _create_draft_request()
    resp = client.post(
        f"/api/requests/{req_id}/submit",
        headers={"Authorization": f"Bearer {tokens['purchaser']}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "PENDING_APPROVAL"
    assert data["submitted_at"] is not None


def test_approve_as_manager(tokens):
    req_id = _create_draft_request()
    # Submit first
    client.post(
        f"/api/requests/{req_id}/submit",
        headers={"Authorization": f"Bearer {tokens['purchaser']}"},
    )

    resp = client.post(
        f"/api/requests/{req_id}/approve",
        headers={"Authorization": f"Bearer {tokens['manager']}"},
        json={"reason": "Dữ liệu hợp lệ, phê duyệt"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "APPROVED"
    assert data["decided_at"] is not None

    dec_resp = client.get(
        f"/api/requests/{req_id}/decisions",
        headers={"Authorization": f"Bearer {tokens['viewer']}"},
    )
    assert dec_resp.status_code == 200
    decs = dec_resp.json()
    assert len(decs) == 2
    assert decs[-1]["action"] == "APPROVE"
    assert decs[-1]["snapshot"] is not None


def test_reject_requires_reason(tokens):
    req_id = _create_draft_request()
    client.post(
        f"/api/requests/{req_id}/submit",
        headers={"Authorization": f"Bearer {tokens['purchaser']}"},
    )

    # Missing reason
    resp = client.post(
        f"/api/requests/{req_id}/reject",
        headers={"Authorization": f"Bearer {tokens['manager']}"},
        json={"reason": ""},
    )
    assert resp.status_code == 422


def test_reject_as_viewer(tokens):
    req_id = _create_draft_request()
    client.post(
        f"/api/requests/{req_id}/submit",
        headers={"Authorization": f"Bearer {tokens['purchaser']}"},
    )

    resp = client.post(
        f"/api/requests/{req_id}/reject",
        headers={"Authorization": f"Bearer {tokens['viewer']}"},
        json={"reason": "Không đồng ý"},
    )
    assert resp.status_code == 403


def test_revise_returns_draft(tokens):
    req_id = _create_draft_request()
    client.post(
        f"/api/requests/{req_id}/submit",
        headers={"Authorization": f"Bearer {tokens['purchaser']}"},
    )

    resp = client.post(
        f"/api/requests/{req_id}/revise",
        headers={"Authorization": f"Bearer {tokens['manager']}"},
        json={"reason": "Yêu cầu kiểm tra lại số lượng"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "DRAFT"

    # Check decision record
    dec_resp = client.get(
        f"/api/requests/{req_id}/decisions",
        headers={"Authorization": f"Bearer {tokens['viewer']}"},
    )
    decs = dec_resp.json()
    assert decs[-1]["action"] == "REQUEST_CHANGES"


def test_scan_alerts(tokens):
    resp = client.post(
        "/api/alerts/scan",
        headers={"Authorization": f"Bearer {tokens['purchaser']}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["scanned"] == 30
    assert "alerts_by_severity" in data
    assert data["alerts_by_severity"].get("HIGH", 0) > 0


def test_get_forecasts(tokens):
    resp = client.get(
        "/api/forecasts",
        headers={"Authorization": f"Bearer {tokens['viewer']}"},
    )
    assert resp.status_code == 200, resp.text
    runs = resp.json()
    assert len(runs) > 0
    assert "metrics" in runs[0]


def test_get_suppliers(tokens):
    resp = client.get(
        "/api/suppliers",
        headers={"Authorization": f"Bearer {tokens['viewer']}"},
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert len(items) > 0
    assert "latest_score" in items[0]

