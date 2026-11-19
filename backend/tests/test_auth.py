import uuid
import pytest
from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def test_login_success():
    resp = client.post(
        "/api/auth/login",
        data={"username": "manager@poc.local", "password": "manager123"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["user"]["email"] == "manager@poc.local"
    assert data["user"]["role"] == "MANAGER"


def test_login_wrong_password():
    resp = client.post(
        "/api/auth/login",
        data={"username": "manager@poc.local", "password": "wrongpassword"},
    )
    assert resp.status_code == 401


def test_login_inactive_user():
    resp = client.post(
        "/api/auth/login",
        data={"username": "system@poc.local", "password": "system123"},
    )
    assert resp.status_code == 403
    assert "vô hiệu hóa" in resp.json().get("detail", "")


def test_register_as_admin():
    admin_login = client.post(
        "/api/auth/login",
        data={"username": "admin@poc.local", "password": "admin123"},
    )
    assert admin_login.status_code == 200
    token = admin_login.json()["access_token"]

    unique_email = f"user_{uuid.uuid4().hex[:8]}@poc.local"
    resp = client.post(
        "/api/auth/register",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "email": unique_email,
            "full_name": "Test User",
            "password": "testpassword123",
            "role": "PURCHASER",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["email"] == unique_email
    assert data["role"] == "PURCHASER"


def test_register_as_viewer():
    viewer_login = client.post(
        "/api/auth/login",
        data={"username": "viewer@poc.local", "password": "viewer123"},
    )
    assert viewer_login.status_code == 200
    token = viewer_login.json()["access_token"]

    resp = client.post(
        "/api/auth/register",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "email": f"fail_{uuid.uuid4().hex[:8]}@poc.local",
            "full_name": "Fail User",
            "password": "failpassword123",
            "role": "PURCHASER",
        },
    )
    assert resp.status_code == 403


def test_get_me():
    login_resp = client.post(
        "/api/auth/login",
        data={"username": "purchaser@poc.local", "password": "purchaser123"},
    )
    token = login_resp.json()["access_token"]

    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["email"] == "purchaser@poc.local"
    assert data["role"] == "PURCHASER"


def test_change_password():
    # 1. Admin creates a user dedicated for password change test
    admin_login = client.post(
        "/api/auth/login",
        data={"username": "admin@poc.local", "password": "admin123"},
    )
    token_admin = admin_login.json()["access_token"]

    email = f"pwd_{uuid.uuid4().hex[:8]}@poc.local"
    client.post(
        "/api/auth/register",
        headers={"Authorization": f"Bearer {token_admin}"},
        json={
            "email": email,
            "full_name": "Password User",
            "password": "oldpassword123",
            "role": "VIEWER",
        },
    )

    # 2. Login with old password
    user_login = client.post(
        "/api/auth/login",
        data={"username": email, "password": "oldpassword123"},
    )
    assert user_login.status_code == 200
    user_token = user_login.json()["access_token"]

    # 3. Change password
    change_resp = client.put(
        "/api/auth/me/password",
        headers={"Authorization": f"Bearer {user_token}"},
        json={
            "current_password": "oldpassword123",
            "new_password": "newpassword456",
        },
    )
    assert change_resp.status_code == 200

    # 4. Login with old password should fail
    fail_login = client.post(
        "/api/auth/login",
        data={"username": email, "password": "oldpassword123"},
    )
    assert fail_login.status_code == 401

    # 5. Login with new password should succeed
    ok_login = client.post(
        "/api/auth/login",
        data={"username": email, "password": "newpassword456"},
    )
    assert ok_login.status_code == 200
    assert "access_token" in ok_login.json()

