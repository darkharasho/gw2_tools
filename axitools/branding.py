"""Brand constants for Guild Wars 2 Tools embeds and styling."""
from __future__ import annotations

from typing import Optional

import discord

# Soft orange derived from the app icon.
BRAND_COLOUR = discord.Colour.from_rgb(153, 93, 37)


def brand_embed(
    *,
    title: str,
    description: Optional[str] = None,
    colour: discord.Colour = BRAND_COLOUR,
) -> discord.Embed:
    """Build an embed with the standard AxiTools branding footer."""
    embed = discord.Embed(title=title, description=description or "", colour=colour)
    embed.set_footer(text="Guild Wars 2 Tools")
    return embed
