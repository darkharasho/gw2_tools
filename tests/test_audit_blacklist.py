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


def _make_command_cog(blacklist):
    config = GuildConfig.default()
    config.audit_channel_blacklist = list(blacklist)
    cog = AuditCog.__new__(AuditCog)
    bot = MagicMock()
    bot.get_config.return_value = config
    bot.save_config = MagicMock()
    bot.ensure_authorised = AsyncMock(return_value=True)
    cog.bot = bot
    return cog, config


def _make_interaction():
    interaction = MagicMock()
    interaction.guild = MagicMock()
    interaction.guild.id = 1
    interaction.response.send_message = AsyncMock()
    return interaction


def test_blacklist_add_is_idempotent():
    cog, config = _make_command_cog([])
    interaction = _make_interaction()
    channel = MagicMock()
    channel.id = 700
    channel.mention = "<#700>"

    asyncio.run(cog.audit_blacklist_add_command.callback(cog, interaction, channel))
    asyncio.run(cog.audit_blacklist_add_command.callback(cog, interaction, channel))

    assert config.audit_channel_blacklist == [700]
    assert cog.bot.save_config.called


def test_blacklist_remove_by_channel():
    cog, config = _make_command_cog([700, 800])
    interaction = _make_interaction()
    channel = MagicMock()
    channel.id = 700

    asyncio.run(
        cog.audit_blacklist_remove_command.callback(cog, interaction, channel, None)
    )

    assert config.audit_channel_blacklist == [800]


def test_blacklist_remove_by_raw_id():
    cog, config = _make_command_cog([700, 800])
    interaction = _make_interaction()

    asyncio.run(
        cog.audit_blacklist_remove_command.callback(cog, interaction, None, "800")
    )

    assert config.audit_channel_blacklist == [700]


def test_blacklist_list_runs():
    cog, config = _make_command_cog([700])
    interaction = _make_interaction()
    asyncio.run(cog.audit_blacklist_list_command.callback(cog, interaction))
    interaction.response.send_message.assert_awaited()


def test_blacklist_add_replies_with_branded_embed():
    cog, config = _make_command_cog([])
    interaction = _make_interaction()
    channel = MagicMock()
    channel.id = 700

    asyncio.run(cog.audit_blacklist_add_command.callback(cog, interaction, channel))

    _, kwargs = interaction.response.send_message.call_args
    embed = kwargs.get("embed")
    assert embed is not None
    assert embed.footer.text == "Guild Wars 2 Tools"
    assert embed.title == "Audit blacklist"


def test_blacklist_channel_id_autocomplete_matches_name_and_empty():
    cog, config = _make_command_cog([700, 800])
    interaction = _make_interaction()

    def _get_channel(cid):
        names = {700: "general-chat", 800: "announcements"}
        if cid in names:
            channel = MagicMock()
            channel.name = names[cid]
            return channel
        return None

    interaction.guild.get_channel.side_effect = _get_channel

    # Query by a substring of the channel name.
    matches = asyncio.run(
        cog.audit_blacklist_channel_id_autocomplete(interaction, "gener")
    )
    assert [c.value for c in matches] == ["700"]
    assert matches[0].name == "#general-chat"

    # Empty query returns all blacklisted channels.
    all_matches = asyncio.run(
        cog.audit_blacklist_channel_id_autocomplete(interaction, "")
    )
    assert [c.value for c in all_matches] == ["700", "800"]
