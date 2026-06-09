
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import discord
from axitools.cogs.help import HelpCog
from axitools.bot import AxiToolsBot

@pytest.fixture
def mock_bot_help():
    bot = MagicMock(spec=AxiToolsBot)
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
    cmd_public.extras = {"public": True}
    cmd_public.parent = None

    cmd_private = MagicMock()
    cmd_private.qualified_name = "config"
    cmd_private.description = "Config stuff"
    cmd_private.extras = {}
    cmd_private.parent = None

    mock_bot_help.tree.get_commands.return_value = [cmd_public, cmd_private]
    
    await cog.help_command.callback(cog, interaction)
    
    # Verify we sent an embed
    assert interaction.response.send_message.called
    args, kwargs = interaction.response.send_message.call_args
    embed = kwargs.get('embed')
    assert embed is not None
    
    # Commands are listed in field values, grouped by category field names.
    body = "\n".join(f.value for f in embed.fields)
    # Verify public command is present
    assert "/help" in body
    # Verify private command is NOT present
    assert "/config" not in body

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
    cmd_private.extras = {"category": "Server Setup"}
    cmd_private.parent = None

    mock_bot_help.tree.get_commands.return_value = [cmd_private]

    await cog.help_command.callback(cog, interaction)

    args, kwargs = interaction.response.send_message.call_args
    embed = kwargs.get('embed')

    body = "\n".join(f.value for f in embed.fields)
    assert "/config" in body
    # Grouped under its category
    assert "Server Setup" in [f.name for f in embed.fields]


@pytest.mark.asyncio
async def test_help_uses_extras_for_public(mock_bot_help):
    cog = HelpCog(mock_bot_help)
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.user = MagicMock(spec=discord.Member)
    interaction.response.send_message = AsyncMock()
    mock_bot_help.is_authorised.return_value = False

    public_cmd = MagicMock()
    public_cmd.qualified_name = "apikey add"
    public_cmd.description = "Add a key"
    public_cmd.extras = {"public": True, "category": "GW2 Account"}
    public_cmd.parent = None

    gated_cmd = MagicMock()
    gated_cmd.qualified_name = "stream list"
    gated_cmd.description = "List"
    gated_cmd.extras = {}
    gated_cmd.parent = None

    mock_bot_help.tree.get_commands.return_value = [public_cmd, gated_cmd]
    await cog.help_command.callback(cog, interaction)

    _, kwargs = interaction.response.send_message.call_args
    embed = kwargs["embed"]
    body = "\n".join(f.value for f in embed.fields)
    assert "/apikey add" in body
    assert "/stream list" not in body
    assert "GW2 Account" in [f.name for f in embed.fields]
