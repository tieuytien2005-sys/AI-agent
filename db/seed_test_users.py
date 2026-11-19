"""
Script nạp các tài khoản người dùng kiểm thử (ADMIN, MANAGER, PURCHASER, VIEWER) vào PostgreSQL.
Dùng trong môi trường Local Dev và GitHub Actions CI/CD.
"""
import os
import sys
import bcrypt
import psycopg


def get_bcrypt_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def seed_users(dsn: str):
    users = [
        ("admin@poc.local", "Admin User", get_bcrypt_hash("admin123"), "ADMIN", True),
        ("manager@poc.local", "Manager User", get_bcrypt_hash("manager123"), "MANAGER", True),
        ("purchaser@poc.local", "Purchaser User", get_bcrypt_hash("purchaser123"), "PURCHASER", True),
        ("viewer@poc.local", "Viewer User", get_bcrypt_hash("viewer123"), "VIEWER", True),
        ("system@poc.local", "System Disabled", "!disabled", "ADMIN", False),
    ]

    print(f"Connecting to database to seed test users...")
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            for email, name, pw_hash, role, active in users:
                cur.execute(
                    """
                    INSERT INTO users (email, full_name, password_hash, role, is_active)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (email) DO UPDATE
                    SET full_name = EXCLUDED.full_name,
                        password_hash = EXCLUDED.password_hash,
                        role = EXCLUDED.role,
                        is_active = EXCLUDED.is_active;
                    """,
                    (email, name, pw_hash, role, active),
                )
        conn.commit()
    print("Seed users created/updated successfully.")


if __name__ == "__main__":
    db_url = os.getenv("DATABASE_URL", "postgresql://app:postgreSQL123@localhost:5432/procurement")
    if db_url.startswith("postgresql+psycopg://"):
        db_url = db_url.replace("postgresql+psycopg://", "postgresql://", 1)
    seed_users(db_url)
