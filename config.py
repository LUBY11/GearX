from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


@dataclass(slots=True)
class DiscordAppConfig:
    token: str
    application_id: Optional[int] = None
    public_key: Optional[str] = None


@dataclass(slots=True)
class OAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: tuple[str, ...] = ("identify", "guilds")


@dataclass(slots=True)
class DashboardConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    session_secret: str = "replace-me"


@dataclass(slots=True)
class SpamDefaults:
    spam_limit: int = 5
    time_window: int = 7
    link_block: bool = False
    mention_limit: int = 5
    new_user_minutes: int = 10
    exception_keywords: tuple[str, ...] = ()


@dataclass(slots=True)
class CurrencyReportConfig:
    enabled: bool = True
    guild_id: Optional[int] = None
    channel_id: Optional[int] = None
    hour: int = 9
    minute: int = 0
    timezone: str = "Asia/Seoul"
    interval_minutes: int = 120
    quote_currency: str = "KRW"
    currencies: tuple[str, ...] = ("USD", "JPY", "CNY")
    api_url: str = "https://open.er-api.com/v6/latest/{base}"
    api_key: Optional[str] = None


@dataclass(slots=True)
class AppConfig:
    discord: DiscordAppConfig
    oauth: OAuthConfig
    dashboard: DashboardConfig
    database_url: str
    spam_defaults: SpamDefaults = field(default_factory=SpamDefaults)
    currency_report: CurrencyReportConfig = field(default_factory=CurrencyReportConfig)
    target_guild_id: Optional[int] = None


def _ensure_env_loaded() -> None:
    """
    `.env` 우선 로드. 현재 작업 디렉터리에서 찾고, 없으면 이 파일이 위치한
    프로젝트 루트(`project/.env`)도 추가로 확인한다.
    """
    candidates = [
        Path(".env"),  # CWD 기준
        Path(__file__).resolve().parent / ".env",  # project 폴더 기준
    ]
    for path in candidates:
        if path.exists():
            load_dotenv(dotenv_path=path)
            break


def load_config() -> AppConfig:
    _ensure_env_loaded()

    discord_config = DiscordAppConfig(
        token=os.getenv("DISCORD_BOT_TOKEN", ""),
        application_id=_read_int("DISCORD_APPLICATION_ID"),
        public_key=os.getenv("DISCORD_PUBLIC_KEY"),
    )

    oauth_config = OAuthConfig(
        client_id=os.getenv("DISCORD_OAUTH_CLIENT_ID", ""),
        client_secret=os.getenv("DISCORD_OAUTH_CLIENT_SECRET", ""),
        redirect_uri=os.getenv("DISCORD_OAUTH_REDIRECT_URI", "http://localhost:8080/auth/callback"),
    )

    dashboard_config = DashboardConfig(
        host=os.getenv("DASHBOARD_HOST", "0.0.0.0"),
        port=int(os.getenv("DASHBOARD_PORT", "8080")),
        session_secret=os.getenv("DASHBOARD_SESSION_SECRET", "replace-me"),
    )

    database_url = os.getenv("DATABASE_URL", "sqlite:///./spam_guard.sqlite3")

    spam_defaults = SpamDefaults(
        spam_limit=int(os.getenv("DEFAULT_SPAM_LIMIT", "5")),
        time_window=int(os.getenv("DEFAULT_TIME_WINDOW", "7")),
        link_block=os.getenv("DEFAULT_LINK_BLOCK", "true").lower() not in {"0", "false", "no"},
        mention_limit=int(os.getenv("DEFAULT_MENTION_LIMIT", "5")),
        new_user_minutes=int(os.getenv("DEFAULT_NEW_USER_MINUTES", "10")),
        exception_keywords=_read_csv("DEFAULT_EXCEPTION_KEYWORDS"),
    )

    target_guild_id = _read_int("TARGET_GUILD_ID")

    currency_report = CurrencyReportConfig(
        enabled=_read_bool("CURRENCY_REPORT_ENABLED", default=True),
        guild_id=_read_int("CURRENCY_REPORT_GUILD_ID") or target_guild_id,
        channel_id=_read_int("CURRENCY_REPORT_CHANNEL_ID"),
        hour=int(os.getenv("CURRENCY_REPORT_HOUR", "9")),
        minute=int(os.getenv("CURRENCY_REPORT_MINUTE", "0")),
        interval_minutes=max(1, int(os.getenv("CURRENCY_REPORT_INTERVAL_MINUTES", "120"))),
        timezone=os.getenv("CURRENCY_REPORT_TIMEZONE", "Asia/Seoul"),
        quote_currency=os.getenv("CURRENCY_REPORT_QUOTE", "KRW").upper(),
        currencies=_read_csv("CURRENCY_REPORT_CODES") or ("USD", "JPY", "CNY"),
        api_url=os.getenv("CURRENCY_REPORT_API_URL", "https://open.er-api.com/v6/latest/{base}"),
        api_key=os.getenv("CURRENCY_REPORT_API_KEY"),
    )

    return AppConfig(
        discord=discord_config,
        oauth=oauth_config,
        dashboard=dashboard_config,
        database_url=database_url,
        spam_defaults=spam_defaults,
        currency_report=currency_report,
        target_guild_id=target_guild_id,
    )


def _read_int(env_key: str) -> Optional[int]:
    value = os.getenv(env_key)
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _read_csv(env_key: str) -> tuple[str, ...]:
    raw = os.getenv(env_key)
    if not raw:
        return ()
    return tuple(k.strip() for k in raw.split(",") if k.strip())


def _read_bool(env_key: str, default: bool = False) -> bool:
    raw = os.getenv(env_key)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "off", "no"}
