from __future__ import annotations

import datetime as dt
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Deque, Dict, Optional, Tuple

import discord

from bot.services.config_service import GuildSettings
from bot.services.violation_tracker import ViolationTracker
from bot.utils.message_analysis import contains_link, count_mentions, is_similar, normalize_content


class SpamActionType(str, Enum):
    NONE = "none"
    WARN = "warn"
    DELETE = "delete"
    TIMEOUT = "timeout"
    KICK = "kick"


@dataclass(slots=True)
class SpamAction:
    action: SpamActionType
    reason: str
    details: Optional[str] = None
    violation_count: int = 0


@dataclass(slots=True)
class MessageRecord:
    timestamp: dt.datetime
    content: str
    normalized: str


class SpamDetector:
    def __init__(self, violation_tracker: ViolationTracker):
        self._violation_tracker = violation_tracker
        self._history: Dict[Tuple[int, int], Deque[MessageRecord]] = {}

    def register_message(self, message: discord.Message, config: GuildSettings) -> Optional[SpamAction]:
        guild = message.guild
        author = message.author
        if not guild or not isinstance(author, discord.Member):
            return None
        key = (guild.id, author.id)
        now = dt.datetime.utcnow()
        normalized = normalize_content(message.content)

        history = self._history.setdefault(key, deque(maxlen=max(config.spam_limit * 2, 20)))
        history.append(MessageRecord(timestamp=now, content=message.content, normalized=normalized))

        window = dt.timedelta(seconds=config.time_window)
        while history and now - history[0].timestamp > window:
            history.popleft()

        violation_reason, forced_action = self._detect_violation(message, history, config)
        if violation_reason is None:
            return None

        count = self._violation_tracker.increment(guild.id, author.id)
        action_type = forced_action or self._action_for_count(count)

        return SpamAction(
            action=action_type,
            reason=violation_reason,
            details=message.content[:200],
            violation_count=count,
        )

    def _detect_violation(
        self,
        message: discord.Message,
        history: Deque[MessageRecord],
        config: GuildSettings,
    ) -> Tuple[Optional[str], Optional[SpamActionType]]:
        if not config.enabled:
            return None, None

        if message.mention_everyone or "@everyone" in message.content or "@here" in message.content:
            # Mass mentions are treated as a high-severity offense.
            return "대량 멘션(@everyone/@here) 사용", SpamActionType.TIMEOUT

        if len(history) > config.spam_limit:
            return "메시지 도배 감지", None

        has_link = contains_link(message.content)

        mentions = count_mentions(message)
        if config.mention_limit and mentions >= config.mention_limit:
            return "멘션 스팸 감지", None

        if self._has_duplicate_content(history):
            return "동일/유사 메시지 반복", None

        if not self._is_ai_exempt(message.content, config.exception_keywords) and self._has_ai_like_similarity(history):
            return "AI 유사도 스팸 감지", None

        member = message.author
        now = dt.datetime.utcnow()
        if isinstance(member, discord.Member):
            account_age = now - member.created_at.replace(tzinfo=None)
            if member.joined_at:
                join_age = now - member.joined_at.replace(tzinfo=None)
            else:
                join_age = account_age
            min_age = dt.timedelta(minutes=config.new_user_minutes)
            is_new = account_age < min_age or join_age < min_age
            if is_new and (has_link or mentions >= 2 or len(history) >= config.spam_limit):
                return "신규 계정 보호 정책 위반", None

        return None, None

    def _has_duplicate_content(self, history: Deque[MessageRecord]) -> bool:
        if len(history) < 3:
            return False
        recent = list(history)[-3:]
        base = recent[-1].normalized
        duplicates = sum(1 for record in recent[:-1] if is_similar(base, record.normalized))
        return duplicates >= 2

    def _has_ai_like_similarity(self, history: Deque[MessageRecord]) -> bool:
        """
        Detects when a user sends several highly similar messages that are not exact duplicates.
        This is a lightweight stand-in for AI-based similarity scoring.
        """
        if len(history) < 4:
            return False
        recent = list(history)[-4:]
        target = recent[-1].normalized
        similarity_hits = 0
        for record in recent[:-1]:
            if is_similar(target, record.normalized, threshold=0.85):
                similarity_hits += 1
        return similarity_hits >= 2

    def _is_ai_exempt(self, content: str, exception_keywords: list[str]) -> bool:
        base = content.lower()
        for keyword in exception_keywords:
            if keyword.lower() in base:
                return True
        return False

    def reset_user(self, guild_id: int, user_id: int) -> None:
        self._violation_tracker.reset(guild_id, user_id)

    def _action_for_count(self, count: int) -> SpamActionType:
        if count <= 1:
            return SpamActionType.WARN
        if count == 2:
            return SpamActionType.DELETE
        if count == 3:
            return SpamActionType.TIMEOUT
        return SpamActionType.KICK
