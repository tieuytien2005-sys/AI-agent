import json
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.core.database import execute_sql
from backend.core.security import get_current_user
from backend.agent.orchestrator import run_agent

router = APIRouter(prefix="/api/agent", tags=["AI Procurement Agent"])


class AgentRunRequest(BaseModel):
    prompt: str = Field(..., description="Yêu cầu từ người dùng hoặc quản lý mua hàng")
    user_id: Optional[int] = Field(1, description="ID người dùng thực hiện yêu cầu")


class AgentRunResponse(BaseModel):
    run_id: int
    status: str
    response: str
    purchase_request_code: Optional[str] = None
    tool_calls_count: Optional[int] = 0


@router.post("/run", response_model=AgentRunResponse, summary="Kích hoạt Agent xử lý yêu cầu")
def trigger_agent(
    req: AgentRunRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Kích hoạt AI Procurement Agent với mô hình Qwen 2.5 local qua Ollama.
    Tự động gọi các tool theo quy trình 7 bước và tạo phiếu đề xuất DRAFT.
    """
    try:
        result = run_agent(prompt=req.prompt, user_id=current_user["id"])
        return result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lỗi khi thực thi Agent: {str(e)}"
        )


@router.get("/runs/{run_id}", summary="Lấy chi tiết một phiên chạy của Agent kèm vết gọi tool")
def get_agent_run_detail(
    run_id: int,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Truy xuất chi tiết phiên chạy agent_runs và toàn bộ lịch sử gọi tool (agent_tool_calls).
    """
    run_rows = execute_sql(
        "SELECT * FROM agent_runs WHERE id = %(run_id)s",
        {"run_id": run_id}
    )
    if not run_rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy phiên chạy agent với run_id={run_id}"
        )
    run_info = run_rows[0]

    # Phân tích JSON output_summary nếu có
    if run_info.get("output_summary") and isinstance(run_info["output_summary"], str):
        try:
            run_info["output_summary"] = json.loads(run_info["output_summary"])
        except Exception:
            pass

    # Lấy các tool calls
    tcall_rows = execute_sql(
        """SELECT seq, tool_name, arguments, result, error, duration_ms, created_at
           FROM agent_tool_calls
           WHERE run_id = %(run_id)s
           ORDER BY seq ASC""",
        {"run_id": run_id}
    )
    for t in tcall_rows:
        for f in ["arguments", "result"]:
            if t.get(f) and isinstance(t[f], str):
                try:
                    t[f] = json.loads(t[f])
                except Exception:
                    pass

    run_info["tool_calls"] = tcall_rows
    return run_info


@router.get("/runs", summary="Lấy danh sách các phiên chạy Agent gần nhất")
def list_agent_runs(
    limit: int = 10,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Lấy danh sách các lần thực thi Agent gần nhất (mặc định 10 lượt).
    """
    rows = execute_sql(
        """SELECT id, trigger_type, triggered_by, model_name, status,
                  tokens_in, tokens_out, started_at, finished_at
           FROM agent_runs
           ORDER BY id DESC
           LIMIT %(limit)s""",
        {"limit": limit}
    )
    return {"count": len(rows), "runs": rows}

