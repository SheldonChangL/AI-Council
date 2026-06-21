"""可程式化的 FakeAdapter（Story 3.1 / FR-18, FR-20 / 藍圖 §6 Mocking）。

``FakeAdapter`` 滿足 :class:`~eps.adapters.base.LLMAdapter` Protocol，供核心編排
做決定性測試：每個方法的回傳值與例外皆可預先以建構參數腳本化（AC-3）。

腳本化模型（皆為決定性、無隨機、無真實 I/O）：

- **觀點**：``viewpoints`` 序列，由 ``invoke`` 依序消耗。
- **焦點**：``focuses`` 序列，由 ``refine_focus`` 依序消耗。
- **回合摘要 / 最終報告**：``round_summaries`` 序列與 ``final_report``。
- **錯誤**：``errors`` 為「方法名 → 例外」對映；該方法被呼叫時固定拋出。
  ``error_after`` 為「方法名 → 允許成功次數」對映：該方法前 N 次正常返回、第 N+1
  次起才拋 ``errors[method]``，用以模擬「跑出部分結果後才失敗」（Story 4.5）。
  未指定時預設 0（即首次呼叫就拋，沿用既有行為）。
- **逾時**：``timeouts`` 為方法名集合；該方法被呼叫時拋出
  :class:`~eps.adapters.base.AdapterTimeout`。
- **SourceError**：``source_error`` 設定時，``validate_source`` 拋出之
  （預設拋出 :class:`~eps.adapters.base.SourceError`）。

序列耗盡後以可預測的衍生字串回退（例如 ``"viewpoint:<persona>@<focus>"``），
讓未完整腳本化的測試仍具決定性。所有呼叫記錄於 :attr:`calls` 供斷言。
"""

from __future__ import annotations

from collections import deque
from typing import Deque, Iterable, List, Mapping, Optional, Sequence, Tuple

from eps.adapters.base import AdapterTimeout


class FakeAdapter:
    """決定性、可程式化的 :class:`~eps.adapters.base.LLMAdapter` 實作。"""

    def __init__(
        self,
        *,
        viewpoints: Sequence[str] = (),
        focuses: Sequence[str] = (),
        round_summaries: Sequence[str] = (),
        final_report: str = "FINAL_REPORT",
        source_error: Optional[BaseException] = None,
        errors: Optional[Mapping[str, BaseException]] = None,
        error_after: Optional[Mapping[str, int]] = None,
        timeouts: Iterable[str] = (),
    ) -> None:
        self._viewpoints: Deque[str] = deque(viewpoints)
        self._focuses: Deque[str] = deque(focuses)
        self._round_summaries: Deque[str] = deque(round_summaries)
        self._final_report = final_report
        self._source_error = source_error
        self._errors: dict[str, BaseException] = dict(errors or {})
        self._error_after: dict[str, int] = dict(error_after or {})
        self._timeouts = set(timeouts)
        # 記錄每次呼叫 (method, args)，供測試斷言呼叫次序與引數。
        self.calls: List[Tuple[str, tuple]] = []

    def _guard(self, method: str, *args: object) -> None:
        """記錄呼叫，並處理腳本化的逾時 / 錯誤（逾時優先）。"""
        self.calls.append((method, args))
        if method in self._timeouts:
            raise AdapterTimeout(f"FakeAdapter 腳本化逾時：{method}")
        if method in self._errors:
            # error_after：前 N 次成功，第 N+1 次起才拋（含本次的呼叫計數）。
            calls_so_far = sum(1 for m, _ in self.calls if m == method)
            if calls_so_far > self._error_after.get(method, 0):
                raise self._errors[method]

    async def validate_source(self, source_url: str) -> None:
        self._guard("validate_source", source_url)
        if self._source_error is not None:
            raise self._source_error
        # 未腳本化錯誤即視為來源有效。
        return None

    async def invoke(self, persona: str, focus: str) -> str:
        self._guard("invoke", persona, focus)
        if self._viewpoints:
            return self._viewpoints.popleft()
        return f"viewpoint:{persona}@{focus}"

    async def refine_focus(self, focus: str, viewpoint: str) -> str:
        self._guard("refine_focus", focus, viewpoint)
        if self._focuses:
            return self._focuses.popleft()
        return f"focus:{focus}+{viewpoint}"

    async def summarize_round(
        self, topic: str, round_number: int, viewpoints: Sequence[str]
    ) -> str:
        self._guard("summarize_round", topic, round_number, tuple(viewpoints))
        if self._round_summaries:
            return self._round_summaries.popleft()
        return f"summary:{topic}#{round_number}:{len(viewpoints)}"

    async def compose_final_report(
        self, topic: str, round_summaries: Sequence[str]
    ) -> str:
        self._guard("compose_final_report", topic, tuple(round_summaries))
        return self._final_report


__all__ = ["FakeAdapter"]
