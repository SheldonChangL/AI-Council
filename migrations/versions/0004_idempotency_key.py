"""idempotency_key — session 新增冪等鍵欄位與唯一索引（Story 5.2 / AC-4）。

就地於 ``session`` 新增 nullable ``idempotency_key`` 欄位，承載 `POST /sessions` 的
``Idempotency-Key``。以 unique 索引保證每個鍵至多對應一場會話（帶相同鍵的重複請求
回傳同一 sessionId）。沿用既有 nullable 欄位先例（final_report / usage_stats），維持
五張表、不新增資料表。
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision = "0004_idempotency_key"
down_revision = "0003_usage_stats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite 原生支援 ALTER TABLE ADD COLUMN（nullable 無 server_default）。
    op.add_column(
        "session",
        sa.Column(
            "idempotency_key", sqlmodel.sql.sqltypes.AutoString(), nullable=True
        ),
    )
    # 唯一索引：每個鍵至多一場會話；NULL 在 SQLite 視為相異，未帶鍵者互不衝突。
    op.create_index(
        "ix_session_idempotency_key",
        "session",
        ["idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    # SQLite 需以 batch 重建表才能 DROP COLUMN / INDEX。
    with op.batch_alter_table("session") as batch_op:
        batch_op.drop_index("ix_session_idempotency_key")
        batch_op.drop_column("idempotency_key")
