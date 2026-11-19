import asyncio
import json
import os
import subprocess
import sys
from backend.core.config import DATABASE_URL
from backend.core.database import execute_sql, _clean_dsn


async def get_latest_sale_date() -> str | None:
    """Trả về MAX(sale_date) từ demand_history."""
    rows = execute_sql(
        "SELECT MAX(sale_date)::text AS d FROM demand_history", {}
    )
    return rows[0]["d"] if rows else None


async def should_retrain(latest_date: str) -> bool:
    """
    Trả về True nếu chưa có forecast_run với
    trained_until = latest_date VÀ model_version LIKE '%full%'.
    """
    rows = execute_sql(
        """
        SELECT id FROM forecast_runs
        WHERE trained_until = %(d)s::date
          AND model_version LIKE '%%full%%'
        LIMIT 1
        """,
        {"d": latest_date},
    )
    return len(rows) == 0


async def trigger_forecast_rerun():
    """
    Chạy lại pipeline ML sau khi import dữ liệu mới.
    Chạy trong BackgroundTasks, không block request.
    """
    try:
        latest_date = await get_latest_sale_date()
        if latest_date is None:
            return

        if not await should_retrain(latest_date):
            _write_audit(
                "FORECAST_SKIP",
                {"reason": "run already exists", "trained_until": latest_date},
            )
            return

        dsn = os.environ.get("DATABASE_URL") or _clean_dsn(DATABASE_URL)

        result = await asyncio.to_thread(
            subprocess.run,
            [
                sys.executable,
                "ml/save_forecasts.py",
                "--dsn",
                dsn,
                "--mode",
                "production",
                "--source",
                "db",
                "--train-until",
                latest_date,
                "--horizon",
                "28",
                "--min-history",
                "90",
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )

        if result.returncode == 0:
            _write_audit(
                "FORECAST_RETRAIN",
                {
                    "trained_until": latest_date,
                    "triggered_by": "import_sales",
                    "source": "db",
                    "stdout_tail": result.stdout[-500:],
                },
            )
        else:
            _write_audit(
                "FORECAST_RETRAIN_FAILED",
                {
                    "trained_until": latest_date,
                    "stderr_tail": result.stderr[-500:],
                },
            )

    except subprocess.TimeoutExpired:
        _write_audit("FORECAST_RETRAIN_TIMEOUT", {"timeout_seconds": 300})
    except Exception as e:
        _write_audit("FORECAST_RETRAIN_ERROR", {"error": str(e)})


def _write_audit(action: str, data: dict):
    execute_sql(
        """
        INSERT INTO audit_logs
          (actor_type, action, entity, after_data)
        VALUES ('SYSTEM', %(action)s, 'forecast_runs', %(data)s::jsonb)
        """,
        {"action": action, "data": json.dumps(data)},
    )
