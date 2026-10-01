"""Alembic environment: the schema is `fraudlens.platform.models`, the URL comes from settings."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from fraudlens.config import settings
from fraudlens.platform.models import Base

config = context.config
if config.config_file_name is not None and not config.attributes.get("url"):
    fileConfig(config.config_file_name)  # command line only: keep the caller's logging otherwise

target_metadata = Base.metadata
# `fraudlens.platform.db.migrate(url)` passes the URL explicitly (tests, other databases).
url = config.attributes.get("url") or settings.database_url


def run_migrations_offline() -> None:
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
