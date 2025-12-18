from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import socket
import uvicorn

from bot.bot import create_bot
from bot.services.config_service import GuildConfigStore
from bot.services.log_service import SpamLogService
from config import load_config
from db import get_session_factory, init_database
from web.main import create_app


async def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    app_config = load_config()
    if not app_config.discord.token:
        raise RuntimeError("DISCORD_BOT_TOKEN이 설정되어 있지 않습니다.")

    init_database(app_config.database_url)
    session_factory = get_session_factory()

    config_store = GuildConfigStore(session_factory, app_config.spam_defaults)
    log_service = SpamLogService(session_factory)

    bot = create_bot(app_config, config_store, log_service)
    web_app = create_app(app_config, config_store, log_service)

    bind_host = app_config.dashboard.host
    bind_port = _pick_available_port(app_config.dashboard.port)
    logging.info("Dashboard available at http://%s:%s", bind_host, bind_port)

    uvicorn_config = uvicorn.Config(
        web_app,
        host=bind_host,
        port=bind_port,
        log_level="info",
        loop="asyncio",
    )
    web_server = uvicorn.Server(config=uvicorn_config)

    async def run_web():
        await web_server.serve()

    async def run_bot():
        await bot.start(app_config.discord.token)

    async def shutdown():
        if bot.is_closed():
            return
        await bot.close()

    tasks = {asyncio.create_task(run_web()), asyncio.create_task(run_bot())}
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            task.result()
    except asyncio.CancelledError:
        pass
    finally:
        web_server.should_exit = True
        await shutdown()


def _pick_available_port(preferred: int) -> int:
    """
    Attempt to bind the preferred port. If it's in use, increment until a free port is found.
    This prevents frequent test runs from failing due to lingering servers.
    """
    port = preferred
    for _ in range(20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                port += 1
                continue
    raise RuntimeError("No available port found near the preferred port.")


def _run_with_reload() -> None:
    """
    Development helper: restarts the whole process when source files change.
    Uses watchfiles (available via uvicorn[standard]) to supervise asyncio.run(main).
    """
    from watchfiles import run_process

    base_dir = Path(__file__).resolve().parent
    run_process(str(base_dir), target=lambda: asyncio.run(main()))


if __name__ == "__main__":
    try:
        if os.getenv("DEV_RELOAD") == "1":
            _run_with_reload()
        else:
            asyncio.run(main())
    except KeyboardInterrupt:
        pass
