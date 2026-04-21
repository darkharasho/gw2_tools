
import pytest
from axitools.bot import AxiToolsBot

def test_bot_class_exists():
    """Simple smoke test to ensure the bot class is importable."""
    assert AxiToolsBot is not None
