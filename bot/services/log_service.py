from __future__ import annotations

import datetime as dt
from typing import List

from sqlalchemy.orm import sessionmaker
from sqlalchemy import func

from db.models import SpamLog


class SpamLogService:
    def __init__(self, session_factory: sessionmaker):
        self._session_factory = session_factory

    def log_violation(
        self,
        guild_id: int,
        user_id: int,
        reason: str,
        details: str | None = None,
        action: str | None = None,
        points: int = 0,
        violation_count: int = 0,
    ) -> None:
        entry = SpamLog(
            guild_id=guild_id,
            user_id=user_id,
            reason=reason,
            details=details,
            action=action,
            points=points,
            violation_count=violation_count,
            timestamp=dt.datetime.now(),
        )
        with self._session_factory() as session:
            session.add(entry)
            session.commit()

    def fetch_logs(self, guild_id: int, limit: int = 50) -> List[SpamLog]:
        with self._session_factory() as session:
            query = (
                session.query(SpamLog)
                .filter(SpamLog.guild_id == guild_id)
                .order_by(SpamLog.timestamp.desc())
                .limit(limit)
            )
            return list(query)

    def fetch_user_points(self, guild_id: int, limit: int = 200):
        """
        Aggregate per-user spam points for a guild from the log history.
        """
        with self._session_factory() as session:
            rows = (
                session.query(
                    SpamLog.user_id.label("user_id"),
                    func.coalesce(func.sum(SpamLog.points), 0).label("points"),
                    func.coalesce(func.max(SpamLog.violation_count), 0).label("max_violation"),
                    func.count(SpamLog.id).label("entries"),
                )
                .filter(SpamLog.guild_id == guild_id)
                .group_by(SpamLog.user_id)
                .order_by(func.sum(SpamLog.points).desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "user_id": row.user_id,
                    "points": int(row.points),
                    "max_violation": int(row.max_violation),
                    "entries": int(row.entries),
                }
                for row in rows
            ]

    def fetch_action_logs(self, guild_id: int, action: str, limit: int = 50) -> List[SpamLog]:
        with self._session_factory() as session:
            query = (
                session.query(SpamLog)
                .filter(SpamLog.guild_id == guild_id, SpamLog.action == action)
                .order_by(SpamLog.timestamp.desc())
                .limit(limit)
            )
            return list(query)

    def fetch_user_history(self, guild_id: int, user_id: int, limit: int = 20) -> List[SpamLog]:
        with self._session_factory() as session:
            query = (
                session.query(SpamLog)
                .filter(SpamLog.guild_id == guild_id, SpamLog.user_id == user_id)
                .order_by(SpamLog.timestamp.desc())
                .limit(limit)
            )
            return list(query)
