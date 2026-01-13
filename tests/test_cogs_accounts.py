
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import discord
from gw2_tools_bot.cogs.accounts import AccountsCog, ApiKeyRecord
from gw2_tools_bot.storage import utcnow

@pytest.fixture
def mock_bot_accounts():
    bot = MagicMock()
    return bot

@pytest.mark.asyncio
async def test_accounts_init(mock_bot_accounts):
    cog = AccountsCog(mock_bot_accounts)
    assert cog is not None
    # We can add more specific layout/logic tests if we mock the fetching and interaction flow,
    # but `AccountsCog` is complex heavily relying on external API calls which are mocked in generic fixtures.
    # For now, ensure it instantiates and basic command structure exists.

    # Check for commands availability if we could access app_commands structure easily
    # or just trust the class definition is valid.

@pytest.mark.asyncio
async def test_accounts_strip_emoji():
    # Helper method test
    # AccountsCog._strip_emoji removes emoji but might leave spaces depending on impl
    # "Hello 😃" -> "Hello "
    assert AccountsCog._strip_emoji("Hello 😃").strip() == "Hello"
    assert AccountsCog._strip_emoji("Guild [TAG]") == "Guild [TAG]"

@pytest.mark.asyncio
async def test_refresh_member_cache_syncs_roles(mock_bot_accounts):
    # Test that _refresh_member_cache calls _sync_roles for successfully refreshed users
    cog = AccountsCog(mock_bot_accounts)
    
    # Mock data
    guild_id = 123
    user_id = 456
    record = ApiKeyRecord(
        name="TestKey",
        key="test-key",
        account_name="Test.1234",
        permissions=["account", "guilds"],
        guild_ids=["GUID-1"],
        guild_labels={},
        characters=[],
        created_at=utcnow(),
        updated_at=utcnow()
    )
    
    # Mock bot.storage.all_api_keys
    mock_bot_accounts.storage.all_api_keys.return_value = [(guild_id, user_id, record)]
    
    # Mock bot.get_guild and guild.get_member
    mock_guild = MagicMock()
    mock_member = MagicMock()
    mock_bot_accounts.get_guild.return_value = mock_guild
    mock_guild.get_member.return_value = mock_member
    
    # Mock _validate_api_key
    mock_validate = AsyncMock(return_value=(
        ["account", "guilds"], # permissions
        ["GUID-1"], # guild_ids
        {}, # guild_details
        "Test.1234", # account_name
        [], # missing
        [] # characters
    ))
    cog._validate_api_key = mock_validate
    
    # Mock _sync_roles
    mock_sync = AsyncMock()
    cog._sync_roles = mock_sync
    
    await cog._refresh_member_cache()
    
    # Verification
    # 1. validate was called (refresh happened)
    mock_validate.assert_awaited_once()
    # 2. upsert was called (storage updated)
    mock_bot_accounts.storage.upsert_api_key.assert_called_once()
    # 3. _sync_roles was called with the correct guild and member
    mock_sync.assert_awaited_once_with(mock_guild, mock_member)
