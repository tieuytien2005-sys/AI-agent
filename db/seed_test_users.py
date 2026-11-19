"""
Script nạp các tài khoản người dùng kiểm thử (ADMIN, MANAGER, PURCHASER, VIEWER) và dữ liệu mẫu vào PostgreSQL.
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
        ("system@poc.local", "System Disabled", get_bcrypt_hash("system123"), "ADMIN", False),
    ]

    print("Connecting to database to seed test users...")
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

            # Khởi tạo bản ghi forecast_runs mẫu để phục vụ API test
            cur.execute("SELECT COUNT(*) FROM forecast_runs;")
            if cur.fetchone()[0] == 0:
                cur.execute(
                    """
                    INSERT INTO forecast_runs (model_name, model_version, trained_until, horizon_days, metrics, params)
                    VALUES ('lgbm_quantile', 'full_v1.0', '2016-05-22', 28,
                            '{"wape_overall": 0.15, "rmsse_overall": 0.25}'::jsonb,
                            '{"horizon": 28}'::jsonb);
                    """
                )
                print("Seed sample forecast_runs created.")

        conn.commit()
    print("Seed users created/updated successfully.")


if __name__ == "__main__":
    db_url = os.getenv("DATABASE_URL", "postgresql://app:postgreSQL123@localhost:5432/procurement")
    if db_url.startswith("postgresql+psycopg://"):
        db_url = db_url.replace("postgresql+psycopg://", "postgresql://", 1)
    seed_users(db_url)
