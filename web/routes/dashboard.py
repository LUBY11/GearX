from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, HTMLResponse, StreamingResponse
import httpx
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from bot.services.config_service import GuildConfigStore
from bot.services.currency_reporter import CurrencyReporter, CurrencyReportResult
from bot.utils.schedule import compute_next_run
from bot.services.log_service import SpamLogService
from config import AppConfig
from web.utils.discord_oauth import DiscordOAuthClient
from web.utils.event_hub import event_hub

router = APIRouter(tags=["dashboard"])
log = logging.getLogger(__name__)
CHANNEL_CACHE_TTL_SECONDS = 300
CHANNEL_CACHE: Dict[int, Dict[str, Any]] = {}


def get_templates(request: Request):
    return request.app.state.templates


def get_config_store(request: Request) -> GuildConfigStore:
    return request.app.state.config_store


def get_log_service(request: Request) -> SpamLogService:
    return request.app.state.log_service


def get_app_config(request: Request) -> AppConfig:
    return request.app.state.config


def get_oauth_client(request: Request) -> DiscordOAuthClient:
    return request.app.state.oauth_client


def require_session_user(request: Request) -> Dict[str, Any]:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=403, detail="로그인이 필요합니다.")
    return user


def _ensure_guild_access(request: Request, guild_id: int) -> Dict[str, Any]:
    guilds = request.session.get("guilds") or []
    for guild in guilds:
        if int(guild["id"]) == guild_id:
            return guild
    # 세션에 없더라도 수동 추가된 길드는 통과시킨다.
    return {"id": guild_id}


async def _fetch_guild_text_channels(
    guild_id: int,
    oauth_client: DiscordOAuthClient,
    bot_token: str,
) -> list[Dict[str, Any]]:
    entry = CHANNEL_CACHE.get(guild_id)
    now = dt.datetime.utcnow().timestamp()
    if entry:
        ts = entry.get("ts", 0)
        channels = entry.get("channels")
        if isinstance(ts, (int, float)) and isinstance(channels, list) and now - ts < CHANNEL_CACHE_TTL_SECONDS:
            return channels
    channels_raw = await oauth_client.fetch_guild_channels(guild_id, bot_token)
    text_channels = []
    for ch in channels_raw:
        if ch.get("type") == 0:
            text_channels.append({
                "id": str(ch.get("id")),
                "name": ch.get("name", f"channel-{ch.get('id')}"),
            })
    CHANNEL_CACHE[guild_id] = {"ts": now, "channels": text_channels}
    return text_channels


def _resolve_timezone(tz_name: str) -> dt.tzinfo:
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        offset = 9 if tz_name in {"Asia/Seoul", "KST"} else 0
        log.warning("Timezone %s not found for dashboard. Falling back to UTC%+d.", tz_name, offset)
        return dt.timezone(dt.timedelta(hours=offset))


@router.get("/events")
async def sse_events(request: Request):
    """Server-Sent Events endpoint for real-time updates."""
    async def event_generator():
        queue = event_hub.subscribe()
        log.debug("SSE client connected")
        try:
            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    log.debug("SSE client disconnected")
                    break
                try:
                    # Wait for message with timeout
                    message = await asyncio.wait_for(queue.get(), timeout=15.0)
                    log.debug("SSE sending message: %s", message[:100])
                    yield f"data: {message}\n\n"
                except asyncio.TimeoutError:
                    # Send heartbeat to keep connection alive
                    yield ": heartbeat\n\n"
        finally:
            event_hub.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        }
    )


@router.post("/guilds/add")
async def add_guild(
    request: Request,
    guild_id: int = Form(...),
    config_store: GuildConfigStore = Depends(get_config_store),
    oauth_client: DiscordOAuthClient = Depends(get_oauth_client),
    user=Depends(require_session_user),
):
    """
    Allow manual registration of a guild ID that the bot is already in.
    This creates a default config row if missing and redirects to its settings tab.
    """
    config_store.get_or_create(guild_id)
    guilds = request.session.get("guilds") or []
    exists = any(int(g["id"]) == guild_id for g in guilds)
    guild_name = None
    if oauth_client.bot_token:
        info = await oauth_client.fetch_guild(guild_id, oauth_client.bot_token)
        if info and "name" in info:
            guild_name = info["name"]
    if not exists:
        guilds.append({"id": str(guild_id), "name": guild_name or f"Guild {guild_id}"})
    else:
        for g in guilds:
            if int(g["id"]) == guild_id and guild_name:
                g["name"] = guild_name
    request.session["guilds"] = guilds
    return RedirectResponse(f"/guilds/{guild_id}?tab=settings", status_code=303)


@router.post("/guilds/delete")
async def delete_guild(
    request: Request,
    guild_id: int = Form(...),
    config_store: GuildConfigStore = Depends(get_config_store),
    user=Depends(require_session_user),
):
    config_store.delete_guild(guild_id)
    guilds = request.session.get("guilds") or []
    guilds = [g for g in guilds if int(g.get("id", 0)) != guild_id]
    request.session["guilds"] = guilds
    return RedirectResponse("/guilds/manage", status_code=303)


@router.post("/guilds/{guild_id}/users/adjust")
async def adjust_user_points(
    guild_id: int,
    request: Request,
    user_id: int = Form(...),
    delta: int = Form(...),
    reason: str = Form("manual adjust"),
    log_service: SpamLogService = Depends(get_log_service),
    user=Depends(require_session_user),
):
    _ensure_guild_access(request, guild_id)
    log_service.log_violation(
        guild_id=guild_id,
        user_id=user_id,
        reason=reason,
        details=None,
        action="adjust",
        points=delta,
        violation_count=0,
    )
    return RedirectResponse(f"/guilds/{guild_id}?tab=users", status_code=303)


@router.get("/guilds/manage", response_class=HTMLResponse)
async def manage_guilds(
    request: Request,
    templates=Depends(get_templates),
    config_store: GuildConfigStore = Depends(get_config_store),
    oauth_client: DiscordOAuthClient = Depends(get_oauth_client),
    user=Depends(require_session_user),
):
    configs = list(config_store.list_all())
    total = len(configs)
    session_guilds = {int(g["id"]): g.get("name") for g in (request.session.get("guilds") or []) if "id" in g}
    first_session_guild = next(iter(session_guilds.keys()), None)
    bot_token = oauth_client.bot_token or ""
    names: Dict[int, str] = {}

    # 병렬로 이름 조회
    async def fetch_name(gid: int, current_name: str | None) -> tuple[int, str | None]:
        if current_name:
            return gid, current_name
        if bot_token:
            info = await oauth_client.fetch_guild(gid, bot_token)
            if info and "name" in info:
                return gid, info["name"]
        return gid, None

    tasks = []
    for cfg in configs:
        tasks.append(fetch_name(cfg.guild_id, session_guilds.get(cfg.guild_id)))

    results = await asyncio.gather(*tasks)
    for gid, name in results:
        names[gid] = name or f"Guild {gid}"

    # 세션 길드 이름도 최신화
    updated_session = []
    for gid, current in session_guilds.items():
        updated_session.append({"id": str(gid), "name": names.get(gid, current or f"Guild {gid}")})
    if updated_session:
        request.session["guilds"] = updated_session
    return templates.TemplateResponse(
        "guild_manage.html",
        {
            "request": request,
            "user": user,
            "configs": configs,
            "total": total,
            "names": names,
            "active_guild_id": first_session_guild,
        },
    )


@router.get("/")
async def index(
    request: Request,
    templates=Depends(get_templates),
    config_store: GuildConfigStore = Depends(get_config_store),
    log_service: SpamLogService = Depends(get_log_service),
    app_config: AppConfig = Depends(get_app_config),
    oauth_client: DiscordOAuthClient = Depends(get_oauth_client),
    guild_id: Optional[int] = Query(None),
):
    user = request.session.get("user")
    guilds = request.session.get("guilds") or []
    log.info("Dashboard index: user=%s guilds=%d", user and user.get("id"), len(guilds))

    # 개인용: target_guild_id가 설정된 경우 해당 길드를 기본 선택
    # 세션에 저장된 선택 길드 우선, 그다음 query param, 마지막으로 config
    selected_id: Optional[int] = None
    if guild_id:
        selected_id = int(guild_id)
        # 사용자가 드롭다운에서 선택한 경우 세션에 저장
        request.session["selected_guild_id"] = selected_id
    elif request.session.get("selected_guild_id"):
        selected_id = request.session.get("selected_guild_id")
    elif app_config.target_guild_id:
        selected_id = app_config.target_guild_id

    if not user:
        return templates.TemplateResponse(
            "landing.html",
            {
                "request": request,
                "guilds": [],
                "user": None,
            },
        )

    # 세션에 있는 길드에서 선택된 길드 찾기
    all_guilds_map: Dict[int, Dict[str, Any]] = {}
    for cfg in config_store.list_all():
        all_guilds_map[cfg.guild_id] = {"id": cfg.guild_id, "name": None}
    for g in guilds:
        if "id" in g:
            gid = int(g["id"])
            all_guilds_map[gid] = {"id": gid, "name": g.get("name")}

    if selected_id not in all_guilds_map and all_guilds_map:
        selected_id = next(iter(all_guilds_map.keys()))
    if selected_id:
        request.session["selected_guild_id"] = selected_id

    enriched = []
    recent_logs = []
    kicked_logs = []
    active_guild_name = None
    all_guilds_list = list(all_guilds_map.values())

    # 병렬 이름 조회 준비
    missing_name_guilds = [g for g in all_guilds_list if not g.get("name")]
    if missing_name_guilds and oauth_client.bot_token:
        async def fetch_guild_name(g_dict: Dict[str, Any]):
            gid = int(g_dict["id"])
            info = await oauth_client.fetch_guild(gid, oauth_client.bot_token)
            if info and "name" in info:
                g_dict["name"] = info["name"]

        await asyncio.gather(*(fetch_guild_name(g) for g in missing_name_guilds))

    for gid, ginfo in all_guilds_map.items():
        if selected_id and gid != selected_id:
            continue
        settings = config_store.get_or_create(gid)
        entries = log_service.fetch_logs(gid, limit=15)
        enriched.append({"info": ginfo, "settings": settings})
        for entry in entries:
            recent_logs.append({"guild": ginfo, "log": entry})
            if (entry.action or "").lower() in {"kick", "ban"}:
                kicked_logs.append({"guild": ginfo, "log": entry})
        if ginfo.get("name"):
            active_guild_name = ginfo["name"]

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "guilds": enriched,
            "recent_logs": sorted(recent_logs, key=lambda x: x["log"].timestamp, reverse=True)[:30],
            "kicked_logs": sorted(kicked_logs, key=lambda x: x["log"].timestamp, reverse=True)[:30],
            "active_guild_id": selected_id,
            "active_guild_name": active_guild_name,
            "all_guilds": all_guilds_list,
        },
    )


@router.get("/guilds/{guild_id}", response_class=HTMLResponse)
async def guild_detail(
    guild_id: int,
    request: Request,
    templates=Depends(get_templates),
    config_store: GuildConfigStore = Depends(get_config_store),
    log_service: SpamLogService = Depends(get_log_service),
    oauth_client: DiscordOAuthClient = Depends(get_oauth_client),
    app_config: AppConfig = Depends(get_app_config),
    user=Depends(require_session_user),
    tab: str = Query("settings"),
):
    guild = _ensure_guild_access(request, guild_id)
    guild_name = guild.get("name")
    settings = config_store.get_or_create(guild_id)
    bot_token = oauth_client.bot_token or ""
    request.session["selected_guild_id"] = guild_id

    if not guild_name and bot_token:
        info = await oauth_client.fetch_guild(guild_id, bot_token)
        if info and "name" in info:
            guild_name = info["name"]
            guild["name"] = guild_name

    # 탭별 조건부 데이터 로딩
    logs = []
    debug_logs = []
    user_points = []
    members = {}

    if tab == "logs":
        logs = log_service.fetch_logs(guild_id, limit=50)
    elif tab == "debug":
        debug_logs = log_service.fetch_action_logs(guild_id, action="test", limit=50)
        if bot_token and debug_logs:
            # 디버그 로그의 사용자 닉네임 조회
            debug_user_ids = list(set(log.user_id for log in debug_logs))[:50]
            
            async def fetch_debug_member(uid: int):
                info = await oauth_client.fetch_guild_member(guild_id, uid, bot_token)
                return uid, info

            results = await asyncio.gather(*(fetch_debug_member(uid) for uid in debug_user_ids))
            for uid, info in results:
                if info:
                    members[uid] = info
    elif tab == "users":
        user_points = log_service.fetch_user_points(guild_id, limit=500)
        if bot_token:
            # 병렬 멤버 정보 조회
            member_ids = [row["user_id"] for row in user_points[:50]]
            
            async def fetch_member(uid: int):
                info = await oauth_client.fetch_guild_member(guild_id, uid, bot_token)
                return uid, info

            results = await asyncio.gather(*(fetch_member(uid) for uid in member_ids))
            for uid, info in results:
                if info:
                    members[uid] = info

    currency_next_run_iso = None
    currency_next_run_display = None
    if app_config.currency_report.enabled:
        next_run_dt = compute_next_run(app_config.currency_report)
        currency_next_run_iso = next_run_dt.astimezone(dt.timezone.utc).isoformat()
        local_tz = _resolve_timezone(app_config.currency_report.timezone)
        currency_next_run_display = next_run_dt.astimezone(local_tz).strftime("%Y-%m-%d %H:%M %Z")

    currency_channels: list[Dict[str, Any]] = []
    if tab == "currency" and bot_token:
        try:
            currency_channels = await _fetch_guild_text_channels(guild_id, oauth_client, bot_token)
        except Exception as exc:
            log.warning("Failed to fetch channels for guild %s: %s", guild_id, exc)

    return templates.TemplateResponse(
        "guild_detail.html",
        {
            "request": request,
            "user": user,
            "guild": guild,
            "settings": settings,
            "logs": logs,
            "debug_logs": debug_logs,
            "user_points": user_points,
            "members": members,
            "guild_name": guild_name or f"Guild {guild_id}",
            "active_tab": tab,
            "keywords": settings.exception_keywords,
            "active_guild_id": guild["id"],
            "currency_config": app_config.currency_report,
            "currency_next_run_iso": currency_next_run_iso,
            "currency_next_run_display": currency_next_run_display,
            "currency_channels": currency_channels,
        },
    )


@router.get("/guilds/{guild_id}/exceptions")
async def guild_exceptions(
    guild_id: int,
    request: Request,
    templates=Depends(get_templates),
    config_store: GuildConfigStore = Depends(get_config_store),
    user=Depends(require_session_user),
):
    _ensure_guild_access(request, guild_id)
    return RedirectResponse(f"/guilds/{guild_id}?tab=exceptions", status_code=303)


@router.post("/guilds/{guild_id}/exceptions")
async def update_exceptions(
    guild_id: int,
    request: Request,
    add_keyword: str = Form(""),
    remove_keyword: str = Form(""),
    config_store: GuildConfigStore = Depends(get_config_store),
    user=Depends(require_session_user),
):
    _ensure_guild_access(request, guild_id)
    settings = config_store.get_or_create(guild_id)
    keywords = list(settings.exception_keywords)
    if add_keyword.strip():
        keywords.append(add_keyword.strip())
    if remove_keyword.strip():
        keywords = [k for k in keywords if k != remove_keyword.strip()]
    config_store.update_settings(guild_id, exception_keywords=keywords)
    return RedirectResponse(f"/guilds/{guild_id}?tab=exceptions", status_code=303)


@router.post("/guilds/{guild_id}/settings")
async def update_guild_settings(
    guild_id: int,
    request: Request,
    enabled: bool = Form(False),
    spam_limit: int = Form(...),
    time_window: int = Form(...),
    link_block: bool = Form(False),
    mention_limit: int = Form(...),
    new_user_minutes: int = Form(...),
    exception_keywords: str = Form(""),
    config_store: GuildConfigStore = Depends(get_config_store),
    user=Depends(require_session_user),
):
    _ensure_guild_access(request, guild_id)
    keywords = [part.strip() for part in exception_keywords.split(",") if part.strip()]
    config_store.update_settings(
        guild_id,
        enabled=enabled,
        spam_limit=spam_limit,
        time_window=time_window,
        link_block=link_block,
        mention_limit=mention_limit,
        new_user_minutes=new_user_minutes,
        exception_keywords=keywords,
    )
    return RedirectResponse(f"/guilds/{guild_id}?tab=settings", status_code=303)


@router.post("/guilds/{guild_id}/currency")
async def update_currency_settings(
    guild_id: int,
    request: Request,
    enabled: bool = Form(False),
    channel_id: str = Form(""),
    config_store: GuildConfigStore = Depends(get_config_store),
    user=Depends(require_session_user),
):
    _ensure_guild_access(request, guild_id)
    clean_channel: int | None = None
    if channel_id.strip():
        try:
            clean_channel = int(channel_id.strip())
        except ValueError:
            raise HTTPException(status_code=400, detail="채널 ID가 올바르지 않습니다.")
    config_store.update_settings(
        guild_id,
        currency_report_enabled=enabled,
        currency_report_channel_id=clean_channel,
    )
    return RedirectResponse(f"/guilds/{guild_id}?tab=currency", status_code=303)


@router.post("/guilds/{guild_id}/currency/test")
async def test_currency_report(
    guild_id: int,
    request: Request,
    channel_id: str = Form(""),
    config_store: GuildConfigStore = Depends(get_config_store),
    app_config: AppConfig = Depends(get_app_config),
    user=Depends(require_session_user),
):
    _ensure_guild_access(request, guild_id)
    settings = config_store.get_or_create(guild_id)
    override_channel: int | None = None
    if channel_id.strip():
        try:
            override_channel = int(channel_id.strip())
        except ValueError:
            raise HTTPException(status_code=400, detail="채널 ID가 올바르지 않습니다.")
    target_channel = override_channel or settings.currency_report_channel_id or app_config.currency_report.channel_id
    if not target_channel:
        raise HTTPException(status_code=400, detail="전송할 채널이 설정되어 있지 않습니다.")
    reporter = CurrencyReporter(app_config.currency_report)
    report = await reporter.build_report()
    if not report:
        raise HTTPException(status_code=502, detail="환율 정보를 가져오지 못했습니다.")
    await _send_discord_message(target_channel, app_config.discord.token, report)
    return RedirectResponse(f"/guilds/{guild_id}?tab=currency", status_code=303)


async def _send_discord_message(channel_id: int, token: str, report: CurrencyReportResult) -> None:
    if not token:
        raise HTTPException(status_code=500, detail="봇 토큰이 설정되어 있지 않습니다.")
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "content": "",
        "embeds": [report.to_embed_dict()],
    }
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(url, json=payload, headers=headers)
    if response.status_code >= 400:
        log.error("Failed to send Discord message: %s %s", response.status_code, response.text)
        raise HTTPException(status_code=response.status_code, detail="디스코드 메시지 전송에 실패했습니다.")
