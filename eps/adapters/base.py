"""eps LLMAdapter Protocol 與例外（Story 3.1 / FR-18, FR-20 / 藍圖 §6）。

核心編排只依賴此處定義的 ``LLMAdapter`` 結構型別，不耦合任何具體 LLM 後端
（如外部 ``codex`` CLI），使後端可替換、且測試可用 ``FakeAdapter`` 做決定性注入。

契約推導（皆以現有資料模型為依據，非發明）：

- ``validate_source(source_url)``：對應 ``SessionStatus.ValidatingSource`` 階段。
  來源無效時拋出 :class:`SourceError`（落地為 ``SessionStatus.SourceInvalid``）；
  有效則正常返回。參數取自 ``Session.source_url``。
- ``invoke(persona, focus)`` → 專家觀點字串，落地為 ``Contribution.viewpoint``。
  ``persona`` 為 ``SessionExpert.persona_prompt``（或名稱），``focus`` 為當前焦點。
- ``refine_focus(focus, viewpoint)`` → 收斂後的新焦點，落地為
  ``Contribution.focus_after``。
- ``summarize_round(topic, round_number, viewpoints)`` → 該回合摘要字串。
- ``compose_final_report(topic, round_summaries)`` → 最終綜整報告，落地為
  ``Session.final_report``。

設計決策：

- **async**：Adapter 包裝外部 CLI 之 I/O，且編排受 ``max_concurrency`` 並發控制
  （藍圖 §3.1），故所有方法為 coroutine；逾時以 :class:`AdapterTimeout` 表達，
  與具體後端無關（藍圖 §6 Mocking 要求可注入逾時）。
- **@runtime_checkable**：使 ``isinstance(obj, LLMAdapter)`` 可用於 AC-2 的型別
  檢查（注意：runtime_checkable 僅檢查方法是否存在，不檢查簽章）。
"""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable


class AdapterError(Exception):
    """LLMAdapter 相關錯誤的基底類別。"""


class SourceError(AdapterError):
    """來源驗證失敗（``validate_source`` 專用）。

    對應 ``SessionStatus.SourceInvalid``：表示提供的來源無法存取或不合法，
    與一般 LLM 呼叫錯誤（:class:`AdapterError`）區隔，供編排層做不同狀態轉移。
    """


class AdapterTimeout(AdapterError):
    """LLM 呼叫逾時。

    以後端無關的型別表達逾時，供編排層在不認識具體後端的情況下處理重試／失敗。
    """


class TransientError(AdapterError):
    """暫時性、**可重試**的後端錯誤（Story 3.2 / AC-3）。

    例如子行程以非零退出但屬可恢復原因（網路抖動、暫時不可用）。編排層可對
    此類錯誤套用重試策略；與 :class:`AuthError` 等不可重試錯誤明確區隔。
    """


class AuthError(AdapterError):
    """認證／授權失敗（**不可重試**）。

    例如外部 CLI 未登入或憑證失效。重試無助於恢復，編排層應直接失敗並要求
    使用者重新認證，故不歸類為 :class:`TransientError`。
    """


class RetryExhaustedError(AdapterError):
    """重試耗盡後仍失敗的永久性錯誤（Story 3.4 / AC-2）。

    對暫時性失敗（:class:`TransientError`）與 stall 逾時（:class:`AdapterTimeout`）
    套用指數退避重試，次數耗盡後拋出此錯誤；落地為 ``SessionStatus.Failed``
    （藍圖 §4），供編排層終止會話而非 silent 續行（OPS-1）。最後一次的底層失敗
    保留於 ``__cause__`` 以利診斷。
    """


@runtime_checkable
class LLMAdapter(Protocol):
    """核心編排依賴的 LLM 後端結構型別（藍圖 §6）。

    任何提供下列 coroutine 方法的物件即滿足此 Protocol；編排層不得依賴任何
    具體實作細節。
    """

    async def validate_source(self, source_url: str) -> None:
        """驗證來源可用性；無效時拋出 :class:`SourceError`，有效則返回 ``None``。"""
        ...

    async def invoke(self, persona: str, focus: str) -> str:
        """以 ``persona`` 針對 ``focus`` 產生觀點，回傳 viewpoint 字串。"""
        ...

    async def refine_focus(self, focus: str, viewpoint: str) -> str:
        """依新 ``viewpoint`` 收斂當前 ``focus``，回傳新的焦點字串。"""
        ...

    async def summarize_round(
        self, topic: str, round_number: int, viewpoints: Sequence[str]
    ) -> str:
        """彙整某回合的全部 ``viewpoints``，回傳該回合摘要。"""
        ...

    async def compose_final_report(
        self, topic: str, round_summaries: Sequence[str]
    ) -> str:
        """彙整各回合摘要，產生最終綜整報告（落地為 ``Session.final_report``）。"""
        ...


__all__ = [
    "AdapterError",
    "SourceError",
    "AdapterTimeout",
    "TransientError",
    "AuthError",
    "RetryExhaustedError",
    "LLMAdapter",
]
