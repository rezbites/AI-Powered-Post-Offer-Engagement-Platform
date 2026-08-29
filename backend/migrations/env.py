"""Alembic environment.

Reads the database URL from application settings rather than alembic.ini, so
the migration tool and the running app can never disagree about which database
they are pointed at.

Runs migrations through the async engine, since the app is fully async and a
second sync driver would otherwise have to be installed just for Alembic.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy import pool

from app.core.config import get_settings
from app.db.base import Base

# Importing the models module registers every table on Base.metadata, which is
# what --autogenerate diffs against. Without this import the metadata is empty
# and autogenerate would cheerfully propose dropping every table.
from app.db import models  # noqa: F401

config = context.config
settings = get_settings()

config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _configure(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Detect column type changes, not just added/dropped columns.
        compare_type=True,
        compare_server_default=True,
        # SQLite cannot ALTER most columns; batch mode rewrites the table
        # instead. Harmless on Postgres, essential for the fallback path.
        render_as_batch=settings.is_sqlite,
    )


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live connection (`alembic upgrade --sql`).

    Useful when a DBA must review and apply migrations by hand.
    """
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=settings.is_sqlite,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
