"""Alembic 執行環境（Story 1.4）。

- 連線字串統一由 `eps.config.get_settings().db_url` 提供（即 `EPS_DB_URL`），
  避免 alembic.ini 與應用程式設定分歧。
- target_metadata 綁定 `SQLModel.metadata`，未來新增 model 後即可 autogenerate。
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlmodel import SQLModel

from eps.config import get_settings

# 匯入 eps.data 以確保所有 model 註冊到 SQLModel.metadata（目前尚無 model）。
import eps.data  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 以應用程式設定覆寫連線字串，確保單一來源。
config.set_main_option("sqlalchemy.url", get_settings().db_url)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    """離線模式：僅輸出 SQL，不建立連線。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """線上模式：建立 engine 並套用 migration。"""
    from sqlalchemy import engine_from_config, pool

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
