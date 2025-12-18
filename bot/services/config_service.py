from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Dict, Iterable

from sqlalchemy.orm import Session, sessionmaker

from config import SpamDefaults
from db.models import GuildConfig


@dataclass(slots=True)
class GuildSettings:
    guild_id: int
    enabled: bool
    spam_limit: int
    time_window: int
    link_block: bool
    mention_limit: int
    new_user_minutes: int
    exception_keywords: list[str]
    currency_report_enabled: bool
    currency_report_channel_id: int | None

    @classmethod
    def from_model(cls, model: GuildConfig) -> "GuildSettings":
        return cls(
            guild_id=model.guild_id,
            enabled=model.enabled,
            spam_limit=model.spam_limit,
            time_window=model.time_window,
            link_block=model.link_block,
            mention_limit=model.mention_limit,
            new_user_minutes=model.new_user_minutes,
            exception_keywords=_split_keywords(model.exception_keywords),
            currency_report_enabled=model.currency_report_enabled,
            currency_report_channel_id=model.currency_report_channel_id,
        )


class GuildConfigStore:
    """
    Simple thread-safe cache around guild configuration rows.
    """

    def __init__(self, session_factory: sessionmaker, defaults: SpamDefaults):
        self._session_factory = session_factory
        self._defaults = defaults
        self._cache: Dict[int, GuildSettings] = {}
        self._lock = threading.RLock()

    def get_or_create(self, guild_id: int) -> GuildSettings:
        with self._lock:
            settings = self._cache.get(guild_id)
            if settings:
                return settings

        with self._session_factory() as session:
            model = session.get(GuildConfig, guild_id)
            if model is None:
                model = self._create_default_model(session, guild_id)
            settings = GuildSettings.from_model(model)

        with self._lock:
            self._cache[guild_id] = settings
        return settings

    def update_settings(self, guild_id: int, **kwargs: int | bool) -> GuildSettings:
        with self._session_factory() as session:
            model = session.get(GuildConfig, guild_id)
            if model is None:
                model = self._create_default_model(session, guild_id)
            for key, value in kwargs.items():
                if hasattr(model, key):
                    if key == "exception_keywords":
                        setattr(model, key, _join_keywords(value))
                    else:
                        setattr(model, key, value)
            session.add(model)
            session.commit()
            session.refresh(model)
            settings = GuildSettings.from_model(model)

        with self._lock:
            self._cache[guild_id] = settings

        return settings

    def list_all(self) -> Iterable[GuildSettings]:
        with self._session_factory() as session:
            configs = session.query(GuildConfig).all()
            return [GuildSettings.from_model(cfg) for cfg in configs]

    def delete_guild(self, guild_id: int) -> None:
        with self._session_factory() as session:
            model = session.get(GuildConfig, guild_id)
            if model:
                session.delete(model)
                session.commit()
        with self._lock:
            self._cache.pop(guild_id, None)

    def _create_default_model(self, session: Session, guild_id: int) -> GuildConfig:
        model = GuildConfig(
            guild_id=guild_id,
            enabled=True,
            spam_limit=self._defaults.spam_limit,
            time_window=self._defaults.time_window,
            link_block=self._defaults.link_block,
            mention_limit=self._defaults.mention_limit,
            new_user_minutes=self._defaults.new_user_minutes,
            exception_keywords=",".join(self._defaults.exception_keywords),
            currency_report_enabled=False,
            currency_report_channel_id=None,
        )
        session.add(model)
        session.commit()
        session.refresh(model)
        return model


def _split_keywords(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _join_keywords(keywords: list[str] | tuple[str, ...] | str | None) -> str:
    if keywords is None:
        return ""
    if isinstance(keywords, str):
        return keywords
    return ",".join(k.strip() for k in keywords if k.strip())
