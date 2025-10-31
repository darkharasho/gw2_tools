"""Module entry point for ``python -m gw2_tools_bot``."""
from __future__ import annotations

from .bot import run


def main() -> None:
    """Run the GW2 Tools bot."""

    run()


if __name__ == "__main__":
    main()
