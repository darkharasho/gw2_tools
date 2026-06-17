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

    def _parse_gw3_html(self, html: str) -> List[GameNewsEntry]:
        soup = BeautifulSoup(html, "html.parser")
        entries: List[GameNewsEntry] = []
        for card in soup.select("article.news-article"):
            try:
                entry = self._parse_gw3_card(card)
            except Exception:  # pragma: no cover - defensive per-card guard
                LOGGER.debug("Failed to parse a GW3 news card", exc_info=True)
                continue
            if entry:
                entries.append(entry)
        return entries

    def _parse_gw3_card(self, card) -> Optional[GameNewsEntry]:
        slug: Optional[str] = None
        card_id = card.get("id") or ""
        if card_id.startswith("article-"):
            slug = card_id[len("article-"):].strip()

        anchor = card.find_parent("a")
        href = anchor.get("href") if anchor else None
        if not slug and href:
            slug = href.rstrip("/").split("/")[-1].strip()
        if not slug:
            return None

        heading = card.select_one("h2.title") or card.find("h2")
        title = heading.get_text(strip=True) if heading else ""
        if not title:
            return None

        if href:
            url = urljoin(GW3_NEWS_PAGE_URL, href)
        else:
            url = f"{GW3_BASE_URL}/en/news/{slug}"

        image = card.find("img")
        image_url = image.get("src") if image and image.get("src") else None

        return GameNewsEntry(
            source_key="gw3",
            entry_id=slug,
            title=title,
            url=url,
            image_url=image_url,
            published_at=None,
            summary=None,
        )

    def _first_image_from_entry(self, entry) -> Optional[str]:
        """Pull the first <img src> from the entry's content/summary HTML.

        The GW2 feed embeds images inline in content:encoded (no media tags),
        so _extract_entry_thumbnail returns None and we fall back to this.
        """
        html_candidates: List[str] = []
        contents = entry.get("content")
        if isinstance(contents, list):
            for item in contents:
                if isinstance(item, dict) and item.get("value"):
                    html_candidates.append(str(item["value"]))
        summary = entry.get("summary") or entry.get("description")
        if summary:
            html_candidates.append(str(summary))

        for html in html_candidates:
            soup = BeautifulSoup(html, "html.parser")
            img = soup.find("img")
            src = img.get("src") if img else None
            if not src:
                continue
            if src.startswith("//"):
                src = "https:" + src
            if src.startswith("http"):
                return src
        return None

    def _parse_gw2_feed(self, raw: str) -> List[GameNewsEntry]:
        parsed = feedparser.parse(raw)
        entries: List[GameNewsEntry] = []
        for entry in parsed.entries:
            entry_id = _entry_identifier(entry)
            link = entry.get("link")
            if not entry_id or not link:
                continue
            title = entry.get("title") or "Guild Wars 2 News"
            published = _convert_struct_time(entry.get("published_parsed"))
            published_at = published.isoformat() if published else None
            summary = _extract_entry_description(entry)
            image = _extract_entry_thumbnail(entry) or self._first_image_from_entry(entry)
            entries.append(
                GameNewsEntry(
                    source_key="gw2",
                    entry_id=entry_id,
                    title=str(title),
                    url=str(link),
                    image_url=image,
                    published_at=published_at,
                    summary=summary,
                )
            )
        return entries

    @tasks.loop(minutes=CHECK_INTERVAL_MINUTES)
    async def _poll_news(self) -> None:  # pragma: no cover - tested via unit tests
        pass


async def setup(bot: AxiToolsBot) -> None:
    await bot.add_cog(GameNewsCog(bot))
