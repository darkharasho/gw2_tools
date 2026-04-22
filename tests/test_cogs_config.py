import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import discord
from axitools.cogs.config import ConfigCog, ConfigView, StatusView
from axitools.config_status import ConfigStatus, StatusField
from axitools.storage import GuildConfig


@pytest.fixture
def mock_bot_config():
    bot = MagicMock()
    # Mock is_authorised to allow access
    bot.is_authorised.return_value = True
    bot.get_config.return_value = GuildConfig(moderator_role_ids=[])
    return bot


@pytest.mark.asyncio
async def test_config_command(mock_bot_config):
    cog = ConfigCog(mock_bot_config)
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.user = MagicMock(spec=discord.Member)
    interaction.response.send_message = AsyncMock()

    await cog.config_command.callback(cog, interaction)

    # Should send a message with a view
    assert interaction.response.send_message.called
    args, kwargs = interaction.response.send_message.call_args
    assert kwargs.get("view") is not None
    assert isinstance(kwargs.get("view"), ConfigView)


@pytest.mark.asyncio
async def test_config_command_unauthorized(mock_bot_config):
    cog = ConfigCog(mock_bot_config)
    mock_bot_config.is_authorised.return_value = False

    interaction = AsyncMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.user = MagicMock(spec=discord.Member)
    interaction.response.send_message = AsyncMock()

    await cog.config_command.callback(cog, interaction)

    # Should deny access
    assert interaction.response.send_message.called
    args, kwargs = interaction.response.send_message.call_args
    assert "permission" in args[0]


# ---------------------------------------------------------------------------
# Helpers for unit tests that don't need the full bot
# ---------------------------------------------------------------------------

def make_cog(config_overrides=None):
    bot = MagicMock()
    config = MagicMock()
    config.build_channel_id = None
    config.arcdps_channel_id = None
    config.update_notes_channel_id = None
    config.moderator_role_ids = []
    config.comp_schedules = []
    if config_overrides:
        for k, v in config_overrides.items():
            setattr(config, k, v)
    bot.get_config.return_value = config
    bot.get_cog.return_value = None  # no cogs in unit tests
    cog = ConfigCog.__new__(ConfigCog)
    cog.bot = bot
    return cog, config


def test_is_first_run_true_when_nothing_configured():
    cog, _ = make_cog()
    assert cog._is_first_run(guild_id=1) is True


def test_is_first_run_false_when_channel_set():
    cog, _ = make_cog({"build_channel_id": 12345})
    assert cog._is_first_run(guild_id=1) is False


def test_get_config_status_warn_when_no_mod_roles():
    cog, _ = make_cog()
    status = cog.get_config_status(guild_id=1)
    assert status.title == "Bot Configuration"
    assert any(f.state == "warn" for f in status.fields)


def test_get_config_status_ok_when_mod_roles_set():
    cog, _ = make_cog({"moderator_role_ids": [111, 222]})
    status = cog.get_config_status(guild_id=1)
    assert any(f.state == "ok" for f in status.fields)


def test_build_status_embed_returns_embed():
    cog, _ = make_cog()
    guild = MagicMock(spec=discord.Guild)
    guild.id = 1
    embed = cog._build_status_embed(guild)
    assert isinstance(embed, discord.Embed)
    assert embed.title == "AxiTools Configuration Status"
