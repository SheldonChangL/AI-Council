"""eps FastAPI app 組裝與端點（Story 1.3 / Story 2.6）。

- AC-1：`app` 為 FastAPI 實例。
- AC-2：`GET /health` 回傳 200 與 `{"status": "ok"}`。
- AC-3：app 啟動時連接 `EPS_DB_URL` 並對 SQLite 啟用 WAL。
- Story 2.6：掛載 `/sessions`、`/personas` 路由；啟動時冪等 seed 內建模板。
- Story 3.5：注入真實 `LocalCliAdapter` 至 `app.state.adapter`，供 `/source/status`
  以 `validate_source()` 真實判定來源是否就緒。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel, create_engine

import eps.data  # noqa: F401 - 確保所有資料表註冊到 SQLModel.metadata
from eps.adapters import LocalCliAdapter
from eps.api import router as api_router
from eps.config import get_settings
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
    # Story 3.5：注入真實 LocalCliAdapter，使 `/source/status` 以 validate_source()
    # 真實判定本機 CLI 安裝與登入狀態（AC-1）。測試可覆寫 get_adapter 依賴。
    app.state.adapter = LocalCliAdapter(settings=settings)
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
