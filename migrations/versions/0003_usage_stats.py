"""usage_stats — session 新增用量統計欄位（Story 4.6 / OPS-3）。

就地於 ``session`` 新增 nullable ``usage_stats`` 欄位（JSON 文字），承載會話結束後
彙總的用量統計（輪次×專家用量）。沿用 Story 2.4 ``final_report`` 的 nullable 欄位
先例（1:1 於 session），維持五張表、不新增資料表。
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision = "0003_usage_stats"
down_revision = "0002_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite 原生支援 ALTER TABLE ADD COLUMN（nullable 無 server_default）。
    op.add_column(
        "session",
        sa.Column("usage_stats", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )


def downgrade() -> None:
    # SQLite 需以 batch 重建表才能 DROP COLUMN。
    with op.batch_alter_table("session") as batch_op:
        batch_op.drop_column("usage_stats")
