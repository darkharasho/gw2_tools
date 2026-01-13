
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import discord
from gw2_tools_bot.cogs.help import HelpCog
from gw2_tools_bot.bot import GW2ToolsBot

@pytest.fixture
def mock_bot_help():
    bot = MagicMock(spec=GW2ToolsBot)
    bot.tree = MagicMock()
    # Mocking get_commands
    bot.tree.get_commands.return_value = []
    return bot

@pytest.mark.asyncio
async def test_help_command_public(mock_bot_help):
    cog = HelpCog(mock_bot_help)
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.user = MagicMock(spec=discord.Member)
    interaction.response.send_message = AsyncMock()
    
    # User not authorised
    mock_bot_help.is_authorised.return_value = False
    
    # Setup some commands
    cmd_public = MagicMock()
    cmd_public.qualified_name = "help"
    cmd_public.description = "Show help"
    
    cmd_private = MagicMock()
    cmd_private.qualified_name = "config"
    cmd_private.description = "Config stuff"
    
    mock_bot_help.tree.get_commands.return_value = [cmd_public, cmd_private]
    
    await cog.help_command.callback(cog, interaction)
    
    # Verify we sent an embed
    assert interaction.response.send_message.called
    args, kwargs = interaction.response.send_message.call_args
    embed = kwargs.get('embed')
    assert embed is not None
    
    # Verify public command is present
    field_names = [f.name for f in embed.fields]
    assert "/help" in field_names
    # Verify private command is NOT present
    assert "/config" not in field_names

@pytest.mark.asyncio
async def test_help_command_authorised(mock_bot_help):
    cog = HelpCog(mock_bot_help)
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.user = MagicMock(spec=discord.Member)
    interaction.response.send_message = AsyncMock()
    
    # User authorised
    mock_bot_help.is_authorised.return_value = True
    
    cmd_private = MagicMock()
    cmd_private.qualified_name = "config"
    cmd_private.description = "Config stuff"
    
    mock_bot_help.tree.get_commands.return_value = [cmd_private]
    
    await cog.help_command.callback(cog, interaction)
    
    args, kwargs = interaction.response.send_message.call_args
    embed = kwargs.get('embed')
    
    field_names = [f.name for f in embed.fields]
    assert "/config" in field_names
