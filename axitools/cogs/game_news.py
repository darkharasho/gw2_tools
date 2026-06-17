"""Multi-source game news feed (Guild Wars 2 + Guild Wars 3)."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import urljoin

import discord
import feedparser
import requests
from bs4 import BeautifulSoup
from discord.ext import commands, tasks

from ..bot import AxiToolsBot
from ..branding import BRAND_COLOUR
from ..config_status import ConfigStatus, StatusField
from ..storage import GameNewsStatus
from .rss import (
    _convert_struct_time,
    _entry_identifier,
    _extract_entry_description,
    _extract_entry_thumbnail,
)

LOGGER = logging.getLogger(__name__)

GW2_FEED_URL = "https://www.guildwars2.com/en/feed/"
GW3_NEWS_PAGE_URL = "https://www.guildwars3.com/en/news/"
GW3_BASE_URL = "https://www.guildwars3.com"
ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"

REQUEST_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}


@dataclass
class GameNewsEntry:
    """A news article from any source, normalized for posting."""

    source_key: str
    entry_id: str
    title: str
    url: str
    image_url: Optional[str] = None
    published_at: Optional[str] = None
    summary: Optional[str] = None


@dataclass(frozen=True)
class NewsSource:
    """Describes one news source: its dedup key, footer label, and logo asset."""

    key: str
    label: str
    logo_asset: str


class GameNewsCog(commands.Cog):
    """Poll GW2 (RSS) and GW3 (scrape) news and post new articles."""

    CHECK_INTERVAL_MINUTES = 15

    SOURCES: List[NewsSource] = [
        NewsSource(key="gw2", label="Guild Wars 2 – News", logo_asset="gw2_logo.png"),
        NewsSource(key="gw3", label="Guild Wars 3 – News", logo_asset="gw3_logo.png"),
    ]

    def __init__(self, bot: AxiToolsBot) -> None:
        self.bot = bot
        self._session = requests.Session()
        self._session.headers.update(REQUEST_HEADERS)
        self._poll_news.start()

    def cog_unload(self) -> None:  # pragma: no cover - discord.py lifecycle
        self._poll_news.cancel()
        self._session.close()

    # ---- shared helpers ---------------------------------------------------

    def _resolve_new_entries(
        self,
        entries: Sequence[GameNewsEntry],
        last_entry_id: Optional[str],
        last_published_at: Optional[str],
    ) -> "Tuple[List[GameNewsEntry], bool]":
        """Return ``(new_entries_oldest_first, boundary_found)``.

        ``boundary_found`` is ``False`` when the recorded entry can no longer be
        located (it scrolled off). The caller re-anchors instead of re-posting
        everything. Mirrors update_notes; the timestamp branch is skipped for
        sources without dates (GW3 passes ``None``).
        """
        if not entries:
            return [], True

        collected: List[GameNewsEntry] = []
        cutoff = self._parse_timestamp(last_published_at)
        boundary_found = not last_entry_id
        for entry in entries:
            if last_entry_id and entry.entry_id == last_entry_id:
                boundary_found = True
                break
            entry_timestamp = self._parse_timestamp(entry.published_at)
            if cutoff and entry_timestamp and entry_timestamp <= cutoff:
                boundary_found = True
                break
            collected.append(entry)

        return list(reversed(collected)), boundary_found

    def _parse_timestamp(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        candidate = value
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            LOGGER.debug("Unable to parse game news timestamp: %s", value)
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _truncate(self, value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        return value[: limit - 1].rstrip() + "…"

    @tasks.loop(minutes=CHECK_INTERVAL_MINUTES)
    async def _poll_news(self) -> None:  # pragma: no cover - tested via unit tests
        pass


async def setup(bot: AxiToolsBot) -> None:
    await bot.add_cog(GameNewsCog(bot))
