from __future__ import annotations

import asyncio
import json
import logging
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import aiohttp
import discord

from ..branding import BRAND_COLOUR, brand_embed
from ..http_utils import read_response_text
from ..storage import normalise_guild_id

LOGGER = logging.getLogger(__name__)


class AccountsSharedMixin:
    """Shared presentation and GW2 API helpers for the account cogs.

    Cogs that mix this in must set ``self.bot`` and initialise the session and
    guild-detail caches via :meth:`_init_shared_state` in their ``__init__``.
    """

    def _init_shared_state(self) -> None:
        self._session: Optional[aiohttp.ClientSession] = None
        self._guild_detail_cache: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Presentation helpers
    # ------------------------------------------------------------------
    def _embed(
        self,
        *,
        title: str,
        description: Optional[str] = None,
        colour: discord.Colour = BRAND_COLOUR,
    ) -> discord.Embed:
        return brand_embed(title=title, description=description, colour=colour)

    @staticmethod
    def _format_list(items: Sequence[str], *, placeholder: str = "None") -> str:
        if not items:
            return placeholder
        return "\n".join(f"• {value}" for value in items)

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

    @staticmethod
    def _normalise_guild_id(guild_id: str) -> str:
        return normalise_guild_id(guild_id)

    async def _send_embed(
        self,
        interaction: discord.Interaction,
        *,
        title: str,
        description: str,
        colour: discord.Colour = BRAND_COLOUR,
        ephemeral: bool = True,
        use_followup: bool = False,
    ) -> None:
        embed = self._embed(title=title, description=description, colour=colour)
        if use_followup or interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------
    async def _get_session(self) -> aiohttp.ClientSession:
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Accept-Encoding": "gzip, deflate, br"},
                auto_decompress=False,
            )
        return self._session

    async def _close_session(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _fetch_json(
        self,
        url: str,
        *,
        api_key: Optional[str] = None,
        params: Optional[Dict[str, str]] = None,
    ) -> Dict | List:
        session = await self._get_session()
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        last_exc: Optional[Exception] = None
        for attempt in range(3):
            try:
                async with session.get(
                    url,
                    headers=headers,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    text = await read_response_text(response)
            except aiohttp.ClientError as exc:
                raise ValueError(f"Failed to reach the Guild Wars 2 API: {exc}") from exc

            if response.status == 429:
                last_exc = ValueError(f"Guild Wars 2 API returned 429: {text[:200]}")
                await asyncio.sleep(2 * (attempt + 1))
                continue

            if response.status != 200:
                raise ValueError(
                    f"Guild Wars 2 API returned {response.status}: {text[:200]}"
                )

            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:  # pragma: no cover - defensive
                raise ValueError("Unexpected response format from the Guild Wars 2 API") from exc
        raise last_exc  # type: ignore[misc]

    async def _fetch_guild_details(
        self, guild_ids: Iterable[str], *, api_key: Optional[str] = None
    ) -> Dict[str, str]:
        details: Dict[str, str] = {}
        cache_payload: Dict[str, Tuple[str, Optional[str]]] = {}
        for guild_id in guild_ids:
            if not guild_id:
                continue
            cached = self._guild_detail_cache.get(guild_id)
            if cached is not None:
                details[guild_id] = cached
                continue
            try:
                payload = await self._fetch_json(
                    f"https://api.guildwars2.com/v2/guild/{guild_id}", api_key=api_key
                )
            except ValueError:
                continue
            name = payload.get("name")
            tag = payload.get("tag")
            if isinstance(name, str) and isinstance(tag, str):
                label = f"{name} [{tag}]"
                details[guild_id] = label
                self._guild_detail_cache[guild_id] = label
                cache_payload[guild_id] = (name, tag)
            elif isinstance(name, str):
                details[guild_id] = name
                self._guild_detail_cache[guild_id] = name
                cache_payload[guild_id] = (name, None)
        if cache_payload:
            self.bot.storage.upsert_guild_details(cache_payload)
        return details

    async def _cached_guild_labels(self, guild_ids: Sequence[str]) -> Dict[str, str]:
        if not guild_ids:
            return {}
        try:
            return await self._fetch_guild_details(guild_ids)
        except ValueError:
            LOGGER.warning("Guild lookup failed while fetching live labels", exc_info=True)
            return {}
