"""eps HTTP 路由：歷史會話查詢/刪除與內建模板庫（Story 2.6）。

端點（AC-1~AC-4）：
- ``GET /sessions``：依 createdAt 遞減列出會話摘要，支援 ``limit`` / ``offset`` / ``status``。
- ``GET /personas``：列出系統內建 Persona 模板（≥3）。
- ``GET /sessions/{id}``：回傳完整會話聚合；不存在回 404 ``SESSION_NOT_FOUND``。
- ``GET /sessions/{id}/report.md``（Story 5.4）：將最終報告匯出為 Markdown 檔；
  未產出報告回 409 ``REPORT_NOT_READY``，不存在回 404 ``SESSION_NOT_FOUND``。
- ``DELETE /sessions/{id}``：真刪會話，成功回 204；不存在回 404 ``SESSION_NOT_FOUND``。
- ``GET /source/status``（Story 3.5）：查詢本地 LLM 來源是否就緒，由注入的
  ``LLMAdapter.validate_source()`` 真實判定。

Repository 由 ``app.state.db_engine``（lifespan 建立）包裝注入，端點不直接持有連線。
``LLMAdapter`` 由 ``app.state.adapter``（lifespan 注入真實 ``LocalCliAdapter``）提供，
測試可用 ``app.dependency_overrides[get_adapter]`` 換成 ``FakeAdapter``。
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fastapi import (
    APIRouter,
    Body,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from eps.adapters.base import AdapterError, LLMAdapter
from eps.api.schemas import (
    EXPERTS_MAX,
    CreateSessionAccepted,
    CreateSessionRequest,
    PersonaOut,
    SessionDetailOut,
    SessionStatusOut,
    SessionSummary,
    SourceStatusOut,
)
from eps.config import DEFAULT_WS_HEARTBEAT_SECONDS
from eps.core.bus import EventBus
from eps.core.events import Event, RoundStarted, StatusChanged
from eps.core.jobs import JobManager
from eps.data.models import RETRYABLE_STATUSES, TERMINAL_STATUSES, SessionStatus
from eps.data.repository import ExpertSpec, Repository, SessionDetail

router = APIRouter()


def get_repository(request: Request) -> Repository:
    """以 lifespan 建立的 engine 包裝 ``Repository`` 注入端點。"""
    return Repository(request.app.state.db_engine)


def get_job_manager(request: Request) -> JobManager:
    """回傳 lifespan 組裝的 ``JobManager``（背景任務排程器）。

    測試以 ``app.dependency_overrides[get_job_manager]`` 覆寫成 stub，避免真實
    背景任務驅動本機 CLI。
    """
    return request.app.state.job_manager


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


def _not_cancellable(session_id: int, current: SessionStatus) -> HTTPException:
    """終態會話不可取消（Story 5.3 / AC-1）→ 409 ``NOT_CANCELLABLE``。"""
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "NOT_CANCELLABLE",
            "message": f"session {session_id} 狀態 {current.value} 為終態，不可取消",
        },
    )


def _not_retryable(session_id: int, current: SessionStatus) -> HTTPException:
    """非失敗終態會話不可重試（Story 5.3 / AC-3）→ 409 ``NOT_RETRYABLE``。"""
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "NOT_RETRYABLE",
            "message": f"session {session_id} 狀態 {current.value} 不可重試",
        },
    )


def _report_not_ready(session_id: int) -> HTTPException:
    """尚未產出最終報告的會話不可匯出（Story 5.4 / AC-2）→ 409 ``REPORT_NOT_READY``。"""
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "REPORT_NOT_READY",
            "message": f"session {session_id} 尚未產出最終報告",
        },
    )


def _validation_error(exc: ValidationError) -> HTTPException:
    """將 ``CreateSessionRequest`` 驗證失敗映射為結構化 HTTP 錯誤（Story 5.2）。

    - experts 超過上限（Pydantic ``too_long`` on ``experts``）→ 400 ``TOO_MANY_EXPERTS``
      （AC-3）。
    - 其餘（topic 空、maxRounds 越界、experts 空、name 空…）→ 422 ``INVALID_INPUT``
      （AC-2）。

    於端點內就地分流（而非全域 handler），避免影響其他端點既有的驗證錯誤行為。
    """
    for err in exc.errors():
        if err.get("type") == "too_long" and "experts" in err.get("loc", ()):
            return HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "TOO_MANY_EXPERTS",
                    "message": f"experts 數量超過上限 {EXPERTS_MAX}",
                },
            )
    # 僅保留可 JSON 序列化的欄位（loc/msg/type）；Pydantic 自訂 validator 的 ctx
    # 可能含例外物件，直接序列化會失敗。
    fields = [
        {"loc": list(err.get("loc", ())), "msg": err.get("msg"), "type": err.get("type")}
        for err in exc.errors()
    ]
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "code": "INVALID_INPUT",
            "message": "請求參數不合法",
            "errors": fields,
        },
    )


@router.post(
    "/sessions",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=CreateSessionAccepted,
)
async def create_session(
    payload: dict = Body(...),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    repo: Repository = Depends(get_repository),
    jobs: JobManager = Depends(get_job_manager),
) -> CreateSessionAccepted:
    """建立會話、驗證並排程背景任務（Story 5.2 / FR-1）。

    - AC-1：合法 payload → 202 ``{sessionId, status:"Created"}`` 並排程背景任務
      （背景任務隨即進入 ``ValidatingSource`` gate，OPS-1）。
    - AC-2：``topic`` 空或 ``maxRounds`` 越界 → 422 ``INVALID_INPUT``。
    - AC-3：``experts`` 超過上限 → 400 ``TOO_MANY_EXPERTS``。
    - AC-4：帶相同 ``Idempotency-Key`` 的重複請求 → 回傳同一 ``sessionId``（不重複排程）。

    以原始 dict 接收後手動 ``model_validate``，使驗證失敗能依類型分流為 422/400
    （見 :func:`_validation_error`）。
    """
    try:
        req = CreateSessionRequest.model_validate(payload)
    except ValidationError as exc:
        raise _validation_error(exc)

    # AC-4：帶鍵時先查既有會話，命中即回同一 sessionId（不重複建立或排程）。
    if idempotency_key:
        existing = repo.find_session_by_idempotency_key(idempotency_key)
        if existing is not None:
            return CreateSessionAccepted(
                session_id=existing.id, status=existing.status
            )

    experts = [
        ExpertSpec(
            name=e.name,
            source_template_id=e.source_template_id,
            persona_prompt=e.persona_prompt,
        )
        for e in req.experts
    ]
    try:
        session = repo.create_session(
            topic=req.topic,
            max_rounds=req.max_rounds,
            experts=experts,
            idempotency_key=idempotency_key,
        )
    except IntegrityError:
        # 併發 backstop（AC-4）：同鍵的另一請求已先落地，回查既有會話回同一 id。
        if idempotency_key:
            existing = repo.find_session_by_idempotency_key(idempotency_key)
            if existing is not None:
                return CreateSessionAccepted(
                    session_id=existing.id, status=existing.status
                )
        raise

    # AC-1：排程背景任務（與 HTTP 連線解耦，隨即進入 ValidatingSource gate）。
    jobs.start(session.id)
    return CreateSessionAccepted(session_id=session.id, status=session.status)


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


@router.get("/sessions/{session_id}/report.md")
def export_report_markdown(
    session_id: int,
    repo: Repository = Depends(get_repository),
) -> Response:
    """將最終報告匯出為 Markdown 檔（Story 5.4 / FR-17 / 藍圖 A6）。

    - AC-1：已產出報告的會話 → 200，``Content-Type: text/markdown``，並以
      ``Content-Disposition: attachment`` 帶 ``.md`` 附檔名供下載／保存。
    - AC-2：尚未產出最終報告（``final_report`` 為空）→ 409 ``REPORT_NOT_READY``。
    - AC-3：會話不存在 → 404 ``SESSION_NOT_FOUND``。

    僅輕量讀取會話本體（不載入 rounds/contributions），以 ``final_report`` 是否
    已落地作為「報告就緒」的單一真相來源——報告與 ``Completed`` 終態於同一
    transaction 原子寫入（見 :meth:`Repository.save_final_report`），故未完成的會話
    必然無報告，匯出即回 409。
    """
    session = repo.get_session(session_id)
    if session is None:
        raise _session_not_found(session_id)
    if not session.final_report:
        raise _report_not_ready(session_id)

    filename = f"session-{session_id}-report.md"
    return Response(
        content=session.final_report,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(
    session_id: int,
    repo: Repository = Depends(get_repository),
) -> None:
    """真刪會話，成功回 204；不存在回 404 ``SESSION_NOT_FOUND``（AC-4）。"""
    if not repo.delete_session(session_id):
        raise _session_not_found(session_id)
    return None


@router.post("/sessions/{session_id}/cancel", response_model=SessionStatusOut)
async def cancel_session(
    session_id: int,
    repo: Repository = Depends(get_repository),
    jobs: JobManager = Depends(get_job_manager),
) -> SessionStatusOut:
    """取消進行中的會話（Story 5.3 / FR-14, OPS-2 / AC-1）。

    - 非終態會話（``Created`` / ``ValidatingSource`` / ``Running``）→ 200
      ``{status:"Cancelled"}``：先 signal 背景任務取消旗標（引擎於回合／專家邊界
      轉入 ``Cancelled`` 並保留已落地的部分結果），再就地落地 ``Cancelled`` 終態，
      使回應與 DB 立即一致。即便背景任務把手已不在（多程序或重啟、:meth:`JobManager.cancel`
      回 ``False``），仍以 DB 為權威記錄使用者的取消請求。
    - 終態會話（``Completed`` / ``Failed`` / ``SourceInvalid`` / ``Cancelled``）→ 409
      ``NOT_CANCELLABLE``。
    - 會話不存在 → 404 ``SESSION_NOT_FOUND``。
    """
    session = repo.get_session(session_id)
    if session is None:
        raise _session_not_found(session_id)
    if session.status in TERMINAL_STATUSES:
        raise _not_cancellable(session_id, session.status)

    jobs.cancel(session_id)  # best-effort：signal 背景引擎於邊界停止並保留部分結果。
    repo.set_status(session_id, SessionStatus.Cancelled)  # 權威落地終態。
    return SessionStatusOut(status=SessionStatus.Cancelled)


@router.post(
    "/sessions/{session_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SessionStatusOut,
)
async def retry_session(
    session_id: int,
    repo: Repository = Depends(get_repository),
    jobs: JobManager = Depends(get_job_manager),
) -> SessionStatusOut:
    """重試失敗的會話（Story 5.3 / FR-14, OPS-2 / AC-2, AC-3）。

    - 失敗終態會話（``SourceInvalid`` / ``Failed``）→ 202 ``{status:"ValidatingSource"}``：
      就地落地 ``ValidatingSource`` 並重新排程背景任務，使其重新進入來源驗證後續跑
      （與 HTTP 連線解耦）。
    - 其餘狀態（含成功終態 ``Completed``、進行中與 ``Cancelled``）→ 409 ``NOT_RETRYABLE``。
    - 會話不存在 → 404 ``SESSION_NOT_FOUND``。
    """
    session = repo.get_session(session_id)
    if session is None:
        raise _session_not_found(session_id)
    if session.status not in RETRYABLE_STATUSES:
        raise _not_retryable(session_id, session.status)

    repo.set_status(session_id, SessionStatus.ValidatingSource)  # 權威落地重試起點。
    jobs.start(session_id)  # 重新排程：背景引擎重跑來源驗證後續流程。
    return SessionStatusOut(status=SessionStatus.ValidatingSource)


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


# ---------------------------------------------------------------------------
# Story 5.5 — WebSocket 事件流 fan-out、心跳與斷線重訂閱回放（FR-11, NFR-3, 藍圖 W1）。
# ---------------------------------------------------------------------------

# AC-3：閒置心跳訊息。非領域事件（不在 EVENT_REGISTRY），僅作傳輸層 keepalive。
_HEARTBEAT: Dict[str, Any] = {"type": "ping"}

# 傳輸層 sink：接受一筆已序列化（dict）的訊息送往 client。以此抽象解耦
# FastAPI ``WebSocket.send_json`` 與核心串流邏輯，使後者可獨立單元測試。
EventSink = Callable[[Dict[str, Any]], Awaitable[None]]


def build_snapshot_events(detail: SessionDetail) -> List[Event]:
    """AC-2：組出重連時先回放的「目前狀態快照」事件序列。

    以既有事件型別表述目前狀態，client 收到即可對齊、不需重跑：

    - :class:`StatusChanged`：會話目前狀態（最新 status）。
    - :class:`RoundStarted`（若已有回合）：最新回合序號與其目前焦點（最新回合最後
      一筆 ``Contribution.focus_after``，無則空字串）。``detail.rounds`` 依
      ``round_number`` 遞增、``contributions`` 依 ``(round_id, seq)`` 遞增，故最後一筆
      相符 contribution 即該回合最新焦點。
    """
    session_id = detail.session.id
    events: List[Event] = [
        StatusChanged(session_id=session_id, status=detail.session.status.value)
    ]
    if detail.rounds:
        latest = detail.rounds[-1]
        focus = ""
        for contribution in detail.contributions:
            if contribution.round_id == latest.id and contribution.focus_after:
                focus = contribution.focus_after
        events.append(
            RoundStarted(
                session_id=session_id,
                round_number=latest.round_number,
                focus=focus,
            )
        )
    return events


async def stream_session_events(
    send: EventSink,
    bus: EventBus,
    repo: Repository,
    session_id: int,
    *,
    heartbeat_seconds: float,
) -> None:
    """核心串流迴圈：先回放狀態快照，再 fan-out 即時事件並維持心跳。

    與 FastAPI 傳輸細節解耦（``send`` 為注入的 sink），可獨立單元測試。

    順序保證（AC-2 無遺漏對齊）：**先訂閱再讀快照**。先訂閱使讀快照後才發佈的事件
    必入訂閱佇列、不致遺漏；快照與即時流之間至多重疊一次（client 以最新值對齊，
    無害）。

    - AC-1：訂閱 ``session_id`` 後即時收取引擎發佈的事件並逐筆 ``send``。
    - AC-2：訂閱後讀取目前聚合，先送 :func:`build_snapshot_events` 的快照。
    - AC-3：每筆等待以 ``heartbeat_seconds`` 為逾時上限；閒置逾時即送一次心跳 ping
      後繼續等待。
    """
    async with bus.subscribe(session_id) as sub:
        # 先訂閱、後讀快照：關閉「讀快照→開始接收」之間的事件遺漏窗口。
        detail = repo.get_session_detail(session_id)
        if detail is not None:
            for event in build_snapshot_events(detail):
                await send(event.to_dict())
        while True:
            try:
                event = await asyncio.wait_for(sub.get(), timeout=heartbeat_seconds)
            except asyncio.TimeoutError:
                await send(_HEARTBEAT)  # AC-3：閒置達上限 → 心跳維持連線。
                continue
            except StopAsyncIteration:
                break  # 訂閱關閉（unsubscribe 哨符）→ 乾淨結束。
            await send(event.to_dict())


@router.websocket("/sessions/{session_id}/events")
async def session_events(websocket: WebSocket, session_id: int) -> None:
    """訂閱會話事件流（Story 5.5 / FR-11, NFR-3 / 藍圖 W1）。

    - AC-4：不存在的會話 → 在 upgrade 前 raise 404 ``SESSION_NOT_FOUND``（FastAPI 以
      WebSocket denial response 回 404），不建立連線。
    - AC-1/AC-2/AC-3：accept 後委派 :func:`stream_session_events`（先回放快照，再
      即時 fan-out 並維持心跳）。client 斷線時 ``send_json`` 拋
      :class:`WebSocketDisconnect`，乾淨結束並由訂閱 context manager 取消訂閱。
    """
    repo = Repository(websocket.app.state.db_engine)
    if repo.get_session(session_id) is None:
        raise _session_not_found(session_id)  # AC-4：404，不 accept。

    bus: EventBus = websocket.app.state.event_bus
    heartbeat_seconds = float(
        getattr(
            websocket.app.state, "ws_heartbeat_seconds", DEFAULT_WS_HEARTBEAT_SECONDS
        )
    )
    await websocket.accept()
    try:
        await stream_session_events(
            websocket.send_json,
            bus,
            repo,
            session_id,
            heartbeat_seconds=heartbeat_seconds,
        )
    except WebSocketDisconnect:
        return  # client 主動斷線：訂閱 context manager 已負責清理。


__all__ = [
    "router",
    "get_repository",
    "get_adapter",
    "get_job_manager",
    "build_snapshot_events",
    "stream_session_events",
]
