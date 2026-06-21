"""eps SQLModel ORM 資料模型（Story 2.1 / FR-15 / 藍圖 §3.2）。

定義會話狀態持久化所需的五個實體：

- ``PersonaTemplate``：可重用的專家人設範本。
- ``Session``：一場 AI-Council 會話（含狀態機與回合上限）。
- ``SessionExpert``：會話中實際參與的專家（由人設範本實例化）。
- ``Round``：會話中的一個回合。
- ``Contribution``：某位專家在某回合的單次發言。

驗證約束（AC-2 / AC-3）以 Pydantic 欄位約束 + validator 強制：
- ``Session.max_rounds`` 合法範圍 ``1 ≤ n ≤ 20``。
- ``Session.topic`` 非空，且長度上限 8k 字。

注意：SQLModel ``table=True`` 模型預設會跳過建構時的 Pydantic 驗證，因此
``Session`` 另外開啟 ``validate_assignment`` 並覆寫 ``__init__``，確保「建構」與
「設定欄位」兩種路徑都會觸發驗證。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import ConfigDict, field_validator
from sqlalchemy import Index, UniqueConstraint, text
from sqlmodel import Field, SQLModel

# 藍圖 §3.2：Session.topic 長度上限與 max_rounds 合法範圍。
TOPIC_MAX_LENGTH = 8000
MAX_ROUNDS_MIN = 1
MAX_ROUNDS_MAX = 20


def _utcnow() -> datetime:
    """時區感知的 UTC 當下時間，作為時間戳預設值。"""
    return datetime.now(timezone.utc)


class SessionStatus(str, Enum):
    """會話狀態機（藍圖 §3.2）。

    值與字串相同，便於持久化為文字欄位與跨層傳遞。
    """

    Created = "Created"
    ValidatingSource = "ValidatingSource"
    Running = "Running"
    Completed = "Completed"
    Failed = "Failed"
    SourceInvalid = "SourceInvalid"
    Cancelled = "Cancelled"


class PersonaTemplate(SQLModel, table=True):
    """可重用的專家人設範本。

    Story 2.5 / AC-1, AC-3：``builtin`` 標示系統內建模板。內建模板為唯讀
    （不提供修改 API），由 ``eps.data.seed.seed_persona_templates`` 冪等寫入。
    """

    __tablename__ = "persona_template"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    description: str = ""
    system_prompt: str = ""
    # Story 2.5 / AC-1：True 表系統內建模板（唯讀），預設為使用者自建（False）。
    builtin: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=_utcnow)


def _validate_topic(value: str) -> str:
    """AC-3：拒絕空字串或全為空白的 topic。"""
    if value is None or not value.strip():
        raise ValueError("topic 不可為空")
    return value


class _SessionConstraints(SQLModel):
    """非 table 的驗證孿生模型。

    SQLModel ``table=True`` 模型在 ``__init__`` 直接設值、跳過 Pydantic 驗證，
    且 ``model_validate`` 內部會再呼叫 ``cls()`` 導致遞迴，因此無法直接在 table
    模型的 ``__init__`` 觸發驗證。此非 table 孿生模型承載受驗證欄位，建構即會
    執行欄位約束與 validator，供 ``Session.__init__`` 委派使用，產生標準的
    ``ValidationError``。
    """

    topic: str = Field(max_length=TOPIC_MAX_LENGTH)
    max_rounds: int = Field(ge=MAX_ROUNDS_MIN, le=MAX_ROUNDS_MAX)

    _check_topic = field_validator("topic")(_validate_topic)


class Session(SQLModel, table=True):
    """一場 AI-Council 會話。

    驗證策略（AC-2 / AC-3）：
    - ``validate_assignment`` 讓「設定欄位」即觸發驗證。
    - 覆寫的 ``__init__`` 委派 ``_SessionConstraints`` 驗證輸入，讓「建構」與
      ``model_validate`` 也會拒絕非法 ``topic`` / ``max_rounds``。
    """

    model_config = ConfigDict(validate_assignment=True)  # type: ignore[assignment]

    __tablename__ = "session"
    # AC-2：列表查詢用索引（最近建立優先）與狀態篩選索引。
    __table_args__ = (
        Index("ix_session_created_at", text("created_at DESC")),
        Index("ix_session_status", "status"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    topic: str = Field(max_length=TOPIC_MAX_LENGTH)
    status: SessionStatus = Field(default=SessionStatus.Created)
    max_rounds: int = Field(ge=MAX_ROUNDS_MIN, le=MAX_ROUNDS_MAX)
    source_url: Optional[str] = Field(default=None)
    # Story 5.2 / AC-4：選用的冪等鍵（Idempotency-Key）。帶相同鍵的重複 `POST /sessions`
    # 須回傳同一會話，故以 unique 索引保證每個鍵至多對應一場會話（併發 backstop）。
    # nullable：未帶鍵建立的會話為 NULL，SQLite 視多個 NULL 為相異，互不衝突。
    idempotency_key: Optional[str] = Field(default=None, index=True, unique=True)
    # Story 2.4 / AC-2：會話完成後產出的最終綜整報告（每場會話 1:1，未完成為 None）。
    final_report: Optional[str] = Field(default=None)
    # Story 4.6 / OPS-3：會話結束後彙總的用量統計（輪次×專家用量），以 JSON 文字
    # 持久化（每場會話 1:1，未統計為 None）。沿用 final_report 的 nullable 欄位先例，
    # 不新增資料表。
    usage_stats: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    _check_topic = field_validator("topic")(_validate_topic)

    def __init__(self, **data: Any) -> None:
        # 委派非 table 孿生模型驗證受約束欄位，讓「建構」也會拒絕非法輸入並
        # 拋出標準 ValidationError（ORM 載入走 __new__、不經此路徑）。
        _SessionConstraints(
            topic=data.get("topic"),
            max_rounds=data.get("max_rounds"),
        )
        super().__init__(**data)


class SessionExpert(SQLModel, table=True):
    """會話中實際參與的專家（由 ``PersonaTemplate`` 實例化）。

    Story 2.5 / AC-2：``persona_template_id`` 為來源模板（sourceTemplateId），
    ``persona_prompt`` 承載「實例化後（可覆寫）的人設 prompt」。覆寫值寫入此處，
    對應的 ``PersonaTemplate`` 列保持不變（覆寫隔離）。
    """

    __tablename__ = "session_expert"

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="session.id", index=True)
    persona_template_id: Optional[int] = Field(
        default=None, foreign_key="persona_template.id"
    )
    name: str
    # Story 2.5 / AC-2：實例化後的人設 prompt；覆寫值寫於此，不回寫模板。
    persona_prompt: str = ""
    # AC-1：會話內專家以連續 order_index（0..n-1）排序寫入。
    order_index: int = 0
    created_at: datetime = Field(default_factory=_utcnow)


class Round(SQLModel, table=True):
    """會話中的一個回合。"""

    __tablename__ = "round"
    # AC-2/AC-3：同一會話的回合序號唯一。
    __table_args__ = (
        UniqueConstraint(
            "session_id", "round_number", name="uq_round_session_round_number"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="session.id", index=True)
    round_number: int = Field(default=0)
    created_at: datetime = Field(default_factory=_utcnow)


class Contribution(SQLModel, table=True):
    """某位專家在某回合的單次發言。"""

    __tablename__ = "contribution"
    # AC-2：同一回合內發言序號唯一，並以 (round_id, seq) 索引支撐有序讀取。
    __table_args__ = (
        UniqueConstraint("round_id", "seq", name="uq_contribution_round_seq"),
        Index("ix_contribution_round_seq", "round_id", "seq"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    round_id: int = Field(foreign_key="round.id", index=True)
    session_expert_id: int = Field(foreign_key="session_expert.id", index=True)
    seq: int = Field(default=0)
    # AC-2：append_contribution(... viewpoint, focus_after) 落地的語意里程碑欄位。
    viewpoint: str = ""
    focus_after: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)


__all__ = [
    "SessionStatus",
    "PersonaTemplate",
    "Session",
    "SessionExpert",
    "Round",
    "Contribution",
    "TOPIC_MAX_LENGTH",
    "MAX_ROUNDS_MIN",
    "MAX_ROUNDS_MAX",
]
