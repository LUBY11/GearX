from __future__ import annotations

from collections.abc import Generator
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

_engine: Optional[Engine] = None
_session_factory: Optional[sessionmaker] = None


def init_engine(database_url: str) -> Engine:
    global _engine, _session_factory

    if _engine is not None:
        return _engine

    connect_args = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    _engine = create_engine(database_url, future=True, connect_args=connect_args)
    _session_factory = sessionmaker(bind=_engine, class_=Session, expire_on_commit=False)
    return _engine


def init_database(database_url: str) -> None:
    engine = init_engine(database_url)
    Base.metadata.create_all(engine)
    _run_migrations(engine)


def get_session() -> Generator[Session, None, None]:
    if _session_factory is None:
        raise RuntimeError("Database engine is not initialized. Call init_database() first.")
    session: Session = _session_factory()
    try:
        yield session
    finally:
        session.close()


def get_session_factory() -> sessionmaker:
    if _session_factory is None:
        raise RuntimeError("Database engine is not initialized. Call init_database() first.")
    return _session_factory


def _run_migrations(engine: Engine) -> None:
    """
    Lightweight, in-place migrations for SQLite deployments without Alembic.
    Adds any newly introduced columns with safe defaults if they are missing.
    """
    inspector = inspect(engine)
    if not inspector.has_table("spam_logs"):
        return

    existing_columns = {col["name"] for col in inspector.get_columns("spam_logs")}
    with engine.begin() as conn:
        if "action" not in existing_columns:
            conn.execute(text("ALTER TABLE spam_logs ADD COLUMN action VARCHAR(32)"))
        if "points" not in existing_columns:
            conn.execute(text("ALTER TABLE spam_logs ADD COLUMN points INTEGER NOT NULL DEFAULT 0"))
        if "violation_count" not in existing_columns:
            conn.execute(text("ALTER TABLE spam_logs ADD COLUMN violation_count INTEGER NOT NULL DEFAULT 0"))
    if inspector.has_table("guild_configs"):
        existing_cfg_cols = {col["name"] for col in inspector.get_columns("guild_configs")}
        with engine.begin() as conn:
            if "exception_keywords" not in existing_cfg_cols:
                conn.execute(text("ALTER TABLE guild_configs ADD COLUMN exception_keywords TEXT DEFAULT ''"))
            if "currency_report_enabled" not in existing_cfg_cols:
                conn.execute(text("ALTER TABLE guild_configs ADD COLUMN currency_report_enabled BOOLEAN NOT NULL DEFAULT 0"))
            if "currency_report_channel_id" not in existing_cfg_cols:
                conn.execute(text("ALTER TABLE guild_configs ADD COLUMN currency_report_channel_id BIGINT"))
