"""Story 4.7 — 一場完整研討透過背景 JobManager 跑完並落地最終報告（AC-1~AC-3）。

整合 harness：以**真實** :meth:`JobManager.start`（非直接呼叫 :meth:`OrchestrationEngine.run`）
啟動背景任務，注入 :class:`FakeAdapter`、3 位專家、``max_rounds=2``，於真實 in-memory
SQLite ``Repository`` 與 in-process ``EventBus`` 上端到端驗證：

- AC-1：等背景任務跑完後，DB 中該會話 ``status=Completed`` 且最終報告非空。
- AC-2：訂閱 EventBus 觀測到的研討事件序列以 ``ReportCompleted`` 結尾，且每輪
  ``RoundSummary`` 都已落地（事件流＋持久化回合雙重確認）。背景任務於 Story 4.6
  另發的 ``UsageStats`` 屬會話結束後的監測事件，不屬研討序列。
- AC-3：任務完成後以「全新 :class:`Repository`」重新由 DB 讀取（模擬離開後回來），
  ``get_session_detail`` 取得全部 rounds / contributions 與最終報告（可恢復可查詢）。

需求對應：FR-9（最終綜整報告）/ FR-13（背景任務生命週期）/ NFR-5（持久化可恢復）。

備註：story 文字以 ``final_report_md`` 指稱最終報告，實際持久化欄位為
``Session.final_report``（亦由 ``SessionDetail.final_report`` 聚合曝露），此處對其驗證。
"""

import asyncio

import pytest
from sqlmodel import SQLModel, create_engine

from eps.adapters import FakeAdapter
from eps.core.bus import EventBus
from eps.core.engine import OrchestrationEngine
from eps.core.jobs import JobManager, JobState
from eps.data.models import SessionStatus
from eps.data.repository import Repository

EXPERTS = ["甲", "乙", "丙"]  # 3 位專家。
MAX_ROUNDS = 2


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


async def _collect_until(sub, target_type, *, timeout=2):
    """收集事件直到（含）某型別出現即停；逾時即視為失敗（避免測試掛死）。"""

    async def _loop():
        out = []
        async for ev in sub:
            out.append(ev)
            if ev.type == target_type:
                break
        return out

    return await asyncio.wait_for(_loop(), timeout=timeout)


async def test_full_session_runs_via_job_manager_and_lands_report(db_engine, repo):
    """AC-1 / AC-2 / AC-3：真實 JobManager 跑完一場完整研討並落地可查的最終報告。"""
    session = repo.create_session(
        topic="是否導入新框架", max_rounds=MAX_ROUNDS, experts=EXPERTS
    )
    bus = EventBus()
    sub = bus.subscribe(session.id)
    # 真實組裝：JobManager 包真實 OrchestrationEngine + FakeAdapter（非直呼 engine.run）。
    jm = JobManager(
        OrchestrationEngine(repo, FakeAdapter(), bus), repo, bus, max_concurrency=2
    )

    # AC-2：先訂閱再啟動，收集研討事件序列直到 ReportCompleted。
    collector = asyncio.create_task(_collect_until(sub, "ReportCompleted"))
    handle = jm.start(session.id)

    # 等背景任務完整跑完（與啟動端解耦，模擬離開後回來）。
    await asyncio.wait_for(handle.task, timeout=2)
    events = await collector

    assert handle.state == JobState.Finished
    assert handle.session_status == SessionStatus.Completed

    # --- AC-2：序列以 ReportCompleted 結尾，且每輪 RoundSummary 都已落地（事件流）---
    assert events[-1].type == "ReportCompleted"
    summary_rounds = [e.round_number for e in events if e.type == "RoundSummary"]
    assert summary_rounds == list(range(1, MAX_ROUNDS + 1))
    # 每個 RoundSummary 皆出現在 ReportCompleted 之前。
    report_idx = next(i for i, e in enumerate(events) if e.type == "ReportCompleted")
    assert all(
        i < report_idx
        for i, e in enumerate(events)
        if e.type == "RoundSummary"
    )

    # --- AC-1：DB 中該會話 status=Completed 且最終報告非空 ---
    detail = repo.get_session_detail(session.id)
    assert detail.session.status == SessionStatus.Completed
    assert detail.session.final_report
    assert detail.final_report == detail.session.final_report

    # --- AC-3：以全新 Repository 重新讀取（模擬離開後回來），取得完整聚合 ---
    fresh_repo = Repository(db_engine)
    reread = fresh_repo.get_session_detail(session.id)
    assert reread is not None
    # 全部回合落地（每輪一筆 RoundSummary 對應一個持久化 Round）。
    assert [r.round_number for r in reread.rounds] == list(range(1, MAX_ROUNDS + 1))
    # 全部發言落地：3 專家 × 2 輪 = 6 筆 contributions。
    assert len(reread.contributions) == len(EXPERTS) * MAX_ROUNDS
    # 最終報告可由持久化恢復查詢。
    assert reread.final_report
    assert reread.final_report == detail.session.final_report
