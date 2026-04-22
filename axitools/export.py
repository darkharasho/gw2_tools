"""CSV export utilities for member data."""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass

import discord


@dataclass
class MemberExportRow:
    member_id: int
    member_display: str
    account_names: list[str]
    guild_ids: list[str]
    guild_names: list[str]
    roles: list[str]
    characters: list[str]


def members_to_csv(rows: list[MemberExportRow]) -> discord.File:
    """Build a UTF-8 CSV discord.File from member export rows."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "Discord ID", "Discord Name", "Account Name",
        "Guild IDs", "Guild Names", "Roles", "Characters",
    ])
    for row in rows:
        writer.writerow([
            row.member_id,
            row.member_display,
            "; ".join(row.account_names),
            "; ".join(row.guild_ids),
            "; ".join(row.guild_names),
            "; ".join(row.roles),
            "; ".join(row.characters),
        ])
    buffer.seek(0)
    return discord.File(
        fp=io.BytesIO(buffer.getvalue().encode("utf-8")),
        filename="select_query.csv",
    )
