"""Database engine, sessions and migrations."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import BACKEND_DIR


def make_engine(url: str) -> Engine:
    # pool_pre_ping: a connection dropped by a database restart is replaced, not handed out.
    return create_engine(url, pool_pre_ping=True, pool_size=20, max_overflow=20)


def make_sessions(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def migrate(url: str, revision: str = "head") -> None:
    """Bring the schema at `url` up to `revision` (what `alembic upgrade` does)."""
    from alembic import command
    from alembic.config import Config

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["url"] = url
    command.upgrade(config, revision)
