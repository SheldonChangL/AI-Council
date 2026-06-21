"""eps 多輪序列編排狀態機（Story 4.4 / FR-5~FR-9 / 藍圖 §3.1, §3.3）。

:class:`OrchestrationEngine` 是核心流程的協調者：依序跑每輪、每位專家，逐位以
``refine_focus`` 彙整焦點並 append-only 落地，回合結束產生輪次總結，達到
``max_rounds`` 後產出最終綜整報告。整個過程透過 :class:`~eps.core.bus.EventBus`
對外發出 :mod:`eps.core.events` 事件，供傳輸層即時推送。

設計（皆以既有契約為依據，非發明）：

- **依賴注入**：``repo`` 負責持久化（里程碑單 transaction）、``adapter`` 為
  :class:`~eps.adapters.base.LLMAdapter`（可注入 ``FakeAdapter`` 決定性測試）、
  ``bus`` 為事件匯流排。引擎不耦合任何具體後端或傳輸實作。
- **焦點演進**：語意收斂與回合摘要委派 :mod:`eps.core.focus`（內含長度上限策略，
  FR-10）；引擎只負責「序列推進」與「狀態轉移」。
- **事件序列**（AC-1）::

      StatusChanged(ValidatingSource) → StatusChanged(Running)
      → (RoundStarted
          → (ExpertStarted → ExpertCompleted → FocusUpdated) × N專家
          → RoundSummary) × max_rounds
      → ReportCompleted

  終態 ``Completed`` 僅落地 DB、不另發 ``StatusChanged``——對外的完成信號即
  ``ReportCompleted``（落地為 ``Session.final_report``）。
- **里程碑落地**（AC-2）：每位專家發言完成立即 ``refine_focus`` 並以
  ``append_contribution`` 在單一 transaction 寫入 ``viewpoint`` 與 ``focus_after``。
"""

from __future__ import annotations

from eps.adapters.base import LLMAdapter, SourceError
from eps.config import DEFAULT_MAX_FOCUS_CHARS
from eps.core import focus as focus_ops
from eps.core.bus import EventBus
from eps.core.domain import ExpertRef, RoundState, SessionRuntime
from eps.core.events import (
    Event,
    ExpertCompleted,
    ExpertStarted,
    FocusUpdated,
    ReportCompleted,
    RoundStarted,
    RoundSummary,
    StatusChanged,
)
from eps.data.models import SessionStatus
from eps.data.repository import Repository


class OrchestrationEngine:
    """多輪序列編排狀態機（FR-5~FR-9）。

    透過注入的 ``repo`` / ``adapter`` / ``bus`` 推進一場會話：驗證來源 → 逐輪逐位
    專家發言並彙整焦點 → 產生輪次總結 → 產出最終報告。每個語意里程碑以單一
    transaction 落地（AC-2），並對外發出對應事件（AC-1）。
    """

    def __init__(
        self,
        repo: Repository,
        adapter: LLMAdapter,
        bus: EventBus,
        *,
        max_focus_chars: int = DEFAULT_MAX_FOCUS_CHARS,
    ) -> None:
        self._repo = repo
        self._adapter = adapter
        self._bus = bus
        self._max_focus_chars = max_focus_chars

    async def run(self, session_id: int) -> SessionRuntime:
        """執行一場會話的完整編排，回傳終態的 :class:`SessionRuntime`。

        來源驗證失敗（:class:`SourceError`）時轉為 ``SourceInvalid`` 並提前結束；
        正常跑完 ``max_rounds`` 後落地最終報告並轉為 ``Completed``（AC-3）。
        """
        runtime = self._load_runtime(session_id)

        # --- 來源驗證階段（StatusChanged(ValidatingSource)）---
        await self._transition(runtime, SessionStatus.ValidatingSource)
        try:
            await self._adapter.validate_source(runtime.source_url or "")
        except SourceError:
            await self._transition(runtime, SessionStatus.SourceInvalid)
            return runtime

        # --- 進入執行（StatusChanged(Running)）---
        await self._transition(runtime, SessionStatus.Running)

        # 第一輪起始焦點為議題本身；其後每輪以上一輪總結為起點（FR-7, FR-10）。
        focus = runtime.topic
        round_summaries: list[str] = []
        for round_number in range(1, runtime.max_rounds + 1):
            focus = await self._run_round(runtime, round_number, focus, round_summaries)

        # --- 收尾：依全程演進脈絡產出最終報告並落地（AC-3）---
        report = await self._adapter.compose_final_report(runtime.topic, round_summaries)
        self._repo.save_final_report(session_id, report)
        runtime.status = SessionStatus.Completed
        await self._publish(ReportCompleted(session_id=session_id, report=report))
        return runtime

    async def _run_round(
        self,
        runtime: SessionRuntime,
        round_number: int,
        focus: str,
        round_summaries: list[str],
    ) -> str:
        """跑完單一回合：逐位專家發言、彙整焦點、產生回合總結。

        回傳本輪總結（作為下一輪的起始焦點）。本輪所有專家的觀點與 ``focus_after``
        皆以 ``append_contribution`` 逐筆單 transaction 落地（AC-2）。
        """
        session_id = runtime.session_id
        rnd = self._repo.create_round(session_id, round_number)
        round_state = RoundState(round_number=round_number, focus=focus)
        runtime.current_round = round_state
        await self._publish(
            RoundStarted(session_id=session_id, round_number=round_number, focus=focus)
        )

        for seq, expert in enumerate(runtime.experts):
            await self._publish(
                ExpertStarted(
                    session_id=session_id,
                    round_number=round_number,
                    expert_id=expert.id,
                    expert_name=expert.name,
                )
            )
            viewpoint = await self._adapter.invoke(expert.persona_prompt, focus)
            await self._publish(
                ExpertCompleted(
                    session_id=session_id,
                    round_number=round_number,
                    expert_id=expert.id,
                    viewpoint=viewpoint,
                )
            )

            # 立即彙整焦點，並把觀點與收斂後焦點以單一 transaction append-only 落地。
            focus = await focus_ops.refine_focus(
                self._adapter, focus, viewpoint, max_chars=self._max_focus_chars
            )
            self._repo.append_contribution(
                round_id=rnd.id,
                expert_id=expert.id,
                seq=seq,
                viewpoint=viewpoint,
                focus_after=focus,
            )
            round_state.viewpoints.append(viewpoint)
            round_state.focus = focus
            await self._publish(
                FocusUpdated(
                    session_id=session_id, round_number=round_number, focus=focus
                )
            )

        summary = await focus_ops.summarize_round(
            self._adapter,
            runtime.topic,
            round_number,
            round_state.viewpoints,
            max_chars=self._max_focus_chars,
        )
        round_state.summary = summary
        round_summaries.append(summary)
        await self._publish(
            RoundSummary(
                session_id=session_id, round_number=round_number, summary=summary
            )
        )
        return summary

    def _load_runtime(self, session_id: int) -> SessionRuntime:
        """由持久層載入會話聚合，建立執行期 :class:`SessionRuntime`。"""
        detail = self._repo.get_session_detail(session_id)
        if detail is None:
            raise ValueError(f"session {session_id} not found")
        experts = [
            ExpertRef(
                id=e.id,
                name=e.name,
                order_index=e.order_index,
                persona_prompt=e.persona_prompt,
            )
            for e in detail.experts
        ]
        session = detail.session
        return SessionRuntime(
            session_id=session_id,
            topic=session.topic,
            max_rounds=session.max_rounds,
            experts=experts,
            status=session.status,
            source_url=session.source_url,
        )

    async def _transition(
        self, runtime: SessionRuntime, status: SessionStatus
    ) -> None:
        """落地狀態轉移並對外發出 ``StatusChanged`` 事件。"""
        runtime.status = status
        self._repo.set_status(runtime.session_id, status)
        await self._publish(
            StatusChanged(session_id=runtime.session_id, status=status.value)
        )

    async def _publish(self, event: Event) -> None:
        await self._bus.publish(event)


__all__ = ["OrchestrationEngine"]
