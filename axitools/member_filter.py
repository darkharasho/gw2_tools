"""Core filtering dataclasses and predicate logic for member searches."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Tuple

import discord


@dataclass
class FilterSet:
    guilds: List[str]
    roles: List[discord.Role]
    accounts: List[str]
    character_keys: List[str]
    character_labels: List[str]
    discord_members: List[discord.Member]
    filters: List[Tuple[str, str]]

    @property
    def character_provided(self) -> bool:
        return bool(self.character_keys)


@dataclass(frozen=True)
class BlanketCondition:
    field: str
    operator: str
    value: str


def blanket_condition_matches(
    condition: BlanketCondition,
    row: dict[str, Any],
) -> bool:
    """Return True if `row` satisfies the given condition."""
    raw = row.get(condition.field)
    if raw is None:
        return False
    values = raw if isinstance(raw, list) else [raw]
    normalised = [str(v).casefold() for v in values]
    target = condition.value.casefold()
    if condition.operator == "==":
        return target in normalised
    if condition.operator == "!=":
        return target not in normalised
    if condition.operator == "~=":
        return any(target in v for v in normalised)
    if condition.operator == "!~=":
        return all(target not in v for v in normalised)
    return False
