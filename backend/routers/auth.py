import datetime as dt
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field

from backend.core.database import execute_sql
from backend.core.security import (
    hash_password,
    verify_password,
    create_access_token,
    get_current_user,
    require_role,
)

router = APIRouter(prefix="/api/auth", tags=["Authentication"])

ALLOWED_ROLES = {"ADMIN", "PURCHASER", "MANAGER", "VIEWER"}


class RegisterRequest(BaseModel):
    email: str = Field(..., description="Email người dùng")
    full_name: str = Field(..., description="Họ và tên")
    password: str = Field(..., min_length=6, description="Mật khẩu")
    role: str = Field(..., description="ADMIN / PURCHASER / MANAGER / VIEWER")


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., description="Mật khẩu hiện tại")
    new_password: str = Field(..., min_length=6, description="Mật khẩu mới")


@router.post("/login", summary="Đăng nhập nhận access token")
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    """
    Xác thực email và mật khẩu qua form x-www-form-urlencoded.
    Trả về JWT bearer token và thông tin user.
    """
    rows = execute_sql(
        "SELECT id, email, full_name, password_hash, role, is_active FROM users WHERE email = %(email)s",
        {"email": form_data.username.strip().lower()},
    )
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email hoặc mật khẩu không chính xác",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = rows[0]
    if not verify_password(form_data.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email hoặc mật khẩu không chính xác",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.get("is_active"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tài khoản đã bị vô hiệu hóa",
        )

    token = create_access_token(user_id=user["id"], role=user["role"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user["id"],
            "email": user["email"],
            "full_name": user["full_name"],
            "role": user["role"],
        },
    }


@router.post("/register", summary="Tạo tài khoản người dùng mới (ADMIN only)")
def register(
    req: RegisterRequest,
    current_user: Dict[str, Any] = Depends(require_role("ADMIN")),
):
    """
    Tạo tài khoản mới, chỉ dành cho vai trò ADMIN.
    """
    role = req.role.strip().upper()
    if role not in ALLOWED_ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Role không hợp lệ. Phải là một trong: {', '.join(ALLOWED_ROLES)}",
        )

    email = req.email.strip().lower()
    existing = execute_sql("SELECT id FROM users WHERE email = %(email)s", {"email": email})
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email đã tồn tại trong hệ thống",
        )

    pwd_hash = hash_password(req.password)
    rows = execute_sql(
        """INSERT INTO users (email, full_name, password_hash, role, is_active)
           VALUES (%(email)s, %(full_name)s, %(password_hash)s, %(role)s, TRUE)
           RETURNING id, email, full_name, role, created_at""",
        {
            "email": email,
            "full_name": req.full_name.strip(),
            "password_hash": pwd_hash,
            "role": role,
        },
    )
    return rows[0]


@router.get("/me", summary="Lấy thông tin người dùng hiện tại")
def get_me(current_user: Dict[str, Any] = Depends(get_current_user)):
    """
    Trả về thông tin chi tiết người dùng đang đăng nhập.
    """
    rows = execute_sql(
        "SELECT id, email, full_name, role, created_at FROM users WHERE id = %(id)s",
        {"id": current_user["id"]},
    )
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy thông tin người dùng",
        )
    return rows[0]


@router.put("/me/password", summary="Đổi mật khẩu người dùng hiện tại")
def change_password(
    req: ChangePasswordRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Xác minh mật khẩu cũ và cập nhật mật khẩu mới cho người dùng hiện tại.
    """
    rows = execute_sql(
        "SELECT password_hash FROM users WHERE id = %(id)s",
        {"id": current_user["id"]},
    )
    if not rows or not verify_password(req.current_password, rows[0]["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mật khẩu hiện tại không chính xác",
        )

    new_hash = hash_password(req.new_password)
    execute_sql(
        "UPDATE users SET password_hash = %(pwd_hash)s WHERE id = %(id)s",
        {"pwd_hash": new_hash, "id": current_user["id"]},
    )
    return {"message": "Đổi mật khẩu thành công"}

