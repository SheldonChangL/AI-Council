"""Story 4.6 — JobManager 背景任務生命週期、併發 semaphore 與用量統計（AC-1~AC-3）。

以真實 in-memory SQLite ``Repository``、in-process ``EventBus`` 驗證；AC-1/AC-2 以可控
時序的引擎替身觀測背景任務與 semaphore 行為，AC-3 以真實 ``OrchestrationEngine`` +
``FakeAdapter`` 驗證用量發佈與持久化（含失敗路徑僅監測不中止）。
"""

import asyncio
import json

import pytest
from sqlmodel import SQLModel, create_engine

from eps.adapters import FakeAdapter
from eps.adapters.base import RetryExhaustedError
from eps.core.bus import EventBus
from eps.core.engine import OrchestrationEngine
from eps.core.jobs import JobManager, JobState, compute_usage
from eps.core.domain import SessionRuntime
from eps.data.models import SessionStatus
from eps.data.repository import Repository


@pytest.fixture
def db_engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def repo(db_engine):
    return Repository(db_engine)


async def _settle(turns: int = 20) -> None:
    """讓事件迴圈推進數個 turn，使已排程的背景任務跑到下一個 await 邊界。"""
    for _ in range(turns):
        await asyncio.sleep(0)


class _GatedEngine:
    """可控時序的引擎替身：``run`` 阻塞於 release 旗標（或取消），並記錄併發峰值。"""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.concurrent = 0
        self.max_concurrent = 0
        self.started: list[int] = []

    async def run(self, session_id, *, cancel_token=None):
        self.started.append(session_id)
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            waiters = [asyncio.ensure_future(self.release.wait())]
            if cancel_token is not None:
                waiters.append(asyncio.ensure_future(cancel_token.wait()))
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            for w in waiters:
                w.cancel()
            cancelled = cancel_token is not None and cancel_token.is_set()
            status = SessionStatus.Cancelled if cancelled else SessionStatus.Completed
            return SessionRuntime(
                session_id=session_id, topic="t", max_rounds=1, status=status
            )
        finally:
            self.concurrent -= 1


# ===========================================================================
# AC-1：start 建立與 HTTP 連線解耦的背景任務並立即回傳，可離開後再查詢狀態。
# ===========================================================================
async def test_start_returns_immediately_and_is_queryable(repo):
    session = repo.create_session(topic="議題", max_rounds=1, experts=["A"])
    engine = _GatedEngine()
    jm = JobManager(engine, repo, EventBus(), max_concurrency=2)

    handle = jm.start(session.id)

    # 立即回傳：背景任務尚未完成（與呼叫端執行緒解耦）。
    assert handle.task is not None
    assert not handle.task.done()
    # 可查詢狀態：start 後即可由 manager 查到同一把手。
    assert jm.status(session.id) is handle

    await _settle()
    assert handle.state == JobState.Running  # 已取得名額、引擎執行中。

    # 放行後正常結束，狀態查詢反映會話終態。
    engine.release.set()
    await asyncio.wait_for(handle.task, timeout=2)
    assert handle.state == JobState.Finished
    assert handle.session_status == SessionStatus.Completed
    assert jm.status(session.id).session_status == SessionStatus.Completed


async def test_status_none_for_unknown_session(repo):
    jm = JobManager(_GatedEngine(), repo, EventBus(), max_concurrency=2)
    assert jm.status(999) is None


async def test_start_is_idempotent_while_in_flight(repo):
    session = repo.create_session(topic="議題", max_rounds=1, experts=["A"])
    engine = _GatedEngine()
    jm = JobManager(engine, repo, EventBus(), max_concurrency=2)

    h1 = jm.start(session.id)
    h2 = jm.start(session.id)  # 仍在進行 → 回傳同一把手，不重複啟動。
    assert h1 is h2
    await _settle()
    assert len(engine.started) == 1

    engine.release.set()
    await asyncio.wait_for(h1.task, timeout=2)


# ===========================================================================
# AC-2：全域 semaphore 限制併發，超出上限者排隊；in-flight 不超過上限；狀態互不污染。
# ===========================================================================
async def test_semaphore_caps_in_flight_and_queues_excess(repo):
    limit = 2
    sessions = [
        repo.create_session(topic=f"S{i}", max_rounds=1, experts=["A"]).id
        for i in range(5)
    ]
    engine = _GatedEngine()
    jm = JobManager(engine, repo, EventBus(), max_concurrency=limit)

    handles = [jm.start(sid) for sid in sessions]
    await _settle()

    # in-flight 引擎執行數不超過上限，超出者仍在 Pending 排隊。
    assert engine.concurrent == limit
    assert engine.max_concurrent == limit
    assert len(engine.started) == limit
    running = [h for h in handles if h.state == JobState.Running]
    pending = [h for h in handles if h.state == JobState.Pending]
    assert len(running) == limit
    assert len(pending) == 5 - limit

    # 放行：排隊者依序取得名額，全程峰值不超過上限，最終全部完成。
    engine.release.set()
    await asyncio.wait_for(asyncio.gather(*(h.task for h in handles)), timeout=2)
    assert engine.max_concurrent == limit
    assert len(engine.started) == 5
    assert all(h.state == JobState.Finished for h in handles)


async def test_cancel_one_session_does_not_pollute_others(repo):
    s1 = repo.create_session(topic="S1", max_rounds=1, experts=["A"]).id
    s2 = repo.create_session(topic="S2", max_rounds=1, experts=["A"]).id
    engine = _GatedEngine()
    jm = JobManager(engine, repo, EventBus(), max_concurrency=2)

    h1 = jm.start(s1)
    h2 = jm.start(s2)
    await _settle()

    # 僅取消 s1：其取消旗標被 set，s2 不受影響。
    assert jm.cancel(s1) is True
    assert h2.cancel_token.is_set() is False

    engine.release.set()  # s2 正常放行。
    await asyncio.wait_for(asyncio.gather(h1.task, h2.task), timeout=2)

    assert h1.session_status == SessionStatus.Cancelled
    assert h2.session_status == SessionStatus.Completed


# ===========================================================================
# AC-3：會話結束發佈 UsageStats（輪次×專家用量）並持久化彙總，僅監測不中止（OPS-3）。
# ===========================================================================
async def _collect_until(sub, target_type, *, timeout=2):
    async def _loop():
        out = []
        async for ev in sub:
            out.append(ev)
            if ev.type == target_type:
                break
        return out

    return await asyncio.wait_for(_loop(), timeout=timeout)


async def test_usage_published_and_persisted_on_completion(repo):
    session = repo.create_session(topic="議題", max_rounds=2, experts=["A", "B"])
    bus = EventBus()
    sub = bus.subscribe(session.id)
    jm = JobManager(OrchestrationEngine(repo, FakeAdapter(), bus), repo, bus, max_concurrency=2)

    collector = asyncio.create_task(_collect_until(sub, "UsageStats"))
    handle = jm.start(session.id)
    await asyncio.wait_for(handle.task, timeout=2)
    events = await collector

    # 發佈 UsageStats：2 輪 × 2 專家 = 4 筆發言。
    usage_events = [e for e in events if e.type == "UsageStats"]
    assert len(usage_events) == 1
    stats = usage_events[0].stats
    assert stats["rounds"] == 2
    assert stats["experts"] == 2
    assert stats["contributions"] == 4
    # 每位專家各發言 2 次（每輪 1 次）。
    per_expert = {k: v for k, v in stats.items() if k.startswith("expert_")}
    assert len(per_expert) == 2
    assert all(v == 2 for v in per_expert.values())

    # 持久化彙總：usage_stats 欄位落地且與事件一致；不改變會話終態（僅監測不中止）。
    detail = repo.get_session_detail(session.id)
    assert detail.session.usage_stats is not None
    assert json.loads(detail.session.usage_stats) == stats
    assert detail.session.status == SessionStatus.Completed
    assert handle.usage == stats


async def test_usage_published_on_failure_without_aborting(repo):
    """失敗路徑仍發佈並持久化用量（僅監測不中止）：不臆造、不改變失敗結果。"""
    session = repo.create_session(topic="議題", max_rounds=1, experts=["A", "B"])
    adapter = FakeAdapter(
        viewpoints=["vA"],
        focuses=["fA"],
        errors={"invoke": RetryExhaustedError("重試耗盡")},
        error_after={"invoke": 1},  # 第一位成功落地，第二位 invoke 失敗。
    )
    bus = EventBus()
    sub = bus.subscribe(session.id)
    jm = JobManager(OrchestrationEngine(repo, adapter, bus), repo, bus, max_concurrency=2)

    collector = asyncio.create_task(_collect_until(sub, "UsageStats"))
    handle = jm.start(session.id)
    await asyncio.wait_for(handle.task, timeout=2)
    events = await collector

    # 會話以 Failed 終結（用量統計未中止/未改變結果）。
    assert handle.session_status == SessionStatus.Failed
    detail = repo.get_session_detail(session.id)
    assert detail.session.status == SessionStatus.Failed
    assert detail.session.final_report is None  # 不臆造。

    # 仍發佈並持久化部分用量：第一位專家的 1 筆發言被計入。
    usage = [e for e in events if e.type == "UsageStats"][0].stats
    assert usage["contributions"] == 1
    assert usage["rounds"] == 1
    assert usage["experts"] == 2
    assert json.loads(detail.session.usage_stats) == usage


# --- compute_usage 純函式：直接以持久化聚合驗證彙總正確 ---
async def test_compute_usage_counts_per_expert(repo):
    session = repo.create_session(topic="議題", max_rounds=3, experts=["A", "B"])
    bus = EventBus()
    await OrchestrationEngine(repo, FakeAdapter(), bus).run(session.id)

    detail = repo.get_session_detail(session.id)
    stats = compute_usage(detail)
    assert stats["rounds"] == 3
    assert stats["experts"] == 2
    assert stats["contributions"] == 6  # 3 輪 × 2 專家。
    per_expert = {k: v for k, v in stats.items() if k.startswith("expert_")}
    assert sorted(per_expert.values()) == [3, 3]
