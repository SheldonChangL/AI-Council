"""Story 6.1 — Typer CLI 骨架與 REST client（AC-1~AC-3 / FR-12 / 藍圖 T4.3）。

驗證 CLI 經既有 REST API 建立會話與查詢狀態：
- AC-1：``--help`` 列出 ``run`` 與 ``status`` 子命令。
- AC-2：``run`` 經 ``POST /sessions`` 建立會話並印出 ``sessionId``。
- AC-3：``status <id>`` 經 ``GET /sessions/{id}`` 印出目前狀態。

CLI 經由 Starlette ``TestClient`` 連向真實 app（觸發 lifespan、共用 tmp DB），
並以 stub ``JobManager`` 隔離背景任務，避免測試驅動真實本機 CLI（沿用 Story 5.2 模式）。
"""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine
from typer.testing import CliRunner

import eps.cli.main as cli_main
import eps.config as config
import eps.main as main
from eps.api.routes import get_job_manager
from eps.cli.client import EpsClient


@pytest.fixture
def db_engine(tmp_path, monkeypatch):
    """將 ``EPS_DB_URL`` 導向 tmp 檔並建立全部資料表（模擬已 migrate 的 DB）。"""
    db_file = tmp_path / "eps.db"
    monkeypatch.setenv("EPS_DB_URL", f"sqlite:///{db_file}")
    config.get_settings.cache_clear()
    engine = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        config.get_settings.cache_clear()


class _StubJobManager:
    """記錄被排程的 session_id，避免測試驅動真實背景任務／本機 CLI。"""

    def __init__(self) -> None:
        self.started: list[int] = []

    def start(self, session_id: int) -> None:
        self.started.append(session_id)

    def cancel(self, session_id: int) -> bool:
        return True


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def wired(db_engine, monkeypatch):
    """啟動 app（觸發 lifespan）並將 CLI 的 client 導向同一測試服務。"""
    stub = _StubJobManager()
    main.app.dependency_overrides[get_job_manager] = lambda: stub
    with TestClient(main.app) as test_client:
        # 注入模式：CLI 共用此 TestClient（已跑 lifespan），且不在 CLI 端關閉它。
        monkeypatch.setattr(
            cli_main, "_make_client", lambda base_url=None: EpsClient(test_client)
        )
        try:
            yield stub
        finally:
            main.app.dependency_overrides.pop(get_job_manager, None)


# --- AC-1：--help 列出 run 與 status 子命令 ---
def test_help_lists_subcommands(runner):
    result = runner.invoke(cli_main.app, ["--help"])

    assert result.exit_code == 0
    assert "run" in result.stdout
    assert "status" in result.stdout


# --- AC-2：run 經 POST /sessions 建立會話並印出 sessionId ---
def test_run_creates_session_and_prints_session_id(runner, wired):
    result = runner.invoke(
        cli_main.app,
        [
            "run",
            "--topic",
            "是否升息",
            "--max-rounds",
            "2",
            "--expert",
            "經濟學家=你是經濟學家",
            "--expert",
            "工程師",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "sessionId:" in result.stdout
    session_id = int(result.stdout.split("sessionId:")[1].strip())
    # 會話確實已建立並排程（背景任務以 stub 隔離）。
    assert wired.started == [session_id]


# --- AC-3：status <id> 經 GET /sessions/{id} 印出目前狀態 ---
def test_status_prints_current_status(runner, wired):
    created = runner.invoke(
        cli_main.app,
        ["run", "--topic", "議題", "--max-rounds", "2", "--expert", "甲"],
    )
    session_id = int(created.stdout.split("sessionId:")[1].strip())

    result = runner.invoke(cli_main.app, ["status", str(session_id)])

    assert result.exit_code == 0, result.stdout
    assert "status: Created" in result.stdout


def test_status_unknown_session_reports_error(runner, wired):
    result = runner.invoke(cli_main.app, ["status", "999999"])

    assert result.exit_code == 1
    assert "SESSION_NOT_FOUND" in result.stderr
