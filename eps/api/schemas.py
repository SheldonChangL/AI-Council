"""eps API 回應模型（Story 2.6 / FR-2, FR-16 / 藍圖 A2/A3/A7/A8）。

所有對外欄位採 camelCase（AC-1 明確要求 `createdAt`）。模型以
``alias_generator=to_camel`` 自動產生別名，並以 ``from_attributes`` 直接由 ORM
模型 / ``SessionDetail`` dataclass 建構；FastAPI 預設以別名序列化回應。
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from eps.data.models import SessionStatus
from eps.data.repository import SessionDetail


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


class SourceStatusOut(_CamelModel):
    """`GET /source/status` 回應（Story 3.5 / FR-4, OPS-1）。

    ``valid`` 由 ``LLMAdapter.validate_source()`` 真實判定：正常返回即 ``True``；
    拋出 ``SourceError``（或其他 ``AdapterError``）即 ``False``，``reason`` 帶
    錯誤訊息（含修復／重新登入提示）。``valid=True`` 時 ``reason`` 為 ``None``。
    """

    valid: bool
    reason: Optional[str] = None


__all__ = [
    "SessionSummary",
    "PersonaOut",
    "SessionFull",
    "ExpertOut",
    "RoundOut",
    "ContributionOut",
    "SessionDetailOut",
    "SourceStatusOut",
]
