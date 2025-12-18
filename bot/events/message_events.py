from __future__ import annotations

import logging

import discord
from discord.ext import commands

from bot.bot import SpamGuardBot
from bot.utils.permissions import is_privileged

log = logging.getLogger(__name__)


def setup(bot: SpamGuardBot) -> None:
    @bot.event
    async def on_ready() -> None:
        log.info("봇 로그인 완료 - %s (%s)", bot.user, bot.user and bot.user.id)

    @bot.event
    async def on_guild_join(guild: discord.Guild) -> None:
        await bot.fetch_guild_settings(guild.id)
        log.info("새로운 서버에 참여했습니다: %s (%s)", guild.name, guild.id)

    @bot.event
    async def on_message(message: discord.Message) -> None:
        await _process_message(bot, message)
        await bot.process_commands(message)

    @bot.event
    async def on_member_ban(guild: discord.Guild, user: discord.User) -> None:
        # 밴 내역도 로그에서 확인할 수 있도록 저장
        await bot.log_violation(
            guild.id,
            user.id,
            reason="길드 밴",
            details=None,
            action="ban",
            points=5,
            violation_count=0,
        )
        log.info("Ban logged: guild=%s user=%s", guild.id, user.id)

    @bot.command(name="spamstatus")
    @commands.has_permissions(manage_guild=True)
    async def spam_status(ctx: commands.Context) -> None:
        settings = await bot.fetch_guild_settings(ctx.guild.id)
        await ctx.send(
            f"스팸 차단 상태: {'ON' if settings.enabled else 'OFF'} / "
            f"메시지 {settings.time_window}s 내 {settings.spam_limit}회 / "
            f"멘션 한도 {settings.mention_limit} / 링크 차단 {'ON' if settings.link_block else 'OFF'}"
        )

    @bot.command(name="spamlog")
    @commands.has_permissions(manage_guild=True)
    async def spam_log(ctx: commands.Context, member: discord.Member) -> None:
        history = bot.log_service.fetch_user_history(ctx.guild.id, member.id, limit=5)
        if not history:
            await ctx.send(f"{member.mention}에 대한 기록이 없습니다.")
            return

        lines = []
        for entry in history:
            ts = entry.timestamp.strftime("%m-%d %H:%M")
            summary = f"[{ts}] {entry.action or 'auto'} pts={entry.points}#{entry.violation_count}: {entry.reason}"
            lines.append(summary)
        await ctx.send("최근 제재 기록:\n" + "\n".join(lines))

    @bot.command(name="forgive")
    @commands.has_permissions(manage_guild=True)
    async def forgive(ctx: commands.Context, member: discord.Member, *, reason: str = "mod manual reset") -> None:
        bot.spam_detector.reset_user(ctx.guild.id, member.id)
        await bot.log_violation(ctx.guild.id, member.id, f"수동 리셋: {reason}", action="forgive", points=0)
        await ctx.send(f"{member.mention} 스트라이크를 초기화했습니다.")


async def _process_message(bot: SpamGuardBot, message: discord.Message) -> None:
    if not message.guild or message.author.bot:
        return
    member = message.author
    if not isinstance(member, discord.Member):
        return
    if is_privileged(member):
        return

    settings = await bot.fetch_guild_settings(message.guild.id)
    action = bot.spam_detector.register_message(message, settings)
    if action:
        await bot.process_spam_action(message, action)
