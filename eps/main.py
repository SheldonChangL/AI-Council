"""eps FastAPI app 組裝與端點（Story 1.3 / Story 2.6）。

- AC-1：`app` 為 FastAPI 實例。
- AC-2：`GET /health` 回傳 200 與 `{"status": "ok"}`。
- AC-3：app 啟動時連接 `EPS_DB_URL` 並對 SQLite 啟用 WAL。
- Story 2.6：掛載 `/sessions`、`/personas` 路由；啟動時冪等 seed 內建模板。
- Story 3.5：注入真實 `LocalCliAdapter` 至 `app.state.adapter`，供 `/source/status`
  以 `validate_source()` 真實判定來源是否就緒。
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel, create_engine

import eps.data  # noqa: F401 - 確保所有資料表註冊到 SQLModel.metadata
from eps.adapters import FakeAdapter, LocalCliAdapter, SourceError
from eps.adapters.base import LLMAdapter
from eps.api import router as api_router
from eps.config import Settings, get_settings
from eps.core.bus import EventBus
from eps.core.engine import OrchestrationEngine
from eps.core.jobs import JobManager
from eps.data.repository import Repository
from eps.data.seed import seed_persona_templates


def _build_engine(db_url: str) -> Engine:
    """依 DB URL 建立 engine；SQLite 需放寬同執行緒限制。"""
    connect_args = (
        {"check_same_thread": False} if db_url.startswith("sqlite") else {}
    )
    return create_engine(db_url, connect_args=connect_args)


def _enable_sqlite_wal(engine: Engine) -> None:
    """對 SQLite 啟用 WAL（藍圖 §1）；非 SQLite 則略過。"""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")


def _build_adapter(settings: Settings) -> LLMAdapter:
    """依 ``EPS_ADAPTER`` 選擇 LLM 後端（Story 6.3）。

    - 預設 ``"local_cli"``：真實 :class:`LocalCliAdapter`，驅動本機 CLI。
    - ``"fake"``：決定性 :class:`FakeAdapter`，供跨行程整合測試（subprocess uvicorn）
      在無真實 I/O 下端到端驗證 CLI ``run --follow`` 串流與報告匯出。可由環境變數調校：
      ``EPS_FAKE_SOURCE_ERROR`` 設定時 ``validate_source`` 拋 ``SourceError``（測 OPS-1
      重新登入路徑）；``EPS_FAKE_VALIDATE_DELAY`` 為來源驗證前的延遲秒數，讓觀看端先
      完成 WS 訂閱，確保不遺漏進度事件。
    """
    if settings.adapter == "fake":
        source_error_msg = os.environ.get("EPS_FAKE_SOURCE_ERROR")
        delay = float(os.environ.get("EPS_FAKE_VALIDATE_DELAY", "0") or "0")
        return FakeAdapter(
            source_error=SourceError(source_error_msg) if source_error_msg else None,
            validate_delay_seconds=delay,
        )
    return LocalCliAdapter(settings=settings)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """啟動時連接 `EPS_DB_URL` 並啟用 SQLite WAL，關閉時釋放連線池。"""
    settings = get_settings()
    engine = _build_engine(settings.db_url)
    _enable_sqlite_wal(engine)
    # Story 2.6 / AC-2：運行中的服務自我提供 schema 與內建模板，讓 `uvicorn eps.main:app`
    # 開箱即用。create_all 與 seed 皆冪等：表已存在（如已 alembic 升級）則為 no-op。
    SQLModel.metadata.create_all(engine)
    seed_persona_templates(engine)
    app.state.db_engine = engine
    # Story 3.5：注入 LLM 後端，使 `/source/status` 以 validate_source() 真實判定來源
    # 就緒狀態（AC-1）。預設真實 LocalCliAdapter；`EPS_ADAPTER=fake` 改注入決定性
    # FakeAdapter（Story 6.3 跨行程整合測試）。測試可覆寫 get_adapter 依賴。
    adapter = _build_adapter(settings)
    app.state.adapter = adapter
    # Story 5.2 / AC-1：組裝背景任務排程器，使 `POST /sessions` 能排程研討。
    # EventBus → OrchestrationEngine（注入 repo/adapter/bus）→ JobManager（全域
    # semaphore 限制併發）。`JobManager.start` 以 asyncio.create_task 在此運行中的
    # event loop 上排程，與 HTTP 連線解耦。測試可覆寫 get_job_manager 依賴。
    bus = EventBus()
    repo = Repository(engine)
    job_engine = OrchestrationEngine(
        repo, adapter, bus, max_focus_chars=settings.max_focus_chars
    )
    app.state.event_bus = bus
    app.state.job_manager = JobManager(
        job_engine, repo, bus, max_concurrency=settings.max_concurrency
    )
    # Story 5.5 / AC-3：WS 事件流閒置心跳間隔，供 `/sessions/{id}/events` 讀取。
    app.state.ws_heartbeat_seconds = settings.ws_heartbeat_seconds
    try:
        yield
    finally:
        engine.dispose()


app = FastAPI(title="eps", lifespan=lifespan)
app.include_router(api_router)


@app.get("/health")
def health() -> dict[str, str]:
    """健康檢查端點（AC-2）。"""
    return {"status": "ok"}
