
import pytest
from unittest.mock import MagicMock
from gw2_tools_bot.bot import GW2ToolsBot
from gw2_tools_bot.storage import GuildConfig

@pytest.fixture
def mock_bot():
    bot = MagicMock(spec=GW2ToolsBot)
    # We want to test the actual is_authorised method, so we assign it back to the mock
    # or better, use a real instance with mocked storage.
    # However, inheriting from commands.Bot makes instantiation heavy.
    # Let's mock the storage part and call the method as a static/unbound one or patch it.
    
    # Actually, is_authorised is an instance method that calls self.get_config.
    # We can just mock get_config on the instance.
    bot.is_authorised = GW2ToolsBot.is_authorised.__get__(bot, GW2ToolsBot)
    return bot

def test_is_authorised_admin(mock_bot):
    guild = MagicMock()
    guild.id = 123
    member = MagicMock()
    permissions = MagicMock()
    permissions.administrator = True
    
    # Config shouldn't matter if admin
    mock_bot.get_config.return_value = GuildConfig(moderator_role_ids=[])
    
    auth = mock_bot.is_authorised(guild, member, permissions=permissions)
    assert auth is True

def test_is_authorised_no_mod_roles(mock_bot):
    guild = MagicMock()
    guild.id = 123
    member = MagicMock()
    permissions = MagicMock()
    permissions.administrator = False
    
    mock_bot.get_config.return_value = GuildConfig(moderator_role_ids=[])
    
    auth = mock_bot.is_authorised(guild, member, permissions=permissions)
    assert auth is False

def test_is_authorised_with_role(mock_bot):
    guild = MagicMock()
    guild.id = 123
    member = MagicMock()
    permissions = MagicMock()
    permissions.administrator = False
    
    role1 = MagicMock()
    role1.id = 101
    member.roles = [role1]
    
    mock_bot.get_config.return_value = GuildConfig(moderator_role_ids=[101, 102])
    
    auth = mock_bot.is_authorised(guild, member, permissions=permissions)
    assert auth is True

def test_is_authorised_without_role(mock_bot):
    guild = MagicMock()
    guild.id = 123
    member = MagicMock()
    permissions = MagicMock()
    permissions.administrator = False
    
    role1 = MagicMock()
    role1.id = 999
    member.roles = [role1]
    
    mock_bot.get_config.return_value = GuildConfig(moderator_role_ids=[101, 102])
    
    auth = mock_bot.is_authorised(guild, member, permissions=permissions)
    assert auth is False
