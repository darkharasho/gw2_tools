"""Module entry point for ``python -m axitools``."""
from __future__ import annotations

from .bot import run


def main() -> None:
    """Run the AxiTools bot."""

    run()


if __name__ == "__main__":
    main()
