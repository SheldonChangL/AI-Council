"""Story 4.1 — 領域物件與 WebSocket 事件型別（AC-1, AC-2, AC-3）。

驗證：
- AC-1：``eps/core/domain.py`` 存在 ``SessionRuntime`` / ``RoundState`` / ``ExpertRef``
  等執行期物件，且可建構。
- AC-2：``eps/core/events.py`` 涵蓋藍圖 §3.3 的 10 種事件型別。
- AC-3：任一事件序列化為 ``{type, sessionId, ts, data}`` 信封。
"""

from datetime import datetime, timezone

import pytest

from eps.core import events as ev
from eps.core.domain import ExpertRef, RoundState, SessionRuntime
from eps.core.events import (
    EVENT_CLASSES,
    EVENT_REGISTRY,
    EVENT_TYPES,
    Event,
    ExpertCompleted,
    ExpertStarted,
    FocusUpdated,
    ReportCompleted,
    RoundStarted,
    RoundSummary,
    SessionFailed,
    StatusChanged,
    TokenChunk,
    UsageStats,
)
from eps.data.models import SessionStatus


# AC-2：藍圖 §3.3 要求涵蓋的事件型別名稱。
EXPECTED_EVENT_TYPES = {
    "RoundStarted",
    "ExpertStarted",
    "TokenChunk",
    "ExpertCompleted",
    "FocusUpdated",
    "RoundSummary",
    "ReportCompleted",
    "SessionFailed",
    "StatusChanged",
    "UsageStats",
}


# ---------------------------------------------------------------------------
# AC-1：執行期領域物件存在且可建構。
# ---------------------------------------------------------------------------
def test_expert_ref_is_immutable_runtime_reference():
    ref = ExpertRef(id=1, name="經濟學家", order_index=0, persona_prompt="p")
    assert (ref.id, ref.name, ref.order_index, ref.persona_prompt) == (
        1,
        "經濟學家",
        0,
        "p",
    )
    with pytest.raises(Exception):
        ref.name = "改名"  # frozen：不可變


def test_round_state_accumulates_runtime_data():
    rs = RoundState(round_number=1)
    assert rs.focus == "" and rs.viewpoints == [] and rs.summary is None
    rs.focus = "通膨"
    rs.viewpoints.append("觀點A")
    rs.summary = "回合摘要"
    assert rs.viewpoints == ["觀點A"]


def test_session_runtime_aggregates_status_and_round():
    runtime = SessionRuntime(
        session_id=7,
        topic="是否升息",
        max_rounds=3,
        experts=[ExpertRef(id=1, name="A", order_index=0)],
        current_round=RoundState(round_number=1, focus="f"),
    )
    # status 重用持久層狀態機，預設為 Created。
    assert runtime.status is SessionStatus.Created
    assert runtime.experts[0].name == "A"
    assert runtime.current_round.round_number == 1


# ---------------------------------------------------------------------------
# AC-2：事件型別涵蓋藍圖 §3.3 全部名稱。
# ---------------------------------------------------------------------------
def test_event_types_cover_blueprint():
    assert EVENT_TYPES == EXPECTED_EVENT_TYPES
    # 每個名稱都有對應的可匯出類別，且 registry 一致。
    for name in EXPECTED_EVENT_TYPES:
        cls = getattr(ev, name)
        assert issubclass(cls, Event)
        assert EVENT_REGISTRY[name] is cls
    assert {cls.type for cls in EVENT_CLASSES} == EXPECTED_EVENT_TYPES


# ---------------------------------------------------------------------------
# AC-3：序列化信封 = {type, sessionId, ts, data}。
# ---------------------------------------------------------------------------
def _sample_event(cls):
    """為每個事件類別建構一個合法樣本（含必要 payload 欄位）。"""
    samples = {
        RoundStarted: dict(round_number=1, focus="f"),
        ExpertStarted: dict(round_number=1, expert_id=2, expert_name="A"),
        TokenChunk: dict(round_number=1, expert_id=2, text="片段"),
        ExpertCompleted: dict(round_number=1, expert_id=2, viewpoint="觀點"),
        FocusUpdated: dict(round_number=1, focus="新焦點"),
        RoundSummary: dict(round_number=1, summary="摘要"),
        ReportCompleted: dict(report="最終報告"),
        SessionFailed: dict(reason="逾時"),
        StatusChanged: dict(status=SessionStatus.Running.value),
        UsageStats: dict(stats={"calls": 3}),
    }
    return cls(session_id=42, **samples[cls])


@pytest.mark.parametrize("cls", EVENT_CLASSES, ids=lambda c: c.type)
def test_event_serializes_to_envelope(cls):
    payload = _sample_event(cls).to_dict()
    # 信封恰為四個鍵。
    assert set(payload) == {"type", "sessionId", "ts", "data"}
    assert payload["type"] == cls.type
    assert payload["sessionId"] == 42
    assert isinstance(payload["data"], dict)
    # ts 為 ISO-8601 字串，可被解析回 datetime。
    assert isinstance(payload["ts"], str)
    assert isinstance(datetime.fromisoformat(payload["ts"]), datetime)


def test_envelope_uses_camel_case_in_data():
    payload = ExpertStarted(
        session_id=1, round_number=2, expert_id=3, expert_name="A"
    ).to_dict()
    assert payload["data"] == {"roundNumber": 2, "expertId": 3, "expertName": "A"}


def test_status_changed_serializes_enum_value():
    payload = StatusChanged(session_id=1, status=SessionStatus.Failed.value).to_dict()
    assert payload["data"] == {"status": "Failed"}


def test_explicit_ts_is_preserved_as_iso():
    ts = datetime(2026, 6, 21, 12, 0, 0, tzinfo=timezone.utc)
    payload = ReportCompleted(session_id=1, ts=ts, report="r").to_dict()
    assert payload["ts"] == ts.isoformat()
    assert payload["data"] == {"report": "r"}
