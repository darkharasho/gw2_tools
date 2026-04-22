"""Shared dataclasses for per-cog configuration status reporting."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class StatusField:
    label: str
    value: str
    state: Literal["ok", "warn", "missing"]


@dataclass
class ConfigStatus:
    title: str
    fields: list[StatusField]
    setup_command: str | None = None
