"""Story 5.5 — WebSocket 事件流 fan-out、心跳與斷線重訂閱回放（AC-1~AC-4）。

兩種驗證層次：

- 核心串流邏輯（``stream_session_events`` / ``build_snapshot_events``）以注入的 sink
  在同一 event loop 內直測，可決定性驗證 fan-out（AC-1）、快照回放（AC-2）與閒置心跳
  （AC-3），不受測試執行緒與 app event loop 跨執行緒投遞限制。
- 端對端（FastAPI + TestClient）驗證真實 WS 接線：404 denial 不建立連線（AC-4）、
  連線即先收快照（AC-2）、閒置送心跳（AC-3）。
"""

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine
from sqlmodel import Session as DBSession
from starlette.testclient import WebSocketDenialResponse

import eps.config as config
import eps.main as main
from eps.api.routes import build_snapshot_events, stream_session_events
from eps.core.bus import EventBus
from eps.core.events import TokenChunk
from eps.data.models import Session, SessionStatus
from eps.data.repository import Repository


# ---------------------------------------------------------------------------
# 測試輔助
# ---------------------------------------------------------------------------
class _Stop(Exception):
    """收滿預期訊息數後中止串流迴圈的哨符（不屬正常控制流）。"""


class _Collect:
    """收集 sink：記錄送出的訊息，達 ``stop_after`` 筆即拋 ``_Stop`` 結束迴圈。"""

    def __init__(self, stop_after: int) -> None:
        self.messages: list = []
        self._stop_after = stop_after

    async def __call__(self, message: dict) -> None:
        self.messages.append(message)
        if len(self.messages) >= self._stop_after:
            raise _Stop


async def _drain(coro) -> None:
    """執行串流協程，吞掉預期的 ``_Stop`` 哨符。"""
    try:
        await coro
    except _Stop:
        pass


@pytest.fixture
def engine(tmp_path):
    """tmp 檔 SQLite engine 並建立全部資料表。"""
    db_file = tmp_path / "eps.db"
    eng = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def repo(engine):
    return Repository(engine)


def _make_session(repo: Repository, *, status=SessionStatus.Created) -> int:
    """建立一場會話並設定狀態，回傳 session_id。"""
    session = repo.create_session(topic="議題", max_rounds=3, experts=["E0"])
    if status is not SessionStatus.Created:
        repo.set_status(session.id, status)
    return session.id


# ---------------------------------------------------------------------------
# AC-1：訂閱後即時收到引擎發佈的事件（fan-out，≤2s），符合信封 schema。
# ---------------------------------------------------------------------------
async def test_fanout_two_subscribers_receive_published_event(repo):
    session_id = _make_session(repo)
    bus = EventBus()
    c1, c2 = _Collect(stop_after=2), _Collect(stop_after=2)  # 快照(1) + 事件(1)

    t1 = asyncio.create_task(
        _drain(stream_session_events(c1, bus, repo, session_id, heartbeat_seconds=10))
    )
    t2 = asyncio.create_task(
        _drain(stream_session_events(c2, bus, repo, session_id, heartbeat_seconds=10))
    )
    # 等兩條連線皆已訂閱再發佈，確保事件必達兩者。
    while bus.subscriber_count(session_id) < 2:
        await asyncio.sleep(0)

    event = TokenChunk(session_id=session_id, round_number=1, expert_id=1, text="片段")
    await bus.publish(event)

    await asyncio.wait_for(asyncio.gather(t1, t2), timeout=2)

    for collected in (c1, c2):
        # 首筆為狀態快照（AC-2），次筆為即時事件。
        assert collected.messages[0]["type"] == "StatusChanged"
        live = collected.messages[1]
        assert live["type"] == "TokenChunk"
        # 信封 schema：{type, sessionId, ts, data}。
        assert set(live.keys()) == {"type", "sessionId", "ts", "data"}
        assert live["sessionId"] == session_id
        assert live["data"]["text"] == "片段"


# ---------------------------------------------------------------------------
# AC-2：重連先回放目前狀態快照（最新 status / 輪次與焦點）。
# ---------------------------------------------------------------------------
def test_build_snapshot_events_status_and_latest_round(repo):
    session_id = _make_session(repo, status=SessionStatus.Running)
    repo.create_round(session_id, 1)
    repo.create_round(session_id, 2)
    detail = repo.get_session_detail(session_id)
    expert_id = detail.experts[0].id
    r1, r2 = detail.rounds[0].id, detail.rounds[1].id
    repo.append_contribution(r1, expert_id, seq=0, viewpoint="v1", focus_after="第一輪焦點")
    repo.append_contribution(r2, expert_id, seq=0, viewpoint="v2", focus_after="第二輪焦點")

    events = build_snapshot_events(repo.get_session_detail(session_id))

    assert [e.type for e in events] == ["StatusChanged", "RoundStarted"]
    status_evt = events[0].to_dict()
    assert status_evt["data"]["status"] == "Running"
    round_evt = events[1].to_dict()
    # 取最新回合（roundNumber=2）與其最新焦點。
    assert round_evt["data"]["roundNumber"] == 2
    assert round_evt["data"]["focus"] == "第二輪焦點"


def test_build_snapshot_events_without_rounds_is_status_only(repo):
    session_id = _make_session(repo, status=SessionStatus.Created)
    events = build_snapshot_events(repo.get_session_detail(session_id))
    assert [e.type for e in events] == ["StatusChanged"]
    assert events[0].to_dict()["data"]["status"] == "Created"


async def test_stream_replays_snapshot_before_live_events(repo):
    """重連時 sink 先收到快照，client 不需重跑即可對齊。"""
    session_id = _make_session(repo, status=SessionStatus.Running)
    repo.create_round(session_id, 1)
    bus = EventBus()
    sink = _Collect(stop_after=2)  # StatusChanged + RoundStarted（皆為快照）

    await _drain(
        stream_session_events(sink, bus, repo, session_id, heartbeat_seconds=10)
    )

    assert [m["type"] for m in sink.messages] == ["StatusChanged", "RoundStarted"]
    assert sink.messages[0]["data"]["status"] == "Running"
    assert sink.messages[1]["data"]["roundNumber"] == 1


# ---------------------------------------------------------------------------
# AC-3：閒置達心跳間隔即送心跳 ping。
# ---------------------------------------------------------------------------
async def test_idle_connection_emits_heartbeat_ping(repo):
    session_id = _make_session(repo)  # Created、無回合 → 快照僅 1 筆
    bus = EventBus()
    sink = _Collect(stop_after=2)  # 快照(1) + 心跳(1)

    # 無事件發佈：快照後閒置即觸發心跳。
    await asyncio.wait_for(
        _drain(
            stream_session_events(sink, bus, repo, session_id, heartbeat_seconds=0.05)
        ),
        timeout=2,
    )

    assert sink.messages[0]["type"] == "StatusChanged"
    assert sink.messages[1] == {"type": "ping"}


# ---------------------------------------------------------------------------
# 端對端：真實 FastAPI + TestClient 接線。
# ---------------------------------------------------------------------------
@pytest.fixture
def db_engine(tmp_path, monkeypatch):
    """將 `EPS_DB_URL` 導向 tmp 檔並建表（模擬已 migrate 的 DB）。"""
    db_file = tmp_path / "app.db"
    monkeypatch.setenv("EPS_DB_URL", f"sqlite:///{db_file}")
    config.get_settings.cache_clear()
    eng = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()
        config.get_settings.cache_clear()


@pytest.fixture
def client(db_engine):
    with TestClient(main.app) as c:
        yield c


def _insert_session(engine, *, status=SessionStatus.Running) -> int:
    with DBSession(engine) as db:
        session = Session(topic="議題", max_rounds=3, status=status)
        db.add(session)
        db.commit()
        db.refresh(session)
        return session.id


# --- AC-4：不存在的會話 id → upgrade 回 404，不建立連線 ---
def test_ws_unknown_session_returns_404_and_no_connection(client):
    with pytest.raises(WebSocketDenialResponse) as exc_info:
        with client.websocket_connect("/sessions/999999/events"):
            pass
    assert exc_info.value.status_code == 404
    assert exc_info.value.json()["detail"]["code"] == "SESSION_NOT_FOUND"


# --- AC-2：連線即先收到目前狀態快照（端對端） ---
def test_ws_connect_replays_status_snapshot(client, db_engine):
    session_id = _insert_session(db_engine, status=SessionStatus.Running)
    with client.websocket_connect(f"/sessions/{session_id}/events") as ws:
        snapshot = ws.receive_json()
    assert snapshot["type"] == "StatusChanged"
    assert snapshot["sessionId"] == session_id
    assert snapshot["data"]["status"] == "Running"


# --- AC-3：閒置即送心跳 ping（端對端） ---
def test_ws_idle_emits_heartbeat(client, db_engine):
    session_id = _insert_session(db_engine, status=SessionStatus.Created)
    # 縮短心跳間隔以利測試；app.state 為 lifespan 由 settings 寫入，這裡就地覆寫。
    client.app.state.ws_heartbeat_seconds = 0.05
    with client.websocket_connect(f"/sessions/{session_id}/events") as ws:
        snapshot = ws.receive_json()
        heartbeat = ws.receive_json()
    assert snapshot["type"] == "StatusChanged"
    assert heartbeat == {"type": "ping"}
