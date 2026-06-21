"""Story 4.4 — OrchestrationEngine 多輪序列編排狀態機（AC-1, AC-2, AC-3）。

以 ``FakeAdapter`` 決定性注入 LLM 行為、in-memory SQLite 注入 ``Repository``、
in-process ``EventBus`` 收集事件，驗證引擎的事件序列、里程碑落地與收尾報告
（FR-5~FR-9）。
"""

import asyncio

import pytest
from sqlmodel import SQLModel, create_engine

from eps.adapters import FakeAdapter
from eps.adapters.base import SourceError
from eps.core.bus import EventBus
from eps.core.engine import OrchestrationEngine
from eps.data.models import SessionStatus
from eps.data.repository import Repository


@pytest.fixture
def engine():
    """乾淨的 in-memory SQLite，建立全部資料表。"""
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def repo(engine):
    return Repository(engine)


def _token(ev):
    """事件序列斷言用 token：StatusChanged 取其 status，其餘取事件 type。"""
    return ev.status if ev.type == "StatusChanged" else ev.type


async def _collect(sub, stop_token="ReportCompleted"):
    """收集事件直到（含）token 為 ``stop_token`` 的事件出現。"""
    out = []
    async for ev in sub:
        out.append(ev)
        if _token(ev) == stop_token:
            break
    return out


async def _run_and_collect(repo, adapter, session_id, *, stop_token="ReportCompleted"):
    """訂閱、併發收集事件並執行引擎，回傳 (events, runtime)。"""
    bus = EventBus()
    sub = bus.subscribe(session_id)
    engine = OrchestrationEngine(repo, adapter, bus)
    task = asyncio.create_task(_collect(sub, stop_token))
    runtime = await engine.run(session_id)
    events = await asyncio.wait_for(task, timeout=2)
    return events, runtime


# ---------------------------------------------------------------------------
# AC-1：2 位專家、max_rounds=2 的完整事件序列。
# ---------------------------------------------------------------------------
async def test_full_event_sequence_two_experts_two_rounds(repo):
    session = repo.create_session(topic="是否升息", max_rounds=2, experts=["A", "B"])
    adapter = FakeAdapter()

    events, _ = await _run_and_collect(repo, adapter, session.id)

    expert_block = ["ExpertStarted", "ExpertCompleted", "FocusUpdated"]
    round_block = ["RoundStarted"] + expert_block * 2 + ["RoundSummary"]
    expected = ["ValidatingSource", "Running"] + round_block * 2 + ["ReportCompleted"]
    assert [_token(e) for e in events] == expected


# AC-1：所有事件皆繫結同一 sessionId。
async def test_events_bound_to_session_id(repo):
    session = repo.create_session(topic="T", max_rounds=1, experts=["A"])
    events, _ = await _run_and_collect(repo, FakeAdapter(), session.id)
    assert all(e.session_id == session.id for e in events)


# ---------------------------------------------------------------------------
# AC-2：每位專家發言後立即 refine_focus，並以 append-only 落地 viewpoint/focus_after。
# ---------------------------------------------------------------------------
async def test_refine_focus_called_per_expert_and_landed(repo):
    session = repo.create_session(topic="議題", max_rounds=2, experts=["A", "B"])
    adapter = FakeAdapter(
        viewpoints=["v1", "v2", "v3", "v4"],
        focuses=["f1", "f2", "f3", "f4"],
    )

    await _run_and_collect(repo, adapter, session.id)

    # 每位專家：invoke 之後緊接著 refine_focus（引數契約 (focus, viewpoint)）。
    method_seq = [name for name, _ in adapter.calls]
    invoke_idx = [i for i, m in enumerate(method_seq) if m == "invoke"]
    for i in invoke_idx:
        assert method_seq[i + 1] == "refine_focus"
    assert method_seq.count("invoke") == 4
    assert method_seq.count("refine_focus") == 4

    # append-only 落地：2 輪 × 2 專家 = 4 筆，viewpoint 與 focus_after 皆落地。
    detail = repo.get_session_detail(session.id)
    assert len(detail.contributions) == 4
    assert [c.viewpoint for c in detail.contributions] == ["v1", "v2", "v3", "v4"]
    assert [c.focus_after for c in detail.contributions] == ["f1", "f2", "f3", "f4"]
    # 兩個回合各自落地，seq 於回合內自 0 起算。
    assert len(detail.rounds) == 2
    assert sorted(c.seq for c in detail.contributions) == [0, 0, 1, 1]


# AC-2：refine_focus 緊接 ExpertCompleted 之後、FocusUpdated 之前（焦點即時收斂）。
async def test_focus_updated_reflects_refined_focus(repo):
    session = repo.create_session(topic="議題", max_rounds=1, experts=["A"])
    adapter = FakeAdapter(viewpoints=["v1"], focuses=["收斂後焦點"])
    events, _ = await _run_and_collect(repo, adapter, session.id)

    focus_events = [e for e in events if e.type == "FocusUpdated"]
    assert len(focus_events) == 1
    assert focus_events[0].focus == "收斂後焦點"


# ---------------------------------------------------------------------------
# AC-3：收尾依全程演進脈絡產出最終報告並落地，狀態轉為 Completed。
# ---------------------------------------------------------------------------
async def test_final_report_composed_and_persisted(repo):
    session = repo.create_session(topic="主題", max_rounds=2, experts=["A", "B"])
    adapter = FakeAdapter(round_summaries=["R1", "R2"], final_report="最終報告")

    events, runtime = await _run_and_collect(repo, adapter, session.id)

    # compose_final_report 收到議題與「全程各輪總結」（最後一輪總結含於其中）。
    assert ("compose_final_report", ("主題", ("R1", "R2"))) in adapter.calls

    # 報告落地、狀態轉為 Completed。
    detail = repo.get_session_detail(session.id)
    assert detail.final_report == "最終報告"
    assert detail.session.status == SessionStatus.Completed
    assert runtime.status == SessionStatus.Completed

    # 對外完成信號為 ReportCompleted（非 StatusChanged(Completed)）。
    assert events[-1].type == "ReportCompleted"
    assert events[-1].report == "最終報告"
    assert not any(_token(e) == "Completed" for e in events)


# AC-3：round_summaries 依序累積，最後一輪總結為序列末項。
async def test_round_summaries_accumulate_in_order(repo):
    session = repo.create_session(topic="主題", max_rounds=3, experts=["A"])
    adapter = FakeAdapter(round_summaries=["S1", "S2", "S3"], final_report="rep")
    await _run_and_collect(repo, adapter, session.id)
    composed = [args for name, args in adapter.calls if name == "compose_final_report"]
    assert composed == [("主題", ("S1", "S2", "S3"))]


# ---------------------------------------------------------------------------
# 來源驗證失敗：轉為 SourceInvalid 並提前結束（不進入 Running / 回合）。
# ---------------------------------------------------------------------------
async def test_source_invalid_short_circuits(repo):
    session = repo.create_session(topic="主題", max_rounds=2, experts=["A"])
    adapter = FakeAdapter(source_error=SourceError("來源不可用"))

    events, runtime = await _run_and_collect(
        repo, adapter, session.id, stop_token="SourceInvalid"
    )

    # 先 ValidatingSource，來源失敗後第二個 StatusChanged 為 SourceInvalid。
    assert [_token(e) for e in events] == ["ValidatingSource", "SourceInvalid"]
    assert runtime.status == SessionStatus.SourceInvalid
    detail = repo.get_session_detail(session.id)
    assert detail.session.status == SessionStatus.SourceInvalid
    assert detail.rounds == []
    # 來源失敗不應呼叫任何 LLM 推進方法。
    assert [name for name, _ in adapter.calls] == ["validate_source"]
