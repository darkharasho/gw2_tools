import asyncio
from unittest.mock import AsyncMock, MagicMock

from axitools.cogs.audit import AuditCog
from axitools.storage import GuildConfig


def _make_cog(blacklist):
    config = GuildConfig.default()
    config.audit_channel_blacklist = list(blacklist)
    cog = AuditCog.__new__(AuditCog)
    bot = MagicMock()
    bot.get_config.return_value = config
    cog.bot = bot
    return cog


def test_is_channel_blacklisted_true_and_false():
    cog = _make_cog([123])
    guild = MagicMock()
    guild.id = 1
    assert cog._is_channel_blacklisted(guild, 123) is True
    assert cog._is_channel_blacklisted(guild, 999) is False
    assert cog._is_channel_blacklisted(guild, None) is False


def test_message_delete_suppressed_when_blacklisted():
    cog = _make_cog([123])
    cog._log_discord_event = AsyncMock()
    message = MagicMock()
    message.guild = MagicMock()
    message.guild.id = 1
    message.channel.id = 123
    asyncio.run(cog.on_message_delete(message))
    cog._log_discord_event.assert_not_called()


def test_voice_state_suppressed_when_before_channel_blacklisted():
    cog = _make_cog([55])
    cog._log_discord_event = AsyncMock()
    member = MagicMock()
    member.guild = MagicMock()
    member.guild.id = 1
    before = MagicMock()
    before.channel.id = 55
    after = MagicMock()
    after.channel.id = 77
    asyncio.run(cog.on_voice_state_update(member, before, after))
    cog._log_discord_event.assert_not_called()


def test_channel_delete_suppressed_when_blacklisted():
    cog = _make_cog([321])
    cog._log_discord_event = AsyncMock()
    cog._find_audit_entry_user = AsyncMock(return_value=None)
    channel = MagicMock()
    channel.guild = MagicMock()
    channel.guild.id = 1
    channel.id = 321
    asyncio.run(cog.on_guild_channel_delete(channel))
    cog._log_discord_event.assert_not_called()
