import asyncio
from unittest.mock import AsyncMock, MagicMock

import discord

from axitools.cogs.audit import AuditCog
from axitools.storage import GuildConfig


def _make_cog():
    config = GuildConfig.default()
    config.audit_channel_id = 999
    cog = AuditCog.__new__(AuditCog)
    bot = MagicMock()
    bot.get_config.return_value = config
    store = MagicMock()
    bot.storage.get_audit_store.return_value = store
    cog.bot = bot
    cog._send_audit_message = AsyncMock()
    return cog


def _make_user(*, name, avatar_url):
    user = MagicMock(spec=discord.Member)
    user.id = 12345
    user.name = name
    user.display_name = name
    user.mention = f"<@{user.id}>"
    avatar = MagicMock()
    avatar.url = avatar_url
    user.display_avatar = avatar
    return user


def _captured_embed(cog):
    return cog._send_audit_message.await_args.args[2]


def test_embed_author_uses_target_avatar():
    cog = _make_cog()
    guild = MagicMock()
    guild.id = 1
    target = _make_user(name="arunamarch", avatar_url="https://cdn/avatar.png")

    asyncio.run(
        cog._log_discord_event(
            guild,
            event_type="member_join",
            actor=None,
            target=target,
            details={"Details": "Member joined the server."},
        )
    )

    embed = _captured_embed(cog)
    assert embed.author.name == "arunamarch"
    assert embed.author.icon_url == "https://cdn/avatar.png"


def test_embed_author_falls_back_to_actor():
    cog = _make_cog()
    guild = MagicMock()
    guild.id = 1
    actor = _make_user(name="moderator", avatar_url="https://cdn/mod.png")

    asyncio.run(
        cog._log_discord_event(
            guild,
            event_type="role_create",
            actor=actor,
            target=None,
            details={"Role": "VIP"},
        )
    )

    embed = _captured_embed(cog)
    assert embed.author.name == "moderator"
    assert embed.author.icon_url == "https://cdn/mod.png"


def test_embed_author_falls_back_to_guild_icon():
    cog = _make_cog()
    guild = MagicMock()
    guild.id = 1
    guild.name = "Guild Wars 2 Tools"
    guild.icon.url = "https://cdn/guild.png"

    asyncio.run(
        cog._log_discord_event(
            guild,
            event_type="role_create",
            actor=None,
            target=None,
            details={"Role": "VIP"},
        )
    )

    embed = _captured_embed(cog)
    assert embed.author.name == "Guild Wars 2 Tools"
    assert embed.author.icon_url == "https://cdn/guild.png"
