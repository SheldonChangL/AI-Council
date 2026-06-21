"""Story 2.6 — 歷史會話/模板庫查詢與會話刪除 API（AC-1~AC-4）。

驗證對運行中的 FastAPI 服務發出 HTTP 請求的行為：
- AC-1：`GET /sessions` 依 createdAt 遞減回傳 `{id, topic, status, createdAt}`，支援 limit。
- AC-2：`GET /personas` 回傳內建模板（≥3）。
- AC-3：`GET /sessions/{id}` 回傳完整聚合；不存在回 404 `SESSION_NOT_FOUND`。
- AC-4：`DELETE /sessions/{id}` 回 204，且後續 `GET /sessions/{id}` 回 404。
"""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine, select
from sqlmodel import Session as DBSession

import eps.config as config
import eps.main as main
from eps.api.routes import get_job_manager
from eps.api.schemas import EXPERTS_MAX
from eps.data.models import (
    Contribution,
    Round,
    Session,
    SessionExpert,
    SessionStatus,
)


@pytest.fixture
def db_engine(tmp_path, monkeypatch):
    """將 `EPS_DB_URL` 導向 tmp 檔並建立全部資料表（模擬已 migrate 的 DB）。"""
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


@pytest.fixture
def client(db_engine):
    """啟動 app（觸發 lifespan：連接同一 DB 檔並冪等 seed personas）。"""
    with TestClient(main.app) as c:
        yield c


def _insert_session(
    engine, *, topic="議題", status=SessionStatus.Created, max_rounds=3
) -> int:
    with DBSession(engine) as db:
        session = Session(topic=topic, max_rounds=max_rounds, status=status)
        db.add(session)
        db.commit()
        db.refresh(session)
        return session.id


def _build_full_session(engine, *, final_report=None) -> dict:
    """建立含 2 專家、2 回合、3 發言的完整會話，回傳關鍵 id。"""
    with DBSession(engine) as db:
        session = Session(topic="完整議題", max_rounds=3, final_report=final_report)
        db.add(session)
        db.flush()
        experts = [
            SessionExpert(session_id=session.id, name="E0", order_index=0),
            SessionExpert(session_id=session.id, name="E1", order_index=1),
        ]
        rounds = [
            Round(session_id=session.id, round_number=1),
            Round(session_id=session.id, round_number=2),
        ]
        db.add_all(experts + rounds)
        db.flush()
        db.add_all(
            [
                Contribution(round_id=rounds[0].id, session_expert_id=experts[0].id,
                             seq=0, viewpoint="v0"),
                Contribution(round_id=rounds[0].id, session_expert_id=experts[1].id,
                             seq=1, viewpoint="v1"),
                Contribution(round_id=rounds[1].id, session_expert_id=experts[0].id,
                             seq=0, viewpoint="v2"),
            ]
        )
        db.commit()
        return {
            "session_id": session.id,
            "round_ids": [r.id for r in rounds],
            "expert_ids": [e.id for e in experts],
        }


# --- AC-1：GET /sessions 依 createdAt 遞減並支援 limit ---
def test_list_sessions_orders_recent_first(client, db_engine):
    first = _insert_session(db_engine, topic="A")
    second = _insert_session(db_engine, topic="B")

    resp = client.get("/sessions", params={"limit": 10})

    assert resp.status_code == 200
    body = resp.json()
    assert [s["id"] for s in body] == [second, first]
    # 形狀為 {id, topic, status, createdAt}。
    assert set(body[0].keys()) == {"id", "topic", "status", "createdAt"}
    assert body[0]["topic"] == "B"
    assert body[0]["status"] == "Created"


def test_list_sessions_respects_limit(client, db_engine):
    for i in range(5):
        _insert_session(db_engine, topic=f"S{i}")

    resp = client.get("/sessions", params={"limit": 2})

    assert resp.status_code == 200
    assert len(resp.json()) == 2


# --- AC-2：GET /personas 回傳內建模板（≥3）---
def test_list_personas_returns_builtin_templates(client):
    resp = client.get("/personas")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) >= 3
    names = {p["name"] for p in body}
    assert {"市場分析師", "技術架構師", "倫理學家"} <= names
    assert all(p["builtin"] is True for p in body)
    assert all(p["systemPrompt"] for p in body)


# --- AC-3：GET /sessions/{id} 完整聚合與 404 ---
def test_get_session_detail_returns_full_aggregate(client, db_engine):
    built = _build_full_session(db_engine, final_report="最終綜整")

    resp = client.get(f"/sessions/{built['session_id']}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["session"]["id"] == built["session_id"]
    assert body["session"]["createdAt"]
    assert body["finalReport"] == "最終綜整"
    assert [e["orderIndex"] for e in body["experts"]] == [0, 1]
    assert [r["roundNumber"] for r in body["rounds"]] == [1, 2]
    assert [(c["roundId"], c["seq"]) for c in body["contributions"]] == [
        (built["round_ids"][0], 0),
        (built["round_ids"][0], 1),
        (built["round_ids"][1], 0),
    ]


def test_get_session_detail_missing_returns_404(client):
    resp = client.get("/sessions/9999")

    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "SESSION_NOT_FOUND"


# --- AC-4：DELETE /sessions/{id} 回 204 且後續查詢回 404 ---
def test_delete_session_returns_204_then_404(client, db_engine):
    session_id = _insert_session(db_engine, topic="待刪")

    resp = client.delete(f"/sessions/{session_id}")
    assert resp.status_code == 204
    assert resp.content == b""

    follow_up = client.get(f"/sessions/{session_id}")
    assert follow_up.status_code == 404
    assert follow_up.json()["detail"]["code"] == "SESSION_NOT_FOUND"


def test_delete_missing_session_returns_404(client):
    resp = client.delete("/sessions/9999")

    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "SESSION_NOT_FOUND"


# === Story 5.2 — POST /sessions 建立會話、驗證並排程背景任務（AC-1~AC-4）===


class _StubJobManager:
    """記錄被排程／取消的 session_id，避免測試驅動真實背景任務／CLI。"""

    def __init__(self) -> None:
        self.started: list[int] = []
        self.cancelled: list[int] = []

    def start(self, session_id: int) -> None:
        self.started.append(session_id)

    def cancel(self, session_id: int) -> bool:
        self.cancelled.append(session_id)
        return True


@pytest.fixture
def jobs_stub(client):
    """以 stub 覆寫 get_job_manager，捕捉排程呼叫（不啟動真實背景任務）。"""
    stub = _StubJobManager()
    main.app.dependency_overrides[get_job_manager] = lambda: stub
    try:
        yield stub
    finally:
        main.app.dependency_overrides.pop(get_job_manager, None)


def _valid_payload(**overrides) -> dict:
    payload = {
        "topic": "是否升息",
        "maxRounds": 3,
        "experts": [
            {"name": "經濟學家", "personaPrompt": "你是經濟學家"},
            {"name": "工程師", "sourceTemplateId": None},
        ],
    }
    payload.update(overrides)
    return payload


# --- AC-1：合法 payload → 202 {sessionId, status:"Created"} 並排程背景任務 ---
def test_create_session_returns_202_and_schedules_job(client, db_engine, jobs_stub):
    resp = client.post("/sessions", json=_valid_payload())

    assert resp.status_code == 202
    body = resp.json()
    assert set(body.keys()) == {"sessionId", "status"}
    assert body["status"] == "Created"
    session_id = body["sessionId"]

    # 背景任務已排程（與 HTTP 連線解耦，將進入 ValidatingSource gate）。
    assert jobs_stub.started == [session_id]

    # 會話與專家已落地。
    with DBSession(db_engine) as db:
        session = db.get(Session, session_id)
        assert session is not None
        assert session.topic == "是否升息"
        assert session.status == SessionStatus.Created
        experts = db.exec(
            select(SessionExpert).where(SessionExpert.session_id == session_id)
        ).all()
        assert [e.name for e in experts] == ["經濟學家", "工程師"]


# --- AC-2：maxRounds 越界或 topic 空 → 422 INVALID_INPUT ---
@pytest.mark.parametrize("bad_rounds", [0, 21])
def test_create_session_invalid_max_rounds_returns_422(client, jobs_stub, bad_rounds):
    resp = client.post("/sessions", json=_valid_payload(maxRounds=bad_rounds))

    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "INVALID_INPUT"
    assert jobs_stub.started == []


@pytest.mark.parametrize("bad_topic", ["", "   "])
def test_create_session_blank_topic_returns_422(client, jobs_stub, bad_topic):
    resp = client.post("/sessions", json=_valid_payload(topic=bad_topic))

    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "INVALID_INPUT"
    assert jobs_stub.started == []


# --- AC-3：experts 數量超過上限 → 400 TOO_MANY_EXPERTS ---
def test_create_session_too_many_experts_returns_400(client, jobs_stub):
    too_many = [{"name": f"E{i}"} for i in range(EXPERTS_MAX + 1)]
    resp = client.post("/sessions", json=_valid_payload(experts=too_many))

    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "TOO_MANY_EXPERTS"
    assert jobs_stub.started == []


# --- AC-4：相同 Idempotency-Key 的重複請求 → 同一 sessionId（不重複排程）---
def test_create_session_idempotent_returns_same_session_id(client, jobs_stub):
    headers = {"Idempotency-Key": "abc-123"}

    first = client.post("/sessions", json=_valid_payload(), headers=headers)
    second = client.post("/sessions", json=_valid_payload(), headers=headers)

    assert first.status_code == 202
    assert second.status_code == 202
    session_id = first.json()["sessionId"]
    assert second.json()["sessionId"] == session_id
    # 僅第一次真正建立並排程；冪等重播不重複排程。
    assert jobs_stub.started == [session_id]


def test_create_session_distinct_keys_create_distinct_sessions(client, jobs_stub):
    first = client.post(
        "/sessions", json=_valid_payload(), headers={"Idempotency-Key": "k1"}
    )
    second = client.post(
        "/sessions", json=_valid_payload(), headers={"Idempotency-Key": "k2"}
    )

    assert first.json()["sessionId"] != second.json()["sessionId"]
    assert len(jobs_stub.started) == 2


# === Story 5.3 — 取消與重試端點（FR-14, OPS-2 / AC-1~AC-3）===


def _status_of(engine, session_id: int) -> SessionStatus:
    with DBSession(engine) as db:
        return db.get(Session, session_id).status


# --- AC-1：取消 Running 會話 → 200 {status:"Cancelled"} 並 signal 取消 ---
def test_cancel_running_session_returns_200_cancelled(client, db_engine, jobs_stub):
    session_id = _insert_session(db_engine, status=SessionStatus.Running)

    resp = client.post(f"/sessions/{session_id}/cancel")

    assert resp.status_code == 200
    assert resp.json() == {"status": "Cancelled"}
    # 背景任務取消旗標已 signal，且終態已權威落地。
    assert jobs_stub.cancelled == [session_id]
    assert _status_of(db_engine, session_id) == SessionStatus.Cancelled


@pytest.mark.parametrize(
    "non_terminal", [SessionStatus.Created, SessionStatus.ValidatingSource]
)
def test_cancel_non_terminal_states_are_cancellable(
    client, db_engine, jobs_stub, non_terminal
):
    session_id = _insert_session(db_engine, status=non_terminal)

    resp = client.post(f"/sessions/{session_id}/cancel")

    assert resp.status_code == 200
    assert resp.json()["status"] == "Cancelled"
    assert _status_of(db_engine, session_id) == SessionStatus.Cancelled


# --- AC-1：取消終態會話 → 409 NOT_CANCELLABLE，不 signal、狀態不變 ---
@pytest.mark.parametrize(
    "terminal",
    [
        SessionStatus.Completed,
        SessionStatus.Failed,
        SessionStatus.SourceInvalid,
        SessionStatus.Cancelled,
    ],
)
def test_cancel_terminal_session_returns_409(client, db_engine, jobs_stub, terminal):
    session_id = _insert_session(db_engine, status=terminal)

    resp = client.post(f"/sessions/{session_id}/cancel")

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "NOT_CANCELLABLE"
    assert jobs_stub.cancelled == []
    assert _status_of(db_engine, session_id) == terminal


def test_cancel_missing_session_returns_404(client, jobs_stub):
    resp = client.post("/sessions/9999/cancel")

    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "SESSION_NOT_FOUND"
    assert jobs_stub.cancelled == []


# --- AC-2：重試 SourceInvalid/Failed → 202 {status:"ValidatingSource"} 並重新排程 ---
@pytest.mark.parametrize(
    "retryable", [SessionStatus.SourceInvalid, SessionStatus.Failed]
)
def test_retry_failed_session_returns_202_validating(
    client, db_engine, jobs_stub, retryable
):
    session_id = _insert_session(db_engine, status=retryable)

    resp = client.post(f"/sessions/{session_id}/retry")

    assert resp.status_code == 202
    assert resp.json() == {"status": "ValidatingSource"}
    # 已重新排程，且起點狀態已權威落地。
    assert jobs_stub.started == [session_id]
    assert _status_of(db_engine, session_id) == SessionStatus.ValidatingSource


# --- AC-3：重試 Completed → 409 NOT_RETRYABLE，不重新排程、狀態不變 ---
def test_retry_completed_session_returns_409(client, db_engine, jobs_stub):
    session_id = _insert_session(db_engine, status=SessionStatus.Completed)

    resp = client.post(f"/sessions/{session_id}/retry")

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "NOT_RETRYABLE"
    assert jobs_stub.started == []
    assert _status_of(db_engine, session_id) == SessionStatus.Completed


@pytest.mark.parametrize(
    "non_retryable",
    [
        SessionStatus.Created,
        SessionStatus.ValidatingSource,
        SessionStatus.Running,
        SessionStatus.Cancelled,
    ],
)
def test_retry_non_retryable_states_return_409(
    client, db_engine, jobs_stub, non_retryable
):
    session_id = _insert_session(db_engine, status=non_retryable)

    resp = client.post(f"/sessions/{session_id}/retry")

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "NOT_RETRYABLE"
    assert jobs_stub.started == []
    assert _status_of(db_engine, session_id) == non_retryable


def test_retry_missing_session_returns_404(client, jobs_stub):
    resp = client.post("/sessions/9999/retry")

    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "SESSION_NOT_FOUND"
    assert jobs_stub.started == []


# === Story 5.4 — 最終報告匯出為 Markdown（FR-17 / 藍圖 A6 / AC-1~AC-3）===


def _insert_completed_session(engine, *, report="# 最終報告\n\n綜整結論。") -> int:
    """建立一場已落地最終報告的 Completed 會話，回傳 session id。"""
    with DBSession(engine) as db:
        session = Session(
            topic="議題",
            max_rounds=3,
            status=SessionStatus.Completed,
            final_report=report,
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        return session.id


# --- AC-1：Completed 會話 → 200、text/markdown、附檔名 ---
def test_export_report_returns_200_markdown_with_attachment(client, db_engine):
    report = "# 最終報告\n\n綜整結論。"
    session_id = _insert_completed_session(db_engine, report=report)

    resp = client.get(f"/sessions/{session_id}/report.md")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    disposition = resp.headers["content-disposition"]
    assert "attachment" in disposition
    assert f'filename="session-{session_id}-report.md"' in disposition
    # 內容即落地的最終報告本體。
    assert resp.text == report


# --- AC-2：尚未產出報告的會話 → 409 REPORT_NOT_READY ---
def test_export_report_not_ready_returns_409(client, db_engine):
    session_id = _insert_session(db_engine, status=SessionStatus.Running)

    resp = client.get(f"/sessions/{session_id}/report.md")

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "REPORT_NOT_READY"


# --- AC-3：不存在的會話 → 404 SESSION_NOT_FOUND ---
def test_export_report_missing_session_returns_404(client):
    resp = client.get("/sessions/9999/report.md")

    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "SESSION_NOT_FOUND"
