"""Shared text formatting utilities for Discord embeds."""
from __future__ import annotations

import re
from html import unescape

from markdownify import markdownify as html_to_markdown

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_BULLET_RE = re.compile(r"^(\s*)[*+]\s+")


def clean_html(text: str, *, max_length: int | None = None) -> str:
    """Strip HTML tags, decode entities, and optionally truncate."""
    text = _HTML_TAG_RE.sub("", text)
    text = unescape(text).strip()
    if max_length is not None and len(text) > max_length:
        text = text[: max_length - 1].rstrip() + "…"
    return text


def clean_markdown(text: str) -> str:
    """Collapse excessive blank lines and normalise bullet markers."""
    lines = [line.rstrip() for line in text.splitlines()]
    cleaned: list[str] = []
    blank = False
    for line in lines:
        if line.strip():
            cleaned.append(line)
            blank = False
        else:
            if not blank:
                cleaned.append("")
            blank = True
    result = "\n".join(cleaned).strip()
    return ensure_bullet_prefix(result) if result else result


def ensure_bullet_prefix(text: str) -> str:
    """Normalise `*` and `+` list markers to `-`."""
    adjusted: list[str] = []
    for line in text.split("\n"):
        match = _BULLET_RE.match(line)
        if match:
            indent = match.group(1)
            remainder = line[match.end() :]
            adjusted.append(f"{indent}- {remainder}")
        else:
            adjusted.append(line)
    return "\n".join(adjusted)


def html_to_discord_markdown(html: str) -> str:
    """Convert HTML to Discord-flavoured markdown and clean up."""
    markdown = html_to_markdown(
        html, heading_style="ATX", bullets="-*+", strip=["img"]
    )
    return clean_markdown(markdown)


def truncate_embed_field(text: str, limit: int = 1024) -> str:
    """Truncate to Discord embed field character limit with ellipsis."""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
