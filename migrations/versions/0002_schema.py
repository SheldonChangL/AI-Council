"""schema — 建立五張表與索引/約束（Story 2.2 / FR-15, FR-16 / 藍圖 §3.2）。

於 baseline 之後建立全部資料表，並建立 AC-2 要求的唯一約束與索引：
- 唯一約束 ``Round(session_id, round_number)``、``Contribution(round_id, seq)``。
- 索引 ``Session(created_at DESC)``、``Session(status)``、``Contribution(round_id, seq)``。

``ix_session_created_at`` 為表達式（DESC）索引，autogenerate 在 SQLite 無法反射，
故於此手動建立。
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision = "0002_schema"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "persona_template",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("system_prompt", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_persona_template_name", "persona_template", ["name"], unique=False)

    op.create_table(
        "session",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("topic", sqlmodel.sql.sqltypes.AutoString(length=8000), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "Created",
                "ValidatingSource",
                "Running",
                "Completed",
                "Failed",
                "SourceInvalid",
                "Cancelled",
                name="sessionstatus",
            ),
            nullable=False,
        ),
        sa.Column("max_rounds", sa.Integer(), nullable=False),
        sa.Column("source_url", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # AC-2：最近建立優先的列表查詢索引（表達式 DESC）與狀態篩選索引。
    op.create_index(
        "ix_session_created_at", "session", [sa.text("created_at DESC")], unique=False
    )
    op.create_index("ix_session_status", "session", ["status"], unique=False)

    op.create_table(
        "round",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["session.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id", "round_number", name="uq_round_session_round_number"
        ),
    )
    op.create_index("ix_round_session_id", "round", ["session_id"], unique=False)

    op.create_table(
        "session_expert",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("persona_template_id", sa.Integer(), nullable=True),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["persona_template_id"], ["persona_template.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["session.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_session_expert_session_id", "session_expert", ["session_id"], unique=False
    )

    op.create_table(
        "contribution",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("round_id", sa.Integer(), nullable=False),
        sa.Column("session_expert_id", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("content", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["round_id"], ["round.id"]),
        sa.ForeignKeyConstraint(["session_expert_id"], ["session_expert.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("round_id", "seq", name="uq_contribution_round_seq"),
    )
    op.create_index("ix_contribution_round_id", "contribution", ["round_id"], unique=False)
    op.create_index(
        "ix_contribution_round_seq", "contribution", ["round_id", "seq"], unique=False
    )
    op.create_index(
        "ix_contribution_session_expert_id",
        "contribution",
        ["session_expert_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_contribution_session_expert_id", table_name="contribution")
    op.drop_index("ix_contribution_round_seq", table_name="contribution")
    op.drop_index("ix_contribution_round_id", table_name="contribution")
    op.drop_table("contribution")

    op.drop_index("ix_session_expert_session_id", table_name="session_expert")
    op.drop_table("session_expert")

    op.drop_index("ix_round_session_id", table_name="round")
    op.drop_table("round")

    op.drop_index("ix_session_status", table_name="session")
    op.drop_index("ix_session_created_at", table_name="session")
    op.drop_table("session")

    op.drop_index("ix_persona_template_name", table_name="persona_template")
    op.drop_table("persona_template")
