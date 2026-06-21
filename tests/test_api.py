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
