from __future__ import annotations

import discord


def is_privileged(member: discord.Member) -> bool:
    perms = member.guild_permissions
    if member.guild is not None and member.guild.owner_id == member.id:
        return True
    return perms.administrator or perms.manage_guild or perms.manage_messages or perms.kick_members

