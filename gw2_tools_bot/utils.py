"""Utility helpers for the GW2 Tools bot."""
from __future__ import annotations

from typing import Optional, Tuple

import discord

from . import constants


def resolve_profession(selection: str) -> Tuple[str, Optional[str]]:
    """Return the base profession and optional specialization for a selection."""

    if selection in constants.PROFESSIONS:
        return selection, None
    spec = constants.SPECIALIZATIONS.get(selection)
    if not spec:
        raise ValueError(f"Unknown class or specialization: {selection}")
    return spec.profession, spec.name


def build_class_display(profession: str, specialization: Optional[str]) -> str:
    if specialization:
        return f"{specialization} ({profession})"
    return profession


def build_embed(
    record,
    *,
    icon_attachment_name: str,
    color: int,
) -> discord.Embed:
    """Create a Discord embed describing the build."""

    embed = discord.Embed(
        title=record.name,
        description=record.description or "",
        color=color,
    )
    embed.set_author(
        name=build_class_display(record.profession, record.specialization),
        icon_url=f"attachment://{icon_attachment_name}",
    )
    embed.set_thumbnail(url=f"attachment://{icon_attachment_name}")
    embed.add_field(name="Chat Code", value=f"```{record.chat_code}```", inline=False)
    if record.url:
        embed.add_field(name="Reference", value=record.url, inline=False)
    embed.set_footer(text=record.to_embed_footer())
    embed.timestamp = discord.utils.utcnow()
    return embed


def get_icon_and_color(selection: str) -> Tuple[str, int]:
    """Determine the icon path and embed color for a class selection."""

    profession_name, specialization_name = resolve_profession(selection)
    profession = constants.PROFESSIONS[profession_name]
    if specialization_name:
        spec = constants.SPECIALIZATIONS[specialization_name]
        path = spec.icon_path(constants.PROFESSIONS)
    else:
        path = profession.icon_path
    return str(path), profession.color
