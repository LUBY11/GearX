from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

import discord

URL_PATTERN = re.compile(
    r"(https?:\/\/|www\.)[^\s]+|discord\.gg\/[^\s]+",
    re.IGNORECASE,
)


def normalize_content(content: str) -> str:
    text = unicodedata.normalize("NFKD", content)
    text = "".join(ch for ch in text if not unicodedata.category(ch).startswith("C"))
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def contains_link(content: str) -> bool:
    return bool(URL_PATTERN.search(content))


def count_mentions(message: discord.Message) -> int:
    count = len(message.mentions) + len(message.role_mentions)
    if message.mention_everyone:
        count += 1
    count += message.content.count("@everyone") + message.content.count("@here")
    return count


def is_similar(a: str, b: str, threshold: float = 0.9) -> bool:
    """Fuzzy match helper for near-identical content."""
    return SequenceMatcher(None, a, b).ratio() >= threshold
