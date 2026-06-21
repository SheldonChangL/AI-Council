"""eps FastAPI app 組裝與 health 端點（Story 1.3）。

- AC-1：`app` 為 FastAPI 實例。
- AC-2：`GET /health` 回傳 200 與 `{"status": "ok"}`。
- AC-3：app 啟動時連接 `EPS_DB_URL` 並對 SQLite 啟用 WAL。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from sqlalchemy.engine import Engine
from sqlmodel import create_engine

from eps.config import get_settings


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
    app.state.db_engine = engine
    try:
        yield
    finally:
        engine.dispose()


app = FastAPI(title="eps", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    """健康檢查端點（AC-2）。"""
    return {"status": "ok"}
