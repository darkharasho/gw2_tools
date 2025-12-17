from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from io import StringIO
from typing import List, Sequence, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from ..bot import GW2ToolsBot

LOGGER = logging.getLogger(__name__)


class DatabaseCog(commands.Cog):
    """Run read-only SQL against guild-scoped bot data."""

    db = app_commands.Group(name="db", description="Inspect stored data for this Discord server.")

    MAX_ROWS = 500

    def __init__(self, bot: GW2ToolsBot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _format_table(
        headers: Sequence[str],
        rows: Sequence[Sequence[str]],
        *,
        placeholder: str = "None",
        code_block: bool = True,
    ) -> str:
        if not rows:
            return placeholder

        widths = [len(header) for header in headers]
        for row in rows:
            for idx, cell in enumerate(row):
                widths[idx] = max(widths[idx], len(cell))

        def _format_row(row: Sequence[str]) -> str:
            padded_cells = [f" {cell.ljust(widths[idx])} " for idx, cell in enumerate(row)]
            return "|" + "|".join(padded_cells) + "|"

        def _divider(char: str) -> str:
            segments = (char * (width + 2) for width in widths)
            return "+" + "+".join(segments) + "+"

        header_divider = _divider("=")
        row_divider = _divider("-")

        lines = [header_divider, _format_row(headers), header_divider]
        lines.extend(_format_row(row) for row in rows)
        lines.append(row_divider)
        table = "\n".join(lines)
        return f"```\n{table}\n```" if code_block else table

    def _copy_table(
        self,
        source: sqlite3.Connection,
        target: sqlite3.Connection,
        *,
        name: str,
        where: str,
        params: Sequence[object],
    ) -> Tuple[List[sqlite3.Row], List[str]]:
        sql_row = source.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone()
        if not sql_row or not sql_row["sql"]:
            return [], []

        target.execute(sql_row["sql"])
        columns = [info["name"] for info in source.execute(f"PRAGMA table_info({name})")]
        if not columns:
            return [], []

        column_list = ", ".join(columns)
        rows = source.execute(
            f"SELECT {column_list} FROM {name} WHERE {where}", params
        ).fetchall()

        placeholders = ",".join("?" for _ in columns)
        insert_sql = f"INSERT INTO {name} ({column_list}) VALUES ({placeholders})"
        target.executemany(
            insert_sql, [[row[column] for column in columns] for row in rows]
        )
        return rows, columns

    def _scoped_connection(self, guild_id: int) -> sqlite3.Connection:
        source_path = self.bot.storage.api_key_store.path
        scoped = sqlite3.connect(":memory:")
        scoped.row_factory = sqlite3.Row
        scoped.execute("PRAGMA foreign_keys = ON")
        scoped.execute("PRAGMA trusted_schema = OFF")

        allowed_tables = set()
        guild_rows: List[sqlite3.Row] = []
        guild_columns: List[str] = []

        if source_path.exists():
            with sqlite3.connect(source_path) as source:
                source.row_factory = sqlite3.Row
                guild_rows, guild_columns = self._copy_table(
                    source,
                    scoped,
                    name="api_keys",
                    where="guild_id = ?",
                    params=[guild_id],
                )
                allowed_tables.update({"api_keys"} if guild_columns else set())

                api_key_ids = [row["id"] for row in guild_rows]
                if api_key_ids:
                    placeholders = ",".join("?" for _ in api_key_ids)
                    key_rows, key_columns = self._copy_table(
                        source,
                        scoped,
                        name="api_key_guilds",
                        where=f"api_key_id IN ({placeholders})",
                        params=api_key_ids,
                    )
                    if key_columns:
                        allowed_tables.add("api_key_guilds")

                    guild_ids = set()
                    for row in guild_rows:
                        try:
                            guild_ids.update(json.loads(row["guild_ids"]))
                        except Exception:
                            continue
                    guild_ids.update(row["guild_id"] for row in key_rows)

                    if guild_ids:
                        placeholders = ",".join("?" for _ in guild_ids)
                        details_rows, detail_columns = self._copy_table(
                            source,
                            scoped,
                            name="guild_details",
                            where=f"guild_id IN ({placeholders})",
                            params=list(guild_ids),
                        )
                        if detail_columns:
                            allowed_tables.add("guild_details")

        if not allowed_tables:
            scoped.execute(
                "CREATE TABLE IF NOT EXISTS api_keys (placeholder TEXT)"  # keep schema visibility
            )
            allowed_tables.add("api_keys")

        def _authorizer(
            action: int, param1: str | None, param2: str | None, db_name: str | None, trigger: str | None
        ) -> int:
            if action in {
                sqlite3.SQLITE_ATTACH,
                sqlite3.SQLITE_DETACH,
                sqlite3.SQLITE_ALTER_TABLE,
                sqlite3.SQLITE_CREATE_INDEX,
                sqlite3.SQLITE_CREATE_TABLE,
                sqlite3.SQLITE_CREATE_TEMP_INDEX,
                sqlite3.SQLITE_CREATE_TEMP_TABLE,
                sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
                sqlite3.SQLITE_CREATE_TEMP_VIEW,
                sqlite3.SQLITE_CREATE_TRIGGER,
                sqlite3.SQLITE_CREATE_VIEW,
                sqlite3.SQLITE_DELETE,
                sqlite3.SQLITE_DROP_INDEX,
                sqlite3.SQLITE_DROP_TABLE,
                sqlite3.SQLITE_DROP_TEMP_INDEX,
                sqlite3.SQLITE_DROP_TEMP_TABLE,
                sqlite3.SQLITE_DROP_TEMP_TRIGGER,
                sqlite3.SQLITE_DROP_TEMP_VIEW,
                sqlite3.SQLITE_DROP_TRIGGER,
                sqlite3.SQLITE_DROP_VIEW,
                sqlite3.SQLITE_INSERT,
                sqlite3.SQLITE_UPDATE,
            }:
                return sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_PRAGMA:
                allowed_pragmas = {
                    "table_info",
                    "index_list",
                    "foreign_key_list",
                    "table_list",
                }
                if param1 and param1.lower() not in allowed_pragmas:
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_READ:
                if param1 is None:
                    return sqlite3.SQLITE_OK
                if param1 not in allowed_tables and param1 not in {
                    "sqlite_schema",
                    "sqlite_master",
                    "sqlite_temp_schema",
                }:
                    # SQLite exposes PRAGMA results via virtual tables prefixed with
                    # "pragma_" (e.g. pragma_table_info). Allow those as part of
                    # read-only inspection while continuing to block other tables.
                    if not param1.startswith("pragma_"):
                        return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        scoped.set_authorizer(_authorizer)
        scoped.execute("PRAGMA query_only = ON")
        return scoped

    async def _execute_query(
        self, guild_id: int, sql: str
    ) -> Tuple[List[str], List[List[str]], bool]:
        def _run() -> Tuple[List[str], List[List[str]], bool]:
            with self._scoped_connection(guild_id) as connection:
                cursor = connection.execute(sql)
                description = cursor.description or []
                headers = [col[0] or f"column_{idx + 1}" for idx, col in enumerate(description)]
                raw_rows = cursor.fetchmany(self.MAX_ROWS + 1)

            truncated = len(raw_rows) > self.MAX_ROWS
            rows = raw_rows[: self.MAX_ROWS]
            display_rows: List[List[str]] = []
            for row in rows:
                display_rows.append(
                    ["—" if value is None else str(value) for value in row]
                )
            return headers, display_rows, truncated

        return await asyncio.to_thread(_run)

    async def _describe_schema(self, guild_id: int) -> List[Tuple[str, List[List[str]]]]:
        def _run() -> List[Tuple[str, List[List[str]]]]:
            with self._scoped_connection(guild_id) as connection:
                tables = [
                    row["name"]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                    )
                ]
                schema: List[Tuple[str, List[List[str]]]] = []
                for table in tables:
                    columns = connection.execute(f"PRAGMA table_info({table})").fetchall()
                    if not columns:
                        continue
                    column_rows = [
                        [
                            str(col["name"]),
                            str(col["type"] or ""),
                            "yes" if col["notnull"] else "no",
                            str(col["dflt_value"]) if col["dflt_value"] is not None else "—",
                            "yes" if col["pk"] else "no",
                        ]
                        for col in columns
                    ]
                    schema.append((table, column_rows))
                return schema

        return await asyncio.to_thread(_run)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------
    @db.command(name="query", description="Run a read-only SQL query for this server's data.")
    @app_commands.describe(sql="SQL query to execute (read-only; scoped to this server)")
    async def query(self, interaction: discord.Interaction, sql: str) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return
        if not sql.strip():
            await interaction.response.send_message("Please provide a SQL query to run.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            headers, rows, truncated = await self._execute_query(interaction.guild.id, sql)
        except sqlite3.Error as exc:
            await interaction.followup.send(f"Query failed: {exc}", ephemeral=True)
            return

        if not headers and not rows:
            await interaction.followup.send("Query executed but returned no data.", ephemeral=True)
            return

        table = self._format_table(headers or ["Result"], rows or [["No rows returned"]], code_block=False)
        buffer = StringIO(table)
        files = [discord.File(fp=buffer, filename="db_query.txt")]

        summary = [f"Returned {len(rows)} row(s) from a guild-scoped snapshot."]
        if truncated:
            summary.append(f"Limited to the first {self.MAX_ROWS} rows.")

        await interaction.followup.send("\n".join(summary), files=files, ephemeral=True)

    @db.command(name="schema", description="Show the available tables and columns for this server.")
    async def schema(self, interaction: discord.Interaction) -> None:
        if not await self.bot.ensure_authorised(interaction):
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            schema = await self._describe_schema(interaction.guild.id)
        except sqlite3.Error as exc:
            await interaction.followup.send(f"Unable to read schema: {exc}", ephemeral=True)
            return

        if not schema:
            await interaction.followup.send("No tables are available for this server yet.", ephemeral=True)
            return

        sections: List[str] = []
        for table, rows in schema:
            sections.append(f"Table: {table}")
            sections.append(
                self._format_table(
                    ["Column", "Type", "Not null", "Default", "Primary key"],
                    rows,
                    code_block=False,
                )
            )
            sections.append("")

        buffer = StringIO("\n".join(sections).strip())
        files = [discord.File(fp=buffer, filename="db_schema.txt")]

        await interaction.followup.send("Attached the schema for this server's data.", files=files, ephemeral=True)


async def setup(bot: GW2ToolsBot) -> None:
    await bot.add_cog(DatabaseCog(bot))
