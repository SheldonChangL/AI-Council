"""baseline — 建立 migration 鏈起點（Story 1.4）。

目前尚無資料 model（`SQLModel.metadata` 為空），此 baseline 不建立任何表，
僅作為 migration 鏈的起點，讓乾淨環境 `alembic upgrade head` 可成功並建立
`alembic_version` 追蹤表。後續 story 的 schema 以此為 down_revision 延伸。
"""
from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
