"""eps WebSocket 事件型別（Story 4.1 / FR-11 / 藍圖 §3.3）。

引擎在編排過程中向傳輸層發出的事件契約。所有事件序列化為統一信封（AC-3）：

    {"type": <事件名>, "sessionId": <int>, "ts": <ISO-8601>, "data": {...}}

設計（與 ``eps.api.schemas`` 一致）：

- 信封與 ``data`` 內欄位皆採 camelCase（``sessionId``、``roundNumber`` …），由
  :func:`pydantic.alias_generators.to_camel` 統一轉換，避免手寫鍵名漂移。
- 各事件為 ``kw_only`` dataclass：共用 ``session_id`` / ``ts``（預設為當下 UTC），
  其餘為該事件的 payload 欄位。``data`` 由 dataclass 欄位（扣除信封欄位）自動推導，
  新增 payload 欄位即自動進入 ``data``，無需改動序列化邏輯。

payload 欄位推導（皆以既有資料模型 / ``LLMAdapter`` 契約為依據，非發明）：

- ``RoundStarted`` / ``FocusUpdated`` → ``Round.round_number`` + 當前 focus。
- ``ExpertStarted`` / ``ExpertCompleted`` / ``TokenChunk`` → ``SessionExpert`` 的
  id/name + ``Contribution.viewpoint``（``TokenChunk`` 為串流中的片段文字）。
- ``RoundSummary`` / ``ReportCompleted`` → :meth:`LLMAdapter.summarize_round` 與
  :meth:`compose_final_report` 的輸出（後者落地為 ``Session.final_report``）。
- ``StatusChanged`` → :class:`SessionStatus` 狀態轉移；``SessionFailed`` →
  終態失敗原因（對應 ``SessionStatus.Failed``，藍圖 §4）。
- ``UsageStats`` → 該會話累積的用量計數（具體計數鍵由引擎填入，此處不預設鍵名）。
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar, Dict, List, Mapping, Type

from pydantic.alias_generators import to_camel

# 信封層級的固定欄位，不納入 ``data`` payload。
_ENVELOPE_FIELDS = frozenset({"session_id", "ts"})


def _utcnow() -> datetime:
    """時區感知的 UTC 當下時間，作為事件時間戳預設值。"""
    return datetime.now(timezone.utc)


def _to_jsonable(value: Any) -> Any:
    """將 payload 值轉為可 JSON 序列化型別（Enum→value、datetime→ISO，遞迴容器）。"""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    return value


@dataclass(kw_only=True)
class Event:
    """所有 WebSocket 事件的基底。

    子類別以 class 變數 ``type`` 宣告事件名，並新增該事件的 payload 欄位。
    :meth:`to_dict` 產出統一信封 ``{type, sessionId, ts, data}``（AC-3）。
    """

    type: ClassVar[str]

    session_id: int
    ts: datetime = field(default_factory=_utcnow)

    def data(self) -> Dict[str, Any]:
        """以 camelCase 鍵回傳此事件的 payload（信封欄位除外）。"""
        return {
            to_camel(f.name): _to_jsonable(getattr(self, f.name))
            for f in fields(self)
            if f.name not in _ENVELOPE_FIELDS
        }

    def to_dict(self) -> Dict[str, Any]:
        """序列化為藍圖 §3.3 的 WS 信封（AC-3）。"""
        return {
            "type": self.type,
            "sessionId": self.session_id,
            "ts": self.ts.isoformat(),
            "data": self.data(),
        }


@dataclass(kw_only=True)
class RoundStarted(Event):
    """新回合開始（``Round.round_number`` + 起始 focus）。"""

    type: ClassVar[str] = "RoundStarted"

    round_number: int
    focus: str = ""


@dataclass(kw_only=True)
class ExpertStarted(Event):
    """某專家在此回合開始發言（``SessionExpert`` id/name）。"""

    type: ClassVar[str] = "ExpertStarted"

    round_number: int
    expert_id: int
    expert_name: str = ""


@dataclass(kw_only=True)
class TokenChunk(Event):
    """專家發言的串流片段（``LLMAdapter.invoke`` 串流輸出的一段文字）。"""

    type: ClassVar[str] = "TokenChunk"

    round_number: int
    expert_id: int
    text: str


@dataclass(kw_only=True)
class ExpertCompleted(Event):
    """某專家完成發言（落地為 ``Contribution.viewpoint``）。"""

    type: ClassVar[str] = "ExpertCompleted"

    round_number: int
    expert_id: int
    viewpoint: str


@dataclass(kw_only=True)
class FocusUpdated(Event):
    """焦點收斂後更新（``LLMAdapter.refine_focus`` → ``Contribution.focus_after``）。"""

    type: ClassVar[str] = "FocusUpdated"

    round_number: int
    focus: str


@dataclass(kw_only=True)
class RoundSummary(Event):
    """回合摘要（``LLMAdapter.summarize_round`` 輸出）。"""

    type: ClassVar[str] = "RoundSummary"

    round_number: int
    summary: str


@dataclass(kw_only=True)
class ReportCompleted(Event):
    """最終綜整報告完成（``compose_final_report`` → ``Session.final_report``）。"""

    type: ClassVar[str] = "ReportCompleted"

    report: str


@dataclass(kw_only=True)
class SessionFailed(Event):
    """會話失敗終態（取消／來源失效／重試耗盡，Story 4.5 / 藍圖 §4）。

    ``reason`` 為對外的失敗原因（來源失效時含「重新登入後重試」提示，AC-2）；
    ``partial_available`` 標示是否已保存可重試的部分結果（已落地的 Contribution／
    回合總結），序列化為 ``partialAvailable``。
    """

    type: ClassVar[str] = "SessionFailed"

    reason: str
    partial_available: bool = False


@dataclass(kw_only=True)
class StatusChanged(Event):
    """會話狀態轉移（重用持久層 :class:`SessionStatus` 狀態機）。"""

    type: ClassVar[str] = "StatusChanged"

    status: str


@dataclass(kw_only=True)
class UsageStats(Event):
    """會話累積用量計數（具體計數鍵由引擎填入，此處不預設鍵名）。"""

    type: ClassVar[str] = "UsageStats"

    stats: Mapping[str, int] = field(default_factory=dict)


# 藍圖 §3.3 涵蓋的全部事件類別（供傳輸層與測試做覆蓋檢查）。
EVENT_CLASSES: List[Type[Event]] = [
    RoundStarted,
    ExpertStarted,
    TokenChunk,
    ExpertCompleted,
    FocusUpdated,
    RoundSummary,
    ReportCompleted,
    SessionFailed,
    StatusChanged,
    UsageStats,
]

# 事件名 → 類別的穩定登錄表（type 字串為對外契約）。
EVENT_REGISTRY: Dict[str, Type[Event]] = {cls.type: cls for cls in EVENT_CLASSES}

# 所有對外事件 type 字串。
EVENT_TYPES: frozenset = frozenset(EVENT_REGISTRY)


__all__ = [
    "Event",
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
    "EVENT_CLASSES",
    "EVENT_REGISTRY",
    "EVENT_TYPES",
]
