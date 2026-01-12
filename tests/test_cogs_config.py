
import pytest
from unittest.mock import AsyncMock, MagicMock
import discord
from gw2_tools_bot.cogs.config import ConfigCog, ConfigView
from gw2_tools_bot.storage import GuildConfig

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
