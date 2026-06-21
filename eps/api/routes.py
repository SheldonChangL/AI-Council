"""eps HTTP 路由：歷史會話查詢/刪除與內建模板庫（Story 2.6）。

端點（AC-1~AC-4）：
- ``GET /sessions``：依 createdAt 遞減列出會話摘要，支援 ``limit`` / ``offset`` / ``status``。
- ``GET /personas``：列出系統內建 Persona 模板（≥3）。
- ``GET /sessions/{id}``：回傳完整會話聚合；不存在回 404 ``SESSION_NOT_FOUND``。
- ``DELETE /sessions/{id}``：真刪會話，成功回 204；不存在回 404 ``SESSION_NOT_FOUND``。
- ``GET /source/status``（Story 3.5）：查詢本地 LLM 來源是否就緒，由注入的
  ``LLMAdapter.validate_source()`` 真實判定。

Repository 由 ``app.state.db_engine``（lifespan 建立）包裝注入，端點不直接持有連線。
``LLMAdapter`` 由 ``app.state.adapter``（lifespan 注入真實 ``LocalCliAdapter``）提供，
測試可用 ``app.dependency_overrides[get_adapter]`` 換成 ``FakeAdapter``。
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from eps.adapters.base import AdapterError, LLMAdapter
from eps.api.schemas import (
    PersonaOut,
    SessionDetailOut,
    SessionSummary,
    SourceStatusOut,
)
from eps.data.models import SessionStatus
from eps.data.repository import Repository

router = APIRouter()


def get_repository(request: Request) -> Repository:
    """以 lifespan 建立的 engine 包裝 ``Repository`` 注入端點。"""
    return Repository(request.app.state.db_engine)


def get_adapter(request: Request) -> LLMAdapter:
    """回傳 lifespan 注入的 ``LLMAdapter``（預設真實 ``LocalCliAdapter``）。

    測試以 ``app.dependency_overrides[get_adapter]`` 覆寫成 ``FakeAdapter``。
    """
    return request.app.state.adapter


def _session_not_found(session_id: int) -> HTTPException:
    """產生結構化 404（AC-3 / AC-4）。"""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": "SESSION_NOT_FOUND",
            "message": f"session {session_id} not found",
        },
    )


@router.get("/sessions", response_model=List[SessionSummary])
def list_sessions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status_filter: Optional[SessionStatus] = Query(default=None, alias="status"),
    repo: Repository = Depends(get_repository),
) -> List[SessionSummary]:
    """列出會話摘要，最近建立優先（AC-1）。"""
    sessions = repo.list_sessions(status=status_filter, limit=limit, offset=offset)
    return [SessionSummary.model_validate(s) for s in sessions]


@router.get("/personas", response_model=List[PersonaOut])
def list_personas(
    repo: Repository = Depends(get_repository),
) -> List[PersonaOut]:
    """列出系統內建 Persona 模板（AC-2）。"""
    personas = repo.list_personas(builtin_only=True)
    return [PersonaOut.model_validate(p) for p in personas]


@router.get("/sessions/{session_id}", response_model=SessionDetailOut)
def get_session(
    session_id: int,
    repo: Repository = Depends(get_repository),
) -> SessionDetailOut:
    """回傳完整會話聚合；不存在回 404 ``SESSION_NOT_FOUND``（AC-3）。"""
    detail = repo.get_session_detail(session_id)
    if detail is None:
        raise _session_not_found(session_id)
    return SessionDetailOut.from_detail(detail)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(
    session_id: int,
    repo: Repository = Depends(get_repository),
) -> None:
    """真刪會話，成功回 204；不存在回 404 ``SESSION_NOT_FOUND``（AC-4）。"""
    if not repo.delete_session(session_id):
        raise _session_not_found(session_id)
    return None


@router.get("/source/status", response_model=SourceStatusOut)
async def source_status(
    adapter: LLMAdapter = Depends(get_adapter),
) -> SourceStatusOut:
    """查詢本地 LLM 來源是否就緒（Story 3.5 / FR-4, OPS-1）。

    呼叫 ``adapter.validate_source()`` 真實判定：正常返回 → ``valid=True``；
    拋出 ``AdapterError``（含 ``SourceError``）→ ``valid=False``，``reason`` 帶
    錯誤訊息（含修復／重新登入提示）。屬就緒查詢，恆回 200。

    來源就緒查詢無關聯特定會話，``source_url`` 傳空字串；``LocalCliAdapter``
    僅驗證本機 CLI 安裝與登入狀態，不使用此參數。
    """
    try:
        await adapter.validate_source("")
    except AdapterError as exc:
        return SourceStatusOut(valid=False, reason=str(exc))
    return SourceStatusOut(valid=True, reason=None)


__all__ = ["router", "get_repository", "get_adapter"]
