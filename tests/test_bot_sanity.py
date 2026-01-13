
import pytest
from gw2_tools_bot.bot import GW2ToolsBot

def test_bot_class_exists():
    """Simple smoke test to ensure the bot class is importable."""
    assert GW2ToolsBot is not None
