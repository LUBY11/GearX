from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any, Dict, List

import httpx

from config import OAuthConfig

DISCORD_API_BASE = "https://discord.com/api"


@dataclass(slots=True)
class OAuthToken:
    access_token: str
    refresh_token: str | None
    token_type: str
    expires_in: int


class DiscordOAuthClient:
    def __init__(self, config: OAuthConfig):
        self.config = config
        # 봇 토큰이 있다면 멤버 조회 등에 활용한다.
        self.bot_token = None

    def authorization_url(self, state: str) -> str:
        scope_values = list(self.config.scopes)
        if "guilds" not in scope_values:
            scope_values.append("guilds")
        scopes = "+".join(scope_values)
        return (
            f"{DISCORD_API_BASE}/oauth2/authorize?client_id={self.config.client_id}"
            f"&redirect_uri={self.config.redirect_uri}"
            f"&response_type=code&scope={scopes}&prompt=consent&state={state}"
        )

    async def exchange_code(self, code: str) -> OAuthToken:
        data = {
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.config.redirect_uri,
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{DISCORD_API_BASE}/oauth2/token", data=data)
            response.raise_for_status()
            payload = response.json()
            return OAuthToken(
                access_token=payload["access_token"],
                refresh_token=payload.get("refresh_token"),
                token_type=payload.get("token_type", "Bearer"),
                expires_in=payload.get("expires_in", 0),
            )

    async def fetch_user(self, token: OAuthToken) -> Dict[str, Any]:
        headers = {"Authorization": f"{token.token_type} {token.access_token}"}
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{DISCORD_API_BASE}/users/@me", headers=headers)
            response.raise_for_status()
            return response.json()

    async def fetch_guilds(self, token: OAuthToken) -> List[Dict[str, Any]]:
        headers = {"Authorization": f"{token.token_type} {token.access_token}"}
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{DISCORD_API_BASE}/users/@me/guilds", headers=headers)
            response.raise_for_status()
            return response.json()

    async def fetch_guild_member(self, guild_id: int, user_id: int, bot_token: str) -> Dict[str, Any] | None:
        headers = {"Authorization": f"Bot {bot_token}"}
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{DISCORD_API_BASE}/guilds/{guild_id}/members/{user_id}", headers=headers)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()

    async def fetch_guild(self, guild_id: int, bot_token: str) -> Dict[str, Any] | None:
        headers = {"Authorization": f"Bot {bot_token}"}
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{DISCORD_API_BASE}/guilds/{guild_id}", headers=headers)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()

    async def fetch_guild_channels(self, guild_id: int, bot_token: str) -> List[Dict[str, Any]]:
        headers = {"Authorization": f"Bot {bot_token}"}
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{DISCORD_API_BASE}/guilds/{guild_id}/channels", headers=headers)
            resp.raise_for_status()
            return resp.json()


def generate_state() -> str:
    return secrets.token_urlsafe(16)
