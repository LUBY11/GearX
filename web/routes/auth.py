from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from config import AppConfig
from web.utils.discord_oauth import DiscordOAuthClient, generate_state

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger(__name__)


def get_oauth_client(request: Request) -> DiscordOAuthClient:
    return request.app.state.oauth_client


def get_app_config(request: Request) -> AppConfig:
    return request.app.state.config


@router.get("/login")
async def login(request: Request, client: DiscordOAuthClient = Depends(get_oauth_client)):
    state = generate_state()
    request.session["oauth_state"] = state
    authorize_url = client.authorization_url(state)
    return RedirectResponse(authorize_url, status_code=302)


@router.get("/callback")
async def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    client: DiscordOAuthClient = Depends(get_oauth_client),
    app_config: AppConfig = Depends(get_app_config),
):
    stored_state = request.session.get("oauth_state")
    if not stored_state or state != stored_state:
        raise HTTPException(status_code=400, detail="잘못된 OAuth 상태 값입니다.")
    if not code:
        raise HTTPException(status_code=400, detail="OAuth 코드가 없습니다.")
    request.session.pop("oauth_state", None)

    token = await client.exchange_code(code)
    user = await client.fetch_user(token)
    guilds = await client.fetch_guilds(token)

    if app_config.target_guild_id:
        target = str(app_config.target_guild_id)
        guilds = [g for g in guilds if str(g.get("id")) == target]
        if not guilds:
            raise HTTPException(status_code=403, detail="지정된 서버에 대한 접근 권한이 없습니다.")

    request.session["user"] = {
        "id": user["id"],
        "username": user["username"],
        "discriminator": user.get("discriminator"),
        "avatar": user.get("avatar"),
    }
    request.session["guilds"] = guilds
    request.session["token"] = {
        "access_token": token.access_token,
        "token_type": token.token_type,
    }

    log.info(
        "OAuth complete: user=%s#%s, guilds=%d",
        user.get("username"),
        user.get("discriminator"),
        len(guilds),
    )

    return RedirectResponse("/", status_code=302)


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=302)
