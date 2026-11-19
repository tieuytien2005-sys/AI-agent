import os
from pathlib import Path
from dotenv import load_dotenv

# Tìm và nạp file .env từ thư mục gốc dự án
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(ROOT_DIR / ".env")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct")
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "ollama")

# Đọc cấu hình kết nối DB
_db_user = os.getenv("POSTGRES_USER", "app")
_db_password = os.getenv("DB_PASSWORD", "postgreSQL123")
_db_host = os.getenv("DB_HOST", "localhost")
_db_port = os.getenv("DB_PORT", "5432")
_db_name = os.getenv("POSTGRES_DB", "procurement")

_raw_db_url = os.getenv("DATABASE_URL")
if _raw_db_url:
    if _raw_db_url.startswith("postgresql://") and not _raw_db_url.startswith("postgresql+psycopg://"):
        DATABASE_URL = _raw_db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    else:
        DATABASE_URL = _raw_db_url
else:
    DATABASE_URL = f"postgresql+psycopg://{_db_user}:{_db_password}@{_db_host}:{_db_port}/{_db_name}"

AGENT_MAX_ITERATIONS = int(os.getenv("AGENT_MAX_ITERATIONS", "10"))
AGENT_TEMPERATURE = float(os.getenv("AGENT_TEMPERATURE", "0.1"))
_prod_run_id = os.getenv("PRODUCTION_RUN_ID")
PRODUCTION_RUN_ID = int(_prod_run_id) if _prod_run_id and _prod_run_id.isdigit() else None

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev_secret_change_in_prod")
JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "8"))

