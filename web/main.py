from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.templating import Jinja2Templates

from bot.services.config_service import GuildConfigStore
from bot.services.log_service import SpamLogService
from config import AppConfig
from web.routes import auth, dashboard
from web.utils.discord_oauth import DiscordOAuthClient


def create_app(app_config: AppConfig, config_store: GuildConfigStore, log_service: SpamLogService) -> FastAPI:
    app = FastAPI(title="Spam Guard Dashboard")
    template_dir = Path(__file__).parent / "templates"
    static_dir = Path(__file__).parent / "static"

    app.state.config = app_config
    app.state.config_store = config_store
    app.state.log_service = log_service
    app.state.templates = Jinja2Templates(directory=str(template_dir))
    oauth_client = DiscordOAuthClient(app_config.oauth)
    # 봇 토큰을 OAuth 클라이언트에 주입해 멤버 조회 시 재사용한다.
    oauth_client.bot_token = app_config.discord.token
    app.state.oauth_client = oauth_client

    app.add_middleware(SessionMiddleware, secret_key=app_config.dashboard.session_secret)

    app.include_router(auth.router)
    app.include_router(dashboard.router)

    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    return app
