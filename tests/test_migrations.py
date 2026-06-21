"""Story 1.4 / 2.2 — Alembic migration 在乾淨環境可建庫並含索引/約束。

Story 1.4：`alembic upgrade head` 能在全新 SQLite 上成功執行並建立追蹤表。
Story 2.2（AC-1/2/3）：head 建立全部五張表；存在 AC-2 要求的唯一約束與索引；
對同一 `(session_id, round_number)` 插入兩筆 Round 因唯一約束失敗。
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

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


# AC-2：藍圖 §3.2 規定的五張業務資料表。
EXPECTED_TABLES = {
    "session",
    "session_expert",
    "round",
    "contribution",
    "persona_template",
}


@pytest.fixture
def upgraded_engine(clean_db):
    """以 `alembic upgrade head` 建立乾淨 schema，回傳可檢視的 engine。"""
    command.upgrade(Config(str(ALEMBIC_INI)), "head")
    engine = create_engine(clean_db)
    try:
        yield engine
    finally:
        engine.dispose()


def test_alembic_ini_exists():
    assert ALEMBIC_INI.is_file()


def test_upgrade_head_on_clean_db(clean_db):
    db_url = clean_db
    alembic_cfg = Config(str(ALEMBIC_INI))

    command.upgrade(alembic_cfg, "head")

    # 乾淨環境套用 migration 後，alembic_version 應記錄目前 head revision。
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

    assert version == "0002_schema"


# --- AC-1：head 建立全部五張表 ---
def test_head_creates_all_five_tables(upgraded_engine):
    tables = set(inspect(upgraded_engine).get_table_names())
    assert EXPECTED_TABLES <= tables, f"缺少資料表：{EXPECTED_TABLES - tables}"


# --- AC-2：唯一約束 ---
def test_unique_constraint_round_session_round_number(upgraded_engine):
    cons = inspect(upgraded_engine).get_unique_constraints("round")
    assert any(
        set(c["column_names"]) == {"session_id", "round_number"} for c in cons
    ), f"缺少 Round(session_id, round_number) 唯一約束：{cons}"


def test_unique_constraint_contribution_round_seq(upgraded_engine):
    cons = inspect(upgraded_engine).get_unique_constraints("contribution")
    assert any(
        set(c["column_names"]) == {"round_id", "seq"} for c in cons
    ), f"缺少 Contribution(round_id, seq) 唯一約束：{cons}"


# --- AC-2：索引 ---
def test_index_session_created_at_desc(upgraded_engine):
    indexes = {x["name"] for x in inspect(upgraded_engine).get_indexes("session")}
    assert "ix_session_created_at" in indexes
    # SQLite 無法反射表達式索引，改由 DDL 文字確認為 DESC。
    with upgraded_engine.connect() as conn:
        ddl = conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE name = 'ix_session_created_at'"
        ).scalar()
    assert ddl is not None and "DESC" in ddl.upper()


def test_index_session_status(upgraded_engine):
    indexes = inspect(upgraded_engine).get_indexes("session")
    assert any(x["column_names"] == ["status"] for x in indexes)


# --- Story 2.4：session.final_report 欄位（nullable）---
def test_session_has_final_report_column(upgraded_engine):
    columns = {c["name"]: c for c in inspect(upgraded_engine).get_columns("session")}
    assert "final_report" in columns
    assert columns["final_report"]["nullable"] is True


# --- Story 2.5：persona_template.builtin / session_expert.persona_prompt 欄位 ---
def test_persona_template_has_builtin_column(upgraded_engine):
    columns = {
        c["name"]: c
        for c in inspect(upgraded_engine).get_columns("persona_template")
    }
    assert "builtin" in columns
    assert columns["builtin"]["nullable"] is False


def test_persona_template_builtin_index(upgraded_engine):
    indexes = inspect(upgraded_engine).get_indexes("persona_template")
    assert any(x["column_names"] == ["builtin"] for x in indexes)


def test_session_expert_has_persona_prompt_column(upgraded_engine):
    columns = {
        c["name"]: c
        for c in inspect(upgraded_engine).get_columns("session_expert")
    }
    assert "persona_prompt" in columns
    assert columns["persona_prompt"]["nullable"] is False


def test_index_contribution_round_seq(upgraded_engine):
    indexes = inspect(upgraded_engine).get_indexes("contribution")
    assert any(x["column_names"] == ["round_id", "seq"] for x in indexes)


# --- AC-3：重複 Round(session_id, round_number) 插入失敗 ---
def test_duplicate_round_violates_unique_constraint(upgraded_engine):
    with upgraded_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO session (topic, status, max_rounds, created_at, updated_at)"
                " VALUES ('t', 'Created', 3, '2026-01-01', '2026-01-01')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO round (session_id, round_number, created_at)"
                " VALUES (1, 1, '2026-01-01')"
            )
        )

    with pytest.raises(IntegrityError):
        with upgraded_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO round (session_id, round_number, created_at)"
                    " VALUES (1, 1, '2026-01-01')"
                )
            )
