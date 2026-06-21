"""Story 6.2 — CLI ``watch`` 端到端：WS 串流進度顯示（AC-1, AC-2, AC-3）。

以**真實** ``eps.main:app``（TestClient 觸發 lifespan）驅動，把背景排程器換成由
:class:`FakeAdapter` 驅動的真實 ``JobManager``（共用 app 既有 ``event_bus``，使 WS
端點與背景引擎在同一條事件流上），CLI 的 ``_make_client`` 經 monkeypatch 注入連向同
一測試服務的 :class:`EpsClient`（沿用 Story 6.1 模式）。

決定性把關：背景任務可能搶在 WS 訂閱前跑完，故沿用 Story 5.6 的 ``_GatedAdapter``，
讓 ``validate_source`` 阻塞到「該會話已有 WS 訂閱者」才放行，確保 CLI 不遺漏自
``Running`` 起的事件。
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

EXPERTS = ["甲", "乙", "丙"]
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
    app.state.ws_heartbeat_seconds = 0.05  # 縮短心跳，避免閒置久候（ping 被渲染器忽略）。
    return adapter


def _create_session(client) -> int:
    payload = {
        "topic": "是否導入新框架",
        "maxRounds": MAX_ROUNDS,
        "experts": [{"name": name} for name in EXPERTS],
    }
    resp = client.post("/sessions", json=payload)
    assert resp.status_code == 202, resp.text
    return resp.json()["sessionId"]


@pytest.fixture
def wire_cli(client, monkeypatch):
    """把 CLI 的 client 導向同一測試服務（注入模式：不在 CLI 端關閉連線）。"""
    monkeypatch.setattr(
        cli_main, "_make_client", lambda base_url=None: EpsClient(client)
    )
    return client


# --- AC-1 / AC-2：完整研討流程 → 各輪各專家進度、輪次總結與「報告完成」 ---
def test_watch_streams_progress_until_report_completed(runner, wire_cli):
    adapter = _install_fake_job_manager(wire_cli)
    session_id = _create_session(wire_cli)
    adapter.session_id = session_id  # 放行 gate 的條件之一（另一為 WS 已訂閱）。

    result = runner.invoke(cli_main.app, ["watch", str(session_id)])

    assert result.exit_code == 0, result.output
    out = result.output
    # AC-1：各輪輪次與發言中的專家名稱。
    assert "第 1 輪" in out
    assert "第 2 輪" in out
    for name in EXPERTS:
        assert name in out
    assert "發言中" in out
    # AC-2：輪次總結與「報告完成」提示。
    assert "第 1 輪總結" in out
    assert "第 2 輪總結" in out
    assert "報告完成" in out


# --- AC-3：來源失效 → 印出 SessionFailed 原因，不偽裝成功，並以非零碼結束 ---
def test_watch_source_failure_prints_failure_and_exits_nonzero(runner, wire_cli):
    adapter = _install_fake_job_manager(
        wire_cli, source_error=SourceError("CLI 未登入或 OAuth session 失效")
    )
    session_id = _create_session(wire_cli)
    adapter.session_id = session_id

    result = runner.invoke(cli_main.app, ["watch", str(session_id)])

    out = result.output
    assert result.exit_code == 1, out
    assert "會話失敗" in out
    assert "重新登入" in out  # SessionFailed.reason 含重新登入引導（OPS-1）。
    assert SOURCE_INVALID_REASON in out
    assert "報告完成" not in out  # OPS-1：不偽裝成功。


# --- 邊界：不存在的會話 → WS upgrade 前以 404 拒絕 → 錯誤訊息 + 非零碼 ---
def test_watch_unknown_session_reports_error(runner, wire_cli):
    result = runner.invoke(cli_main.app, ["watch", "999999"])

    assert result.exit_code == 1
    assert "無法連線事件流" in result.stderr
