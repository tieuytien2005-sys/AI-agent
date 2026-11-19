import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import httpx
from backend.core.config import OLLAMA_BASE_URL, OLLAMA_MODEL
from backend.core.database import get_db, text
from backend.routers.agent import router as agent_router
from backend.routers.auth import router as auth_router
from backend.routers.requests import router as requests_router
from backend.routers.alerts import router as alerts_router
from backend.routers.forecasts import router as forecasts_router
from backend.routers.suppliers import router as suppliers_router
from backend.routers.imports import router as imports_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("procurement_backend")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Kiểm tra kết nối CSDL PostgreSQL
    try:
        with get_db() as session:
            session.execute(text("SELECT 1;"))
        logger.info("Đã kết nối thành công tới PostgreSQL Database.")
    except Exception as e:
        logger.warning("Cảnh báo: Chưa thể kết nối PostgreSQL. Vui lòng kiểm tra Docker container: %s", e)

    # 2. Kiểm tra kết nối Ollama
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{OLLAMA_BASE_URL.rstrip('/')}/models")
            if resp.status_code == 200:
                logger.info("Đã kết nối thành công tới Ollama API (%s), model: %s", OLLAMA_BASE_URL, OLLAMA_MODEL)
            else:
                logger.warning("Ollama trả về mã lỗi: %s", resp.status_code)
    except Exception as e:
        logger.warning("Cảnh báo: Chưa thể kết nối tới Ollama API tại %s (%s). Vui lòng đảm bảo Ollama đang chạy.", OLLAMA_BASE_URL, e)

    yield
    logger.info("Ứng dụng FastAPI đang dừng.")


app = FastAPI(
    title="AI Procurement Agent Backend",
    description="Backend cung cấp API điều phối AI Procurement Agent với mô hình Qwen 2.5 local qua Ollama.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(requests_router)
app.include_router(alerts_router)
app.include_router(forecasts_router)
app.include_router(suppliers_router)
app.include_router(agent_router)
app.include_router(imports_router)


@app.get("/", summary="Healthcheck và thông tin hệ thống")
def root_info():
    return {
        "service": "AI Procurement Agent Backend",
        "status": "ONLINE",
        "llm_model": OLLAMA_MODEL,
        "docs_url": "/docs",
    }
