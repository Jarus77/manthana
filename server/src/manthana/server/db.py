"""Server database engine + schema initialization.

SQLite for dev/tests, Postgres for production (same SQLModel models). pgvector is
reserved for the cross-engineer skill miner (a later scope), not needed by the
founder query, which is plain SQL over released compactions.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from .tables import SERVER_TABLES

_MEMORY_URLS = {"sqlite://", "sqlite:///:memory:"}


def _sqlite_fk(dbapi_conn: Any, _record: Any) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


def create_db_engine(db_url: str) -> Engine:
    if db_url in _MEMORY_URLS:
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
    elif db_url.startswith("sqlite"):
        engine = create_engine(db_url, connect_args={"check_same_thread": False})
    else:
        engine = create_engine(db_url)
    if db_url.startswith("sqlite"):
        event.listen(engine, "connect", _sqlite_fk)
    return engine


def init_db(engine: Engine) -> None:
    """Create the server tables (idempotent)."""
    SQLModel.metadata.create_all(
        engine,
        tables=[table.__table__ for table in SERVER_TABLES],  # type: ignore[attr-defined]
    )


__all__ = ["create_db_engine", "init_db"]
