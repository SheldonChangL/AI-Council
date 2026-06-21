"""eps.data subpackage。

匯入 ORM 模型，確保 ``import eps.data`` 即可將所有資料表註冊到
``SQLModel.metadata``（供 Alembic autogenerate 與 metadata.create_all 使用）。
"""

from eps.data.models import (
    Contribution,
    MAX_ROUNDS_MAX,
    MAX_ROUNDS_MIN,
    PersonaTemplate,
    Round,
    Session,
    SessionExpert,
    SessionStatus,
    TOPIC_MAX_LENGTH,
)
from eps.data.repository import ExpertSpec, Repository, SessionDetail
from eps.data.seed import BUILTIN_PERSONA_TEMPLATES, seed_persona_templates

__all__ = [
    "Contribution",
    "PersonaTemplate",
    "ExpertSpec",
    "Repository",
    "SessionDetail",
    "Round",
    "Session",
    "SessionExpert",
    "SessionStatus",
    "BUILTIN_PERSONA_TEMPLATES",
    "seed_persona_templates",
    "MAX_ROUNDS_MAX",
    "MAX_ROUNDS_MIN",
    "TOPIC_MAX_LENGTH",
]
