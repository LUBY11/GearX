from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.services.config_service import GuildConfigStore, GuildSettings
from bot.services.currency_reporter import CurrencyReporter, CurrencyReportResult
from bot.services.log_service import SpamLogService
from bot.services.spam_detector import SpamAction, SpamActionType, SpamDetector
from bot.services.violation_tracker import ViolationTracker
from bot.utils.permissions import is_privileged
from bot.utils.schedule import compute_next_run, generate_schedule_times
from config import AppConfig
from web.utils.event_hub import event_hub

log = logging.getLogger(__name__)


class SpamGuardBot(commands.Bot):
    def __init__(
        self,
        app_config: AppConfig,
        config_store: GuildConfigStore,
        log_service: SpamLogService,
        spam_detector: SpamDetector,
    ):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.guilds = True

        super().__init__(command_prefix="!", intents=intents)
        self.app_config = app_config
        self.config_store = config_store
        self.log_service = log_service
        self.spam_detector = spam_detector
        self.currency_reporter = CurrencyReporter(app_config.currency_report)
        self._currency_task: Optional[tasks.Loop] = None

    async def setup_hook(self) -> None:
        from bot.events import message_events

        message_events.setup(self)
        self._register_slash_commands()
        self._start_currency_report_task()
        await self.tree.sync()
        log.info("SpamGuardBot setup complete")

    async def fetch_guild_settings(self, guild_id: int) -> GuildSettings:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.config_store.get_or_create, guild_id)

    async def fetch_all_guild_settings(self) -> list[GuildSettings]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: list(self.config_store.list_all()))

    async def log_violation(
        self,
        guild_id: int,
        user_id: int,
        reason: str,
        details: str | None = None,
        action: str | None = None,
        points: int = 0,
        violation_count: int = 0,
    ) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            self.log_service.log_violation,
            guild_id,
            user_id,
            reason,
            details,
            action,
            points,
            violation_count,
        )
        # 실시간 업데이트를 위해 이벤트 발행
        log.info("Publishing SSE event: new_log for guild %s", guild_id)
        await event_hub.publish("new_log", {
            "guild_id": guild_id,
            "user_id": user_id,
            "reason": reason,
            "details": details,
            "action": action,
            "points": points,
            "violation_count": violation_count,
        })

    async def process_spam_action(self, message: discord.Message, action: SpamAction) -> None:
        guild = message.guild
        author = message.author
        if not guild or not isinstance(author, discord.Member):
            return

        reason = action.reason
        if action.action == SpamActionType.WARN:
            await self._send_warning(message, reason, action.violation_count)
        elif action.action == SpamActionType.DELETE:
            await self._delete_message(message, reason)
        elif action.action == SpamActionType.TIMEOUT:
            await self._timeout_member(author, reason)
        elif action.action == SpamActionType.KICK:
            await self._kick_member(author, reason)

        await self.log_violation(
            guild.id,
            author.id,
            reason,
            action.details,
            action=action.action.value,
            points=self._points_for_action(action.action),
            violation_count=action.violation_count,
        )

    def _points_for_action(self, action: SpamActionType) -> int:
        if action == SpamActionType.WARN:
            return 1
        if action == SpamActionType.DELETE:
            return 1
        if action == SpamActionType.TIMEOUT:
            return 3
        if action == SpamActionType.KICK:
            return 5
        return 0

    async def _send_warning(self, message: discord.Message, reason: str, count: int) -> None:
        """Send warning via DM instead of posting in the server."""
        member = message.author
        if not isinstance(member, discord.Member):
            return
        try:
            await member.send(f"⚠️ 스팸 감지 ({reason}) - 누적 {count}회. 계속될 경우 제재됩니다.")
        except discord.HTTPException:
            log.debug("Failed to DM warning to user %s in guild %s", member.id, message.guild and message.guild.id)

    async def _delete_message(self, message: discord.Message, reason: str) -> None:
        try:
            await message.delete()
        except discord.HTTPException:
            log.warning("Failed to delete spam message: guild=%s", message.guild and message.guild.id)

        async with message.channel.typing():
            await message.channel.send(
                f"🧹 스팸 메시지를 삭제했습니다. (사유: {reason})", delete_after=5
            )

    async def _timeout_member(self, member: discord.Member, reason: str) -> None:
        until = dt.datetime.utcnow() + dt.timedelta(minutes=10)
        try:
            await member.timeout(until, reason=reason)
            await member.send(f"⏳ {member.guild.name}에서 10분간 타임아웃되었습니다. 사유: {reason}")
        except discord.HTTPException:
            log.warning("Failed to timeout member %s in guild %s", member.id, member.guild.id)

    async def _kick_member(self, member: discord.Member, reason: str) -> None:
        try:
            await member.send(f"🚪 {member.guild.name}에서 킥되었습니다. 사유: {reason}")
        except discord.HTTPException:
            pass
        try:
            await member.kick(reason=reason)
        except discord.HTTPException:
            log.error("Failed to kick member %s in guild %s", member.id, member.guild.id)

    def _register_slash_commands(self) -> None:
        @self.tree.command(name="spamlog", description="최근 스팸 제재 기록을 확인합니다.")
        @app_commands.checks.has_permissions(manage_guild=True)
        async def slash_spamlog(interaction: discord.Interaction, member: discord.Member):
            history = self.log_service.fetch_user_history(interaction.guild_id, member.id, limit=5)
            if not history:
                await interaction.response.send_message(
                    f"{member.mention}에 대한 제재 기록이 없습니다.", ephemeral=True
                )
                return
            lines = []
            for entry in history:
                ts = entry.timestamp.strftime("%m-%d %H:%M")
                lines.append(
                    f"[{ts}] {entry.action or 'auto'} pts={entry.points}#{entry.violation_count}: {entry.reason}"
                )
            await interaction.response.send_message("\n".join(lines), ephemeral=True)

        @self.tree.command(name="forgive", description="해당 사용자의 스트라이크를 초기화합니다.")
        @app_commands.checks.has_permissions(manage_guild=True)
        async def slash_forgive(
            interaction: discord.Interaction,
            member: discord.Member,
            reason: str = "mod manual reset",
        ):
            self.spam_detector.reset_user(interaction.guild_id, member.id)
            await self.log_violation(
                interaction.guild_id, member.id, f"수동 리셋: {reason}", action="forgive", points=0
            )
            await interaction.response.send_message(
                f"{member.mention} 스트라이크를 초기화했습니다.", ephemeral=True
            )

        @self.tree.command(name="spamstatus", description="현재 스팸 설정을 확인합니다.")
        @app_commands.checks.has_permissions(manage_guild=True)
        async def slash_spamstatus(interaction: discord.Interaction):
            settings = await self.fetch_guild_settings(interaction.guild_id)
            msg = (
                f"스팸 차단: {'ON' if settings.enabled else 'OFF'}\n"
                f"메시지 {settings.time_window}s 내 {settings.spam_limit}회\n"
                f"멘션 한도: {settings.mention_limit}\n"
                f"링크 차단: {'ON' if settings.link_block else 'OFF'}\n"
                f"AI 예외 키워드: {', '.join(settings.exception_keywords) or '없음'}"
            )
            await interaction.response.send_message(msg, ephemeral=True)

        @self.tree.command(name="test", description="디버깅용 테스트 로그를 기록합니다.")
        @app_commands.checks.has_permissions(manage_guild=True)
        async def slash_test(interaction: discord.Interaction):
            await self.log_violation(
                interaction.guild_id,
                interaction.user.id,
                reason="테스트 로그",
                details="manual /test trigger",
                action="test",
                points=0,
                violation_count=0,
            )
            await interaction.response.send_message("테스트 로그를 기록했습니다.", ephemeral=True)

        @self.tree.command(name="warntest", description="경고 DM 테스트 후 로그에 기록합니다.")
        async def slash_warntest(interaction: discord.Interaction):
            if interaction.guild_id is None:
                await interaction.response.send_message("길드 안에서만 실행 가능합니다.", ephemeral=True)
                return
            # DM으로 경고 메시지 전송
            try:
                await interaction.user.send(f"⚠️ {interaction.guild.name} 테스트 경고: 테스트 경고 메시지입니다.")
                dm_sent = True
            except discord.HTTPException:
                dm_sent = False

            await self.log_violation(
                interaction.guild_id,
                interaction.user.id,
                reason="테스트 경고",
                details=f"warntest slash command (DM: {'성공' if dm_sent else '실패'})",
                action="test",
                points=0,
                violation_count=0,
            )
            if dm_sent:
                await interaction.response.send_message("DM 경고를 전송하고 테스트 로그에 기록했습니다.", ephemeral=True)
            else:
                await interaction.response.send_message("DM 전송 실패, 테스트 로그에만 기록했습니다.", ephemeral=True)

        @self.tree.command(name="currencytest", description="환율 보고서를 즉시 전송합니다.")
        @app_commands.checks.has_permissions(manage_guild=True)
        async def slash_currencytest(
            interaction: discord.Interaction,
            channel: Optional[discord.TextChannel] = None,
        ):
            if interaction.guild_id is None:
                await interaction.response.send_message("길드 안에서만 실행 가능합니다.", ephemeral=True)
                return
            channel_id = channel.id if channel else None
            success = await self.trigger_currency_report(interaction.guild_id, channel_id, source="slash")
            if success:
                await interaction.response.send_message("환율 보고서를 전송했습니다.", ephemeral=True)
            else:
                await interaction.response.send_message("환율 보고서 전송에 실패했습니다. 설정을 확인해 주세요.", ephemeral=True)
    def _start_currency_report_task(self) -> None:
        cfg = self.app_config.currency_report
        if not cfg.enabled:
            log.info("Currency report disabled globally.")
            return

        times = generate_schedule_times(cfg)
        if not times:
            log.warning("No valid schedule times computed; currency report task not started.")
            return

        @tasks.loop(time=times)
        async def currency_loop() -> None:
            await self._post_currency_report()

        @currency_loop.before_loop
        async def before_currency_loop() -> None:
            await self.wait_until_ready()

        self._currency_task = currency_loop
        currency_loop.start()
        log.info(
            "Scheduled currency report every %d minutes (per-guild channels, timezone=%s, next=%s)",
            cfg.interval_minutes,
            cfg.timezone,
            compute_next_run(cfg).isoformat(),
        )

    async def _post_currency_report(
        self,
        guild_id: Optional[int] = None,
        channel_override: Optional[int] = None,
        source: str = "schedule",
    ) -> bool:
        cfg = self.app_config.currency_report
        if not cfg.enabled:
            return False

        report = await self.currency_reporter.build_report()
        if not report:
            log.warning("Currency report skipped - no rates fetched")
            return False

        if guild_id is not None:
            target_settings = [await self.fetch_guild_settings(guild_id)]
        else:
            target_settings = await self.fetch_all_guild_settings()

        targets: list[tuple[int, int]] = []
        for settings in target_settings:
            if not settings.currency_report_enabled:
                continue
            resolved_channel = (
                channel_override if channel_override and settings.guild_id == guild_id else settings.currency_report_channel_id
            )
            if not resolved_channel:
                resolved_channel = cfg.channel_id
            if not resolved_channel:
                log.debug("Guild %s has currency report enabled but no channel configured.", settings.guild_id)
                continue
            targets.append((settings.guild_id, resolved_channel))

        if not targets:
            log.info("No currency report targets available for source %s", source)
            return False

        delivered = False
        for gid, channel_id in targets:
            channel = await self._resolve_report_channel(channel_id, guild_id=gid)
            if not channel:
                log.warning("Channel %s for guild %s unavailable for currency report.", channel_id, gid)
                continue
            try:
                embed = self._build_currency_embed(report)
                if embed:
                    await channel.send(embed=embed)
                else:
                    await channel.send(report.text)
                delivered = True
                log.info("Posted currency report to guild=%s channel=%s source=%s", gid, channel_id, source)
            except discord.HTTPException:
                log.exception("Failed to send currency report to guild=%s channel=%s", gid, channel_id)
        return delivered

    async def trigger_currency_report(
        self,
        guild_id: int,
        channel_id: Optional[int] = None,
        source: str = "manual",
    ) -> bool:
        return await self._post_currency_report(guild_id=guild_id, channel_override=channel_id, source=source)

    async def _resolve_report_channel(
        self,
        channel_id: int,
        guild_id: Optional[int] = None,
    ) -> Optional[discord.abc.Messageable]:
        channel = self.get_channel(channel_id)
        if isinstance(channel, discord.abc.Messageable):
            if guild_id and getattr(channel, "guild", None) and channel.guild.id != guild_id:
                log.warning("Channel %s does not belong to guild %s", channel_id, guild_id)
                return None
            return channel
        try:
            fetched = await self.fetch_channel(channel_id)
        except discord.HTTPException:
            return None
        if isinstance(fetched, discord.abc.Messageable):
            if guild_id and getattr(fetched, "guild", None) and fetched.guild.id != guild_id:
                log.warning("Fetched channel %s not part of guild %s", channel_id, guild_id)
                return None
            return fetched
        return None

    def _build_currency_embed(self, report: CurrencyReportResult) -> Optional[discord.Embed]:
        data = report.to_embed_dict()
        try:
            embed = discord.Embed.from_dict(data)
            return embed
        except Exception:
            log.exception("Failed to build currency embed; falling back to plain text")
            return None


def create_bot(app_config: AppConfig, config_store: GuildConfigStore, log_service: SpamLogService) -> SpamGuardBot:
    tracker = ViolationTracker()
    detector = SpamDetector(tracker)
    bot = SpamGuardBot(app_config, config_store, log_service, detector)
    return bot
