"""Story 5.6 — 使用者透過 Web API + WS 啟動會話並即時看到進度直到報告完成。

端到端（end-to-end）驗證：以**真實** ``eps.main:app``（TestClient 觸發 lifespan、自建
schema 與 seed），把背景排程器換成由 :class:`FakeAdapter` 驅動的真實 ``JobManager``
（共用 app 既有 ``event_bus``，使 WS 端點訂閱得到同一條事件流），再以 HTTP ``POST
/sessions`` 啟動、WebSocket client 連 ``/sessions/{id}/events`` 即時觀看：

- AC-1：3 位專家、``max_rounds=2`` → WS 依序收到 ``StatusChanged(Running)``、各輪
  ``RoundStarted/ExpertStarted/ExpertCompleted/FocusUpdated/RoundSummary``、最後
  ``ReportCompleted``。
- AC-2：收到 ``ReportCompleted`` 後 ``GET /sessions/{id}/report.md`` → 200、非空 Markdown。
- AC-3：注入回傳 ``SourceError`` 的 adapter → WS 收到 ``StatusChanged(SourceInvalid)``
  與 ``SessionFailed``（``partialAvailable``、含「重新登入」提示），而非偽造的成功報告
  （OPS-1）。

需求對應：FR-11, FR-12, FR-13, FR-17, OPS-1。依賴 Story 5.2 / 5.4 / 5.5 / 4.7。

決定性把關（gate）：背景任務於 ``POST`` 後隨即在 app event loop 上推進，可能搶在 WS
訂閱前就跑完——屆時自 ``Running`` 起的即時事件將不在訂閱佇列內。為使序列可決定性斷言，
本測試的 ``_GatedAdapter`` 讓 ``validate_source`` 阻塞到「該會話已有 WS 訂閱者」才放行，
確保 client 不遺漏自 ``Running``／``SourceInvalid`` 起的事件。
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine

import eps.config as config
import eps.main as main
from eps.adapters import FakeAdapter
from eps.adapters.base import SourceError
from eps.core.engine import SOURCE_INVALID_REASON, OrchestrationEngine
from eps.core.jobs import JobManager
from eps.data.repository import Repository

EXPERTS = ["甲", "乙", "丙"]  # 3 位專家。
MAX_ROUNDS = 2


# ---------------------------------------------------------------------------
# 把關 adapter：validate_source 阻塞到 WS 已訂閱本會話再放行（見模組 docstring）。
# ---------------------------------------------------------------------------
class _GatedAdapter(FakeAdapter):
    def __init__(self, bus, **kwargs) -> None:
        super().__init__(**kwargs)
        self._bus = bus
        self.session_id: int | None = None

    async def validate_source(self, source_url: str) -> None:
        # 等到 client 已訂閱本會話事件流，才放行（含本路徑後續可能拋出的 SourceError），
        # 確保自此之後發佈的事件必入訂閱佇列、不致遺漏。
        while self.session_id is None or self._bus.subscriber_count(self.session_id) == 0:
            await asyncio.sleep(0.01)
        return await super().validate_source(source_url)


@pytest.fixture
def db_engine(tmp_path, monkeypatch):
    """將 `EPS_DB_URL` 導向 tmp 檔並建表（模擬已 migrate 的 DB）。"""
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
    """啟動 app（觸發 lifespan：連接同一 DB 檔並冪等 seed personas）。"""
    with TestClient(main.app) as c:
        yield c


def _install_fake_job_manager(client, **adapter_kwargs) -> _GatedAdapter:
    """把背景排程器換成由 ``_GatedAdapter`` 驅動的真實 JobManager。

    共用 app lifespan 既有的 ``event_bus``，使 WS 端點（讀 ``app.state.event_bus``）與
    背景引擎發佈在同一條事件流上；repo 包既有 ``db_engine``。回傳 adapter 供測試設定
    ``session_id`` 並斷言呼叫。
    """
    app = client.app
    bus = app.state.event_bus
    repo = Repository(app.state.db_engine)
    adapter = _GatedAdapter(bus, **adapter_kwargs)
    engine = OrchestrationEngine(repo, adapter, bus)
    app.state.job_manager = JobManager(engine, repo, bus, max_concurrency=2)
    # 縮短心跳間隔，避免閒置時測試久候（心跳 ping 於斷言時濾除）。
    app.state.ws_heartbeat_seconds = 0.05
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


def _token(msg: dict):
    """事件序列 token：StatusChanged 取 status，其餘取 type。"""
    if msg["type"] == "StatusChanged":
        return msg["data"]["status"]
    return msg["type"]


def _recv_until(ws, target_type: str, *, max_messages: int = 200) -> list[dict]:
    """收 WS 訊息直到（含）某 type 出現；濾除心跳 ping。逾量即失敗（避免掛死）。"""
    out: list[dict] = []
    for _ in range(max_messages):
        msg = ws.receive_json()
        if msg.get("type") == "ping":  # 傳輸層心跳，非領域事件。
            continue
        out.append(msg)
        if msg["type"] == target_type:
            return out
    raise AssertionError(f"未在 {max_messages} 則訊息內收到 {target_type}：{out}")


# ---------------------------------------------------------------------------
# AC-1 / AC-2：完整研討流程的即時事件序列 + 報告匯出。
# ---------------------------------------------------------------------------
def test_full_session_streams_progress_then_report(client):
    adapter = _install_fake_job_manager(client)
    session_id = _create_session(client)
    adapter.session_id = session_id  # 放行 gate 的條件之一（另一為 WS 已訂閱）。

    with client.websocket_connect(f"/sessions/{session_id}/events") as ws:
        messages = _recv_until(ws, "ReportCompleted")

    tokens = [_token(m) for m in messages]

    # 連線當下的快照（首筆 StatusChanged，狀態為 gate 暫停處的 ValidatingSource）之後，
    # 應依序見到 Running → 每輪事件 → ReportCompleted。自第一個 Running 起切片斷言，
    # 對快照前綴穩健。
    expert_block = ["ExpertStarted", "ExpertCompleted", "FocusUpdated"] * len(EXPERTS)
    round_block = ["RoundStarted"] + expert_block + ["RoundSummary"]
    expected = ["Running"] + round_block * MAX_ROUNDS + ["ReportCompleted"]
    assert tokens[tokens.index("Running"):] == expected

    # 全部訊息皆繫結同一 sessionId（信封 schema：{type, sessionId, ts, data}）。
    assert all(m["sessionId"] == session_id for m in messages)
    report_evt = messages[-1]
    assert report_evt["type"] == "ReportCompleted"
    assert report_evt["data"]["report"]  # 報告內容非空。

    # --- AC-2：收到 ReportCompleted 後匯出 Markdown → 200、非空 ---
    resp = client.get(f"/sessions/{session_id}/report.md")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    assert resp.text  # 非空 Markdown 報告。


# ---------------------------------------------------------------------------
# AC-3：來源失效 → SessionFailed（SourceInvalid、partialAvailable、重新登入提示），
#       而非偽造的成功報告（OPS-1）。
# ---------------------------------------------------------------------------
def test_source_error_streams_session_failed_not_fake_report(client):
    adapter = _install_fake_job_manager(
        client, source_error=SourceError("CLI 未登入或 OAuth session 失效")
    )
    session_id = _create_session(client)
    adapter.session_id = session_id

    with client.websocket_connect(f"/sessions/{session_id}/events") as ws:
        messages = _recv_until(ws, "SessionFailed")

    tokens = [_token(m) for m in messages]
    # 來源失效落地 SourceInvalid，並對外發出 SessionFailed（OPS-1：不偽造成功報告）。
    assert "SourceInvalid" in tokens
    assert "ReportCompleted" not in tokens

    failed = messages[-1]
    assert failed["type"] == "SessionFailed"
    # partialAvailable 為 False（pre-flight 失效，尚無任何部分結果）。
    assert failed["data"]["partialAvailable"] is False
    # 失敗原因含「重新登入」提示（OPS-1 重新登入引導）。
    assert "重新登入" in failed["data"]["reason"]
    assert failed["data"]["reason"] == SOURCE_INVALID_REASON

    # 報告未產出 → 匯出回 409 REPORT_NOT_READY（未偽造成功報告）。
    resp = client.get(f"/sessions/{session_id}/report.md")
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "REPORT_NOT_READY"
