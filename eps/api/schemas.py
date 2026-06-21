"""eps API 請求/回應模型（Story 2.6, 5.1 / FR-1, FR-2, FR-16 / 藍圖 A1/A2/A3/A7/A8）。

所有對外欄位採 camelCase（AC-1 明確要求 `createdAt`、`maxRounds`）。模型以
``alias_generator=to_camel`` 自動產生別名，並以 ``from_attributes`` 直接由 ORM
模型 / ``SessionDetail`` dataclass 建構；FastAPI 預設以別名序列化回應。請求 DTO
同樣以別名接受 camelCase 輸入（``populate_by_name=True`` 亦容許 snake_case）。
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from eps.data.models import (
    MAX_ROUNDS_MAX,
    MAX_ROUNDS_MIN,
    TOPIC_MAX_LENGTH,
    SessionStatus,
)
from eps.data.repository import SessionDetail

# Story 5.1 / AC-2 / 藍圖 §3.3：CreateSessionRequest.experts 的數量界線。
# 下界由 AC-2 明確要求（空清單須驗證失敗）。上界為 API 契約 guardrail：藍圖
# §3.3 未明定確切數值，引擎以序列方式逐一推進專家、無硬性上限，故此處取保守
# 預設值供契約驗證；若藍圖後續明定，調整此常數即可。
EXPERTS_MIN = 1
EXPERTS_MAX = 8


class _CamelModel(BaseModel):
    """共用基底：camelCase 別名 + 可由 ORM 屬性建構。"""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class SessionSummary(_CamelModel):
    """`GET /sessions` 清單項目（AC-1）：`{id, topic, status, createdAt}`。"""

    id: int
    topic: str
    status: SessionStatus
    created_at: datetime


class PersonaOut(_CamelModel):
    """`GET /personas` 模板項目（AC-2）。"""

    id: int
    name: str
    description: str
    system_prompt: str
    builtin: bool


class SessionFull(_CamelModel):
    """`GET /sessions/{id}` 的會話本體（AC-3）。"""

    id: int
    topic: str
    status: SessionStatus
    max_rounds: int
    source_url: Optional[str] = None
    final_report: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ExpertOut(_CamelModel):
    """會話聚合中的參與專家。"""

    id: int
    name: str
    persona_template_id: Optional[int] = None
    persona_prompt: str
    order_index: int


class RoundOut(_CamelModel):
    """會話聚合中的回合。"""

    id: int
    round_number: int


class ContributionOut(_CamelModel):
    """會話聚合中的單次發言里程碑。"""

    id: int
    round_id: int
    session_expert_id: int
    seq: int
    viewpoint: str
    focus_after: Optional[str] = None


class SessionDetailOut(_CamelModel):
    """`GET /sessions/{id}` 完整聚合（AC-3）。"""

    session: SessionFull
    experts: List[ExpertOut]
    rounds: List[RoundOut]
    contributions: List[ContributionOut]
    final_report: Optional[str] = None

    @classmethod
    def from_detail(cls, detail: SessionDetail) -> "SessionDetailOut":
        """由 repository 的 ``SessionDetail`` dataclass 建構回應模型。"""
        return cls(
            session=SessionFull.model_validate(detail.session),
            experts=[ExpertOut.model_validate(e) for e in detail.experts],
            rounds=[RoundOut.model_validate(r) for r in detail.rounds],
            contributions=[
                ContributionOut.model_validate(c) for c in detail.contributions
            ],
            final_report=detail.final_report,
        )


class ExpertIn(_CamelModel):
    """`POST /sessions` 請求中的單一專家規格（Story 5.1 / AC-1）。

    對應 repository 的 ``ExpertSpec``：``personaPrompt`` 為選用覆寫（預設空字串），
    ``sourceTemplateId`` 為選用來源模板 id。``name`` 必填且不可為空白。
    """

    name: str = Field(min_length=1)
    persona_prompt: str = ""
    source_template_id: Optional[int] = None

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        """拒絕全為空白的 name（與 ``Session.topic`` 同一語意）。"""
        if not value.strip():
            raise ValueError("name 不可為空")
        return value


class CreateSessionRequest(_CamelModel):
    """`POST /sessions` 請求本體（Story 5.1 / AC-1, AC-2 / FR-1）。

    欄位約束與持久層 ``Session`` 模型一致：``topic`` 非空且 ≤ ``TOPIC_MAX_LENGTH``、
    ``maxRounds`` 介於 ``MAX_ROUNDS_MIN..MAX_ROUNDS_MAX``。``experts`` 數量須介於
    ``EXPERTS_MIN..EXPERTS_MAX``（AC-2：空清單或超過上限即驗證失敗）。
    """

    topic: str = Field(min_length=1, max_length=TOPIC_MAX_LENGTH)
    max_rounds: int = Field(ge=MAX_ROUNDS_MIN, le=MAX_ROUNDS_MAX)
    experts: List[ExpertIn] = Field(min_length=EXPERTS_MIN, max_length=EXPERTS_MAX)

    @field_validator("topic")
    @classmethod
    def _topic_not_blank(cls, value: str) -> str:
        """拒絕全為空白的 topic（與 ``Session`` 模型一致）。"""
        if not value.strip():
            raise ValueError("topic 不可為空")
        return value


class CreateSessionResponse(_CamelModel):
    """`POST /sessions` 回應（Story 5.1 / AC-1）。

    回傳剛建立的會話本體與其參與專家；新建會話尚無回合/發言，故不含 ``rounds`` /
    ``contributions``（形狀為 ``SessionDetailOut`` 在建立時點的子集，藍圖 A1/A3）。
    """

    session: SessionFull
    experts: List[ExpertOut]


class SourceStatusOut(_CamelModel):
    """`GET /source/status` 回應（Story 3.5 / FR-4, OPS-1）。

    ``valid`` 由 ``LLMAdapter.validate_source()`` 真實判定：正常返回即 ``True``；
    拋出 ``SourceError``（或其他 ``AdapterError``）即 ``False``，``reason`` 帶
    錯誤訊息（含修復／重新登入提示）。``valid=True`` 時 ``reason`` 為 ``None``。
    """

    valid: bool
    reason: Optional[str] = None


__all__ = [
    "EXPERTS_MIN",
    "EXPERTS_MAX",
    "SessionSummary",
    "PersonaOut",
    "SessionFull",
    "ExpertOut",
    "RoundOut",
    "ContributionOut",
    "SessionDetailOut",
    "ExpertIn",
    "CreateSessionRequest",
    "CreateSessionResponse",
    "SourceStatusOut",
]
