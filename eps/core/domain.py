"""eps 執行期領域物件（Story 4.1 / FR-11 / 藍圖 §3.3）。

引擎在記憶體中編排一場會話時所需的「執行期」聚合，與 ``eps.data.models`` 的
持久化 ORM 模型分離：ORM 模型負責落地，這裡的物件負責承載「正在跑」的狀態，
供引擎推進回合、傳輸層產生事件。

契約推導（皆以既有資料模型為依據，非發明）：

- ``ExpertRef``：對應 ``SessionExpert`` 的執行期輕量參照（id / name / order_index /
  persona_prompt）。引擎呼叫 :meth:`LLMAdapter.invoke` 時以 ``persona_prompt`` 為
  ``persona`` 引數。
- ``RoundState``：對應 ``Round`` 加上回合內累積的執行期資料（當前 focus、已收集的
  viewpoints、回合摘要），對齊 ``Contribution.viewpoint`` / ``focus_after`` 與
  :meth:`LLMAdapter.summarize_round` 的輸出。
- ``SessionRuntime``：對應 ``Session`` 的執行期聚合（topic / max_rounds / status /
  參與專家 / 當前回合），``status`` 直接重用持久層的 :class:`SessionStatus` 狀態機。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from eps.data.models import SessionStatus


@dataclass(frozen=True)
class ExpertRef:
    """執行期專家參照（對應 ``SessionExpert``）。

    不可變：一場會話的參與專家在開跑後固定。``persona_prompt`` 供引擎作為
    :meth:`LLMAdapter.invoke` 的 ``persona`` 引數；``order_index`` 決定回合內發言順序。
    """

    id: int
    name: str
    order_index: int
    persona_prompt: str = ""


@dataclass
class RoundState:
    """單一回合的執行期狀態（對應 ``Round`` 與其 ``Contribution`` 累積）。

    ``focus`` 為此回合當前焦點（由 :meth:`LLMAdapter.refine_focus` 收斂、落地為
    ``Contribution.focus_after``）；``viewpoints`` 依發言順序累積各專家觀點
    （落地為 ``Contribution.viewpoint``）；``summary`` 為回合結束後的摘要
    （:meth:`LLMAdapter.summarize_round` 輸出，回合進行中為 ``None``）。
    """

    round_number: int
    focus: str = ""
    viewpoints: List[str] = field(default_factory=list)
    summary: Optional[str] = None


@dataclass
class SessionRuntime:
    """一場執行中的會話聚合（對應 ``Session``）。

    承載引擎推進所需的最小可變狀態：``status`` 重用持久層狀態機、``experts`` 為
    開跑時固定的參與專家、``current_round`` 為正在進行的回合（尚未開始為 ``None``）。
    """

    session_id: int
    topic: str
    max_rounds: int
    experts: List[ExpertRef] = field(default_factory=list)
    status: SessionStatus = SessionStatus.Created
    current_round: Optional[RoundState] = None


__all__ = [
    "ExpertRef",
    "RoundState",
    "SessionRuntime",
]
