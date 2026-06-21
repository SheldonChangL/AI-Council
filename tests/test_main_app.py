"""Story 1.3 — FastAPI app 組裝與 health 端點（AC-1, AC-2, AC-3）。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import create_engine

import eps.config as config
import eps.main as main


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """將 `EPS_DB_URL` 導向 tmp_path，避免在 repo 產生 SQLite 檔。"""
    db_file = tmp_path / "eps.db"
    monkeypatch.setenv("EPS_DB_URL", f"sqlite:///{db_file}")
    config.get_settings.cache_clear()
    try:
        yield db_file
    finally:
        config.get_settings.cache_clear()


# AC-1: import app 取得 FastAPI 實例。
def test_app_is_fastapi_instance():
    assert isinstance(main.app, FastAPI)


# AC-2: GET /health 回傳 200 與 {"status": "ok"}。
def test_health_returns_ok(isolated_db):
    with TestClient(main.app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# AC-3: 啟動時連接 EPS_DB_URL 並對 SQLite 啟用 WAL。
def test_startup_enables_sqlite_wal(isolated_db):
    db_file = isolated_db

    with TestClient(main.app):
        pass  # 觸發 lifespan startup。

    # WAL 模式會持久化於 DB 檔，重新連線仍應為 wal。
    engine = create_engine(f"sqlite:///{db_file}")
    with engine.connect() as conn:
        mode = conn.exec_driver_sql("PRAGMA journal_mode").scalar()
    engine.dispose()
    assert mode.lower() == "wal"
