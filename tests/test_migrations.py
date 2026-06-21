"""Story 1.4 — Alembic migration 在乾淨環境可建庫（AC-1）。

驗證 `alembic upgrade head` 能在全新 SQLite 上成功執行，並建立 alembic 追蹤表，
即每個里程碑的「建庫」步驟確實可運行。
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

import eps.config as config

REPO_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"


@pytest.fixture
def clean_db(tmp_path, monkeypatch):
    """將 `EPS_DB_URL` 導向 tmp_path 的全新 SQLite 檔。"""
    db_file = tmp_path / "migrate.db"
    db_url = f"sqlite:///{db_file}"
    monkeypatch.setenv("EPS_DB_URL", db_url)
    config.get_settings.cache_clear()
    try:
        yield db_url
    finally:
        config.get_settings.cache_clear()


def test_alembic_ini_exists():
    assert ALEMBIC_INI.is_file()


def test_upgrade_head_on_clean_db(clean_db):
    db_url = clean_db
    alembic_cfg = Config(str(ALEMBIC_INI))

    command.upgrade(alembic_cfg, "head")

    # 乾淨環境套用 migration 後，alembic_version 應記錄 baseline revision。
    engine = create_engine(db_url)
    try:
        tables = inspect(engine).get_table_names()
        assert "alembic_version" in tables
        with engine.connect() as conn:
            version = conn.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar()
    finally:
        engine.dispose()

    assert version == "0001_baseline"
