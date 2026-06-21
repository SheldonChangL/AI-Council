"""Story 6.3 — CLI ``run --follow`` 串流進度並輸出最終報告（in-process 快速覆蓋）。

以**真實** ``eps.main:app``（TestClient 觸發 lifespan）驅動，把背景排程器換成由
:class:`FakeAdapter` 驅動的真實 ``JobManager``，CLI 的 ``_make_client`` 經 monkeypatch
注入連向同一測試服務（沿用 Story 6.2 模式）。與 subprocess e2e（test_story_6_3_cli_e2e）
互補：此處決定性、快速地驗證 follow 的輸出分流（進度 stderr／報告 stdout）、AC-2 報告
一致與 AC-3 失敗路徑。

決定性把關：沿用 ``_GatedAdapter``，``validate_source`` 阻塞到該會話已有 WS 訂閱者再
放行，確保 CLI 不遺漏自 ``Running`` 起的事件。新會話 id 自 1 起（seed 僅建 persona，
不建 session），故可在叫用前預先設定 gate 放行的目標會話 id。
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine
from typer.testing import CliRunner

import eps.cli.main as cli_main
import eps.config as config
import eps.main as main
from eps.adapters import FakeAdapter
from eps.adapters.base import SourceError
from eps.cli.client import EpsClient
from eps.core.engine import SOURCE_INVALID_REASON, OrchestrationEngine
from eps.core.jobs import JobManager
from eps.data.repository import Repository

EXPERTS = ["市場分析師", "技術架構師"]
MAX_ROUNDS = 2


class _GatedAdapter(FakeAdapter):
    """validate_source 阻塞到 WS 已訂閱本會話再放行（見 Story 5.6）。"""

    def __init__(self, bus, **kwargs) -> None:
        super().__init__(**kwargs)
        self._bus = bus
        self.session_id: int | None = None

    async def validate_source(self, source_url: str) -> None:
        while self.session_id is None or self._bus.subscriber_count(self.session_id) == 0:
            await asyncio.sleep(0.01)
        return await super().validate_source(source_url)


@pytest.fixture
def db_engine(tmp_path, monkeypatch):
    db_file = tmp_path / "app.db"
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


@pytest.fixture
def client(db_engine):
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _install_fake_job_manager(client, **adapter_kwargs) -> _GatedAdapter:
    app = client.app
    bus = app.state.event_bus
    repo = Repository(app.state.db_engine)
    adapter = _GatedAdapter(bus, **adapter_kwargs)
    engine = OrchestrationEngine(repo, adapter, bus)
    app.state.job_manager = JobManager(engine, repo, bus, max_concurrency=2)
    app.state.ws_heartbeat_seconds = 0.05
    return adapter


@pytest.fixture
def wire_cli(client, monkeypatch):
    """把 CLI 的 client 導向同一測試服務（注入模式：不在 CLI 端關閉連線）。"""
    monkeypatch.setattr(
        cli_main, "_make_client", lambda base_url=None: EpsClient(client)
    )
    return client


def _invoke_run_follow(runner):
    args = ["run", "--topic", "是否導入新框架", "--max-rounds", str(MAX_ROUNDS),
            "--follow"]
    for name in EXPERTS:
        args.extend(["--expert", name])
    return runner.invoke(cli_main.app, args)


# --- AC-1 / AC-2：串流進度（stderr）+ 最終報告（stdout）並與 report.md 一致 ---
def test_run_follow_streams_progress_and_prints_matching_report(runner, wire_cli):
    adapter = _install_fake_job_manager(wire_cli)
    adapter.session_id = 1  # 新會話 id 自 1 起；放行 gate 的目標會話。

    result = _invoke_run_follow(runner)

    assert result.exit_code == 0, result.output
    # AC-1：各輪各專家進度與「報告完成」串流於 stderr。
    err = result.stderr
    assert "sessionId: 1" in err
    assert "第 1 輪" in err and "第 2 輪" in err
    for name in EXPERTS:
        assert name in err
    assert "發言中" in err
    assert "報告完成" in err

    # AC-1：最終報告文字印於 stdout（FakeAdapter 預設 final_report）。
    assert result.stdout.strip() == "FINAL_REPORT"

    # AC-2：CLI 報告（stdout）與匯出端點 report.md 逐字一致。
    resp = wire_cli.get("/sessions/1/report.md")
    assert resp.status_code == 200
    assert result.stdout.strip() == resp.text.strip()


# --- AC-3：來源失效 → 非零碼 + 重新登入提示，stdout 不輸出臆造報告（OPS-1） ---
def test_run_follow_source_error_exits_nonzero_with_relogin_and_no_report(
    runner, wire_cli
):
    adapter = _install_fake_job_manager(
        wire_cli, source_error=SourceError("CLI 未登入或 OAuth session 失效")
    )
    adapter.session_id = 1

    result = _invoke_run_follow(runner)

    assert result.exit_code == 1, result.output
    assert "會話失敗" in result.stderr
    assert "重新登入" in result.stderr  # SessionFailed.reason 含重新登入引導（OPS-1）。
    assert SOURCE_INVALID_REASON in result.stderr
    # OPS-1：不偽裝成功 — stdout 無任何報告文字。
    assert result.stdout.strip() == ""
