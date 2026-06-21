"""eps 背景任務生命週期、併發 semaphore 與用量統計（Story 4.6 / FR-13, NFR-4, OPS-3）。

:class:`JobManager` 將「長時會話編排」承載為背景 asyncio 任務，與 HTTP 連線解耦：
:meth:`JobManager.start` 立即回傳 :class:`JobHandle`，呼叫端（HTTP 連線）可離開後再以
:meth:`JobManager.status` 查詢（AC-1）。全域 :class:`asyncio.Semaphore` 限制同時 in-flight
的引擎執行數（→CLI 子行程數），超出上限者排隊等待（AC-2）。每場會話各自持有獨立
:class:`JobHandle` 與取消旗標，狀態互不污染。

設計（皆以既有契約為依據，非發明）：

- **背景任務生命週期**：``start`` 以 :func:`asyncio.create_task` 建立背景任務並登錄到
  ``session_id → JobHandle`` 表後即回傳；任務內先 acquire semaphore 再呼叫
  :meth:`OrchestrationEngine.run`。等待名額期間 job 處於 ``Pending``，不佔用 in-flight
  名額（AC-2）。
- **取消管道**：沿用 Story 4.5 的注入式 ``cancel_token: asyncio.Event``，由 ``JobHandle``
  持有；:meth:`JobManager.cancel` set 之，引擎於回合／專家邊界轉入 ``Cancelled``。
- **用量統計（僅監測不中止，OPS-3）**：會話結束（無論 Completed / Failed / Cancelled /
  SourceInvalid）後於 ``finally`` 區塊以 :meth:`Repository.get_session_detail` 彙總
  「輪次×專家用量」，發佈 :class:`~eps.core.events.UsageStats` 事件並以
  :meth:`Repository.save_usage_stats` 持久化。統計自身的任何失敗只記錄、絕不影響
  會話結果。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Mapping, Optional, Protocol

from eps.core.bus import EventBus
from eps.core.events import UsageStats
from eps.data.models import SessionStatus
from eps.data.repository import Repository, SessionDetail

logger = logging.getLogger(__name__)


class _Runnable(Protocol):
    """JobManager 對引擎的最小依賴：可被取消旗標中斷的非同步 ``run``。

    對齊 :meth:`eps.core.engine.OrchestrationEngine.run` 的契約：回傳具備 ``status``
    （:class:`SessionStatus`）的終態執行期物件。以 Protocol 表述以利測試注入替身。
    """

    async def run(
        self, session_id: int, *, cancel_token: Optional[asyncio.Event] = ...
    ) -> object: ...


class JobState(str, Enum):
    """背景任務生命週期狀態（與會話領域狀態 :class:`SessionStatus` 分離）。

    描述「承載會話的背景任務」本身的階段，而非會話的領域狀態：

    - ``Pending``：已排入，等待 semaphore 名額（尚未佔用 in-flight 名額）。
    - ``Running``：已取得名額，引擎執行中。
    - ``Finished``：引擎正常結束（會話自身終態見 :attr:`JobHandle.session_status`）。
    - ``Errored``：背景任務拋出未預期例外（見 :attr:`JobHandle.error`）。
    """

    Pending = "Pending"
    Running = "Running"
    Finished = "Finished"
    Errored = "Errored"


@dataclass
class JobHandle:
    """單一會話背景任務的可查詢把手。

    ``session_status`` 為引擎正常結束後落地的會話終態（``Errored`` 時為 None）；
    ``usage`` 為彙總後的用量計數；``cancel_token`` 為注入引擎的取消旗標。
    """

    session_id: int
    state: JobState = JobState.Pending
    session_status: Optional[SessionStatus] = None
    usage: Optional[Mapping[str, int]] = None
    error: Optional[BaseException] = None
    cancel_token: asyncio.Event = field(default_factory=asyncio.Event)
    task: Optional["asyncio.Task[None]"] = None


def compute_usage(detail: SessionDetail) -> Dict[str, int]:
    """由持久化的會話聚合彙總「輪次×專家用量」（Story 4.6 / OPS-3）。

    僅讀取已落地的 append-only 里程碑（rounds / contributions），故對 Completed 與
    失敗／取消（部分結果）皆成立：

    - ``rounds``：已落地的回合數。
    - ``experts``：參與專家數。
    - ``contributions``：實際發生的發言數（即「輪次×專家」實際執行的格數）。
    - ``expert_<id>``：每位專家的發言次數（專家用量分解）。
    """
    stats: Dict[str, int] = {
        "rounds": len(detail.rounds),
        "experts": len(detail.experts),
        "contributions": len(detail.contributions),
    }
    per_expert: Dict[int, int] = {e.id: 0 for e in detail.experts}
    for contribution in detail.contributions:
        per_expert[contribution.session_expert_id] = (
            per_expert.get(contribution.session_expert_id, 0) + 1
        )
    for expert_id, count in per_expert.items():
        stats[f"expert_{expert_id}"] = count
    return stats


class JobManager:
    """以背景任務承載會話編排，全域 semaphore 限制併發，會話結束發佈用量統計。"""

    def __init__(
        self,
        engine: _Runnable,
        repo: Repository,
        bus: EventBus,
        *,
        max_concurrency: int,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError(f"max_concurrency 必須 ≥ 1，得到 {max_concurrency}")
        self._engine = engine
        self._repo = repo
        self._bus = bus
        # 全域併發閘門（NFR-4：上限 < 10 由 Settings 驗證），限制 in-flight 引擎執行數。
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._jobs: Dict[int, JobHandle] = {}

    def start(self, session_id: int) -> JobHandle:
        """為 ``session_id`` 建立背景任務並**立即回傳**把手（AC-1）。

        與 HTTP 連線解耦：呼叫端可在此後離開，再以 :meth:`status` 查詢。對「仍在進行」
        的同一會話重複呼叫為冪等（回傳既有把手），避免重複啟動污染狀態。
        """
        existing = self._jobs.get(session_id)
        if existing is not None and existing.state in (
            JobState.Pending,
            JobState.Running,
        ):
            return existing
        handle = JobHandle(session_id=session_id)
        handle.task = asyncio.create_task(self._run_job(handle))
        self._jobs[session_id] = handle
        return handle

    def status(self, session_id: int) -> Optional[JobHandle]:
        """查詢某會話的背景任務把手；未啟動過回傳 ``None``（AC-1）。"""
        return self._jobs.get(session_id)

    def cancel(self, session_id: int) -> bool:
        """請求取消某會話：set 其取消旗標（引擎於邊界轉入 ``Cancelled``）。

        會話未啟動或已結束回傳 ``False``；否則 set 旗標並回傳 ``True``。
        """
        handle = self._jobs.get(session_id)
        if handle is None or handle.state in (JobState.Finished, JobState.Errored):
            return False
        handle.cancel_token.set()
        return True

    async def _run_job(self, handle: JobHandle) -> None:
        """背景任務本體：semaphore 閘門內執行引擎，結束後彙總用量（僅監測不中止）。"""
        async with self._semaphore:  # AC-2：超出上限者於此排隊，不佔 in-flight 名額。
            handle.state = JobState.Running
            try:
                runtime = await self._engine.run(
                    handle.session_id, cancel_token=handle.cancel_token
                )
                handle.session_status = getattr(runtime, "status", None)
                handle.state = JobState.Finished
            except Exception as exc:  # noqa: BLE001 - 未預期例外不得污染其他會話。
                handle.error = exc
                handle.state = JobState.Errored
                logger.exception(
                    "background job for session %s failed unexpectedly",
                    handle.session_id,
                )
            finally:
                # AC-3 / OPS-3：一場會話結束即統計，成功/失敗/取消皆發佈並持久化。
                await self._publish_usage(handle)

    async def _publish_usage(self, handle: JobHandle) -> None:
        """彙總、持久化並發佈會話用量（僅監測不中止：任何失敗只記錄不上拋）。"""
        try:
            detail = self._repo.get_session_detail(handle.session_id)
            if detail is None:
                return
            stats = compute_usage(detail)
            handle.usage = stats
            self._repo.save_usage_stats(
                handle.session_id, json.dumps(stats, sort_keys=True)
            )
            await self._bus.publish(
                UsageStats(session_id=handle.session_id, stats=stats)
            )
        except Exception:  # noqa: BLE001 - 僅監測不中止（OPS-3）。
            logger.exception(
                "failed to publish usage stats for session %s", handle.session_id
            )


__all__ = [
    "JobManager",
    "JobHandle",
    "JobState",
    "compute_usage",
]
