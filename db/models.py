from __future__ import annotations

import datetime as dt
from typing import Protocol

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class GuildConfig(Base):
    __tablename__ = "guild_configs"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    spam_limit: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    time_window: Mapped[int] = mapped_column(Integer, default=7, nullable=False)
    link_block: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    mention_limit: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    new_user_minutes: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    exception_keywords: Mapped[str | None] = mapped_column(Text, default="", nullable=True)
    currency_report_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    currency_report_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow, nullable=False)

    def update_from_defaults(self, defaults: "SpamDefaultsLike") -> None:
        self.spam_limit = defaults.spam_limit
        self.time_window = defaults.time_window
        self.link_block = defaults.link_block
        self.mention_limit = defaults.mention_limit
        self.new_user_minutes = defaults.new_user_minutes


class SpamLog(Base):
    __tablename__ = "spam_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    violation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow, nullable=False)


class SpamDefaultsLike(Protocol):
    spam_limit: int
    time_window: int
    link_block: bool
    mention_limit: int
    new_user_minutes: int
