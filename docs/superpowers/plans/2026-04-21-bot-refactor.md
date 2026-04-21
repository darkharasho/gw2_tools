# AxiTools Bot Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce code duplication, decompose the `select.py` monolith, and add a unified `/status` command for admin configuration discovery.

**Architecture:** Shared text formatting moves to `axitools/rendering.py`. The `cogs/select.py` monolith splits into three pure-Python modules (`member_filter`, `query_schema`, `export`) with a thin cog delegating to them. Each cog implements `get_config_status()` using a `ConfigStatus` dataclass from `axitools/config_status.py`. The `config` cog aggregates these into a `/status` command.

**Tech Stack:** Python 3.10+, discord.py 2.3+, pytest, markdownify, BeautifulSoup4

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `axitools/rendering.py` | Create | Shared HTML/markdown formatting utilities |
| `axitools/config_status.py` | Create | `ConfigStatus` + `StatusField` dataclasses |
| `axitools/member_filter.py` | Create | `FilterSet`, `BlanketCondition`, `blanket_condition_matches` |
| `axitools/query_schema.py` | Create | `parse_blanket_query`, AI schema/response helpers |
| `axitools/export.py` | Create | `MemberExportRow`, `members_to_csv` |
| `axitools/storage.py` | Modify | Add `StorageManager.count_api_keys()` |
| `axitools/cogs/select.py` | Refactor | Thin cog — delegates to new modules |
| `axitools/cogs/config.py` | Extend | Add `/status` command + `StatusView` |
| `axitools/cogs/rss.py` | Update | Use `rendering.py`; add `get_config_status()` |
| `axitools/cogs/update_notes.py` | Update | Use `rendering.py`; add `get_config_status()` |
| `axitools/cogs/builds.py` | Update | Add `get_config_status()` |
| `axitools/cogs/arcdps.py` | Update | Add `get_config_status()` |
| `axitools/cogs/comps.py` | Update | Add `get_config_status()` |
| `axitools/cogs/accounts.py` | Update | Add `get_config_status()` |
| `axitools/cogs/audit.py` | Update | Add `get_config_status()` |
| `tests/test_rendering.py` | Create | Tests for `rendering.py` |
| `tests/test_config_status.py` | Create | Tests for `config_status.py` |
| `tests/test_export.py` | Create | Tests for `export.py` |
| `tests/test_member_filter.py` | Create | Tests for `member_filter.py` |
| `tests/test_query_schema.py` | Create | Tests for `query_schema.py` |
| `tests/test_cogs_rss.py` | Update | Import `_extract_entry_description` still works |
| `tests/test_cogs_select.py` | Update | Update imports to new modules |
| `tests/test_cogs_config.py` | Update | Add `/status` command tests |

---

## Task 1: Create `axitools/rendering.py`

**Files:**
- Create: `axitools/rendering.py`
- Create: `tests/test_rendering.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_rendering.py
from axitools.rendering import (
    clean_html,
    clean_markdown,
    ensure_bullet_prefix,
    html_to_discord_markdown,
    truncate_embed_field,
)


def test_clean_html_strips_tags():
    assert clean_html("<b>hello</b>") == "hello"


def test_clean_html_decodes_entities():
    assert clean_html("&amp; &lt;b&gt;") == "& <b>"


def test_clean_html_truncates():
    long_text = "a" * 500
    result = clean_html(f"<p>{long_text}</p>", max_length=10)
    assert len(result) <= 10
    assert result.endswith("…")


def test_clean_html_no_truncation_when_within_limit():
    assert clean_html("<p>hello</p>", max_length=100) == "hello"


def test_clean_markdown_collapses_blank_lines():
    text = "line one\n\n\n\nline two"
    result = clean_markdown(text)
    assert result == "line one\n\nline two"


def test_clean_markdown_strips_trailing_whitespace():
    result = clean_markdown("hello   \nworld")
    assert result == "hello\nworld"


def test_ensure_bullet_prefix_normalises_asterisk():
    assert ensure_bullet_prefix("* item") == "- item"


def test_ensure_bullet_prefix_normalises_plus():
    assert ensure_bullet_prefix("+ item") == "- item"


def test_ensure_bullet_prefix_preserves_existing_dash():
    assert ensure_bullet_prefix("- item") == "- item"


def test_ensure_bullet_prefix_preserves_indent():
    assert ensure_bullet_prefix("  * nested") == "  - nested"


def test_html_to_discord_markdown_converts_bold():
    result = html_to_discord_markdown("<strong>hello</strong>")
    assert "**hello**" in result


def test_html_to_discord_markdown_converts_list():
    result = html_to_discord_markdown("<ul><li>item one</li><li>item two</li></ul>")
    assert "- item one" in result
    assert "- item two" in result


def test_truncate_embed_field_within_limit():
    assert truncate_embed_field("hello", 10) == "hello"


def test_truncate_embed_field_over_limit():
    text = "a" * 1025
    result = truncate_embed_field(text)
    assert len(result) <= 1024
    assert result.endswith("…")


def test_truncate_embed_field_default_limit():
    text = "a" * 1024
    assert truncate_embed_field(text) == text
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_rendering.py -v
```

Expected: `ModuleNotFoundError: No module named 'axitools.rendering'`

- [ ] **Step 3: Implement `axitools/rendering.py`**

```python
"""Shared text formatting utilities for Discord embeds."""
from __future__ import annotations

import re
from html import unescape
from typing import List

from markdownify import markdownify as html_to_markdown

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_BULLET_RE = re.compile(r"^(\s*)[\*\+]\s+")


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
    cleaned: List[str] = []
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
    adjusted: List[str] = []
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_rendering.py -v
```

Expected: all 16 tests PASS

- [ ] **Step 5: Commit**

```bash
git add axitools/rendering.py tests/test_rendering.py
git commit -m "feat: add shared rendering utilities"
```

---

## Task 2: Update `cogs/rss.py` to use `rendering.py`

**Files:**
- Modify: `axitools/cogs/rss.py`

- [ ] **Step 1: Verify the existing RSS test passes before touching anything**

```bash
python -m pytest tests/test_cogs_rss.py -v
```

Expected: 2 tests PASS

- [ ] **Step 2: Update imports in `axitools/cogs/rss.py`**

Add this import near the top of the file (after the existing imports):

```python
from ..rendering import clean_html, html_to_discord_markdown, truncate_embed_field
```

- [ ] **Step 3: Remove the four local functions and the `SUMMARY_REGEX` constant**

Delete these from `rss.py`:
- `SUMMARY_REGEX = re.compile(r"<[^>]+>")` (line 27)
- `def _clean_summary(...)` (lines 30–37)
- `def _clean_markdown(...)` (lines 40–55)
- `def _ensure_bullet_prefix(...)` (lines 58–69)
- `def _render_html_summary(...)` (lines 72–79)

Also remove `re` from the imports at the top (check if it's used elsewhere first; if not, remove it).

- [ ] **Step 4: Update `_extract_entry_description` to use `rendering.py` functions**

Replace the body of `_extract_entry_description` (lines 122–141) with:

```python
def _extract_entry_description(entry: feedparser.FeedParserDict, *, max_length: int = 1800) -> Optional[str]:
    contents = entry.get("content")
    if isinstance(contents, list):
        for item in contents:
            if not isinstance(item, dict):
                continue
            value = item.get("value")
            if value:
                rendered = html_to_discord_markdown(str(value))
                if rendered:
                    return truncate_embed_field(rendered, max_length) if len(rendered) > max_length else rendered
                return clean_html(str(value), max_length=max_length)

    summary = entry.get("summary") or entry.get("description")
    if summary:
        rendered = html_to_discord_markdown(str(summary))
        if rendered:
            return truncate_embed_field(rendered, max_length) if len(rendered) > max_length else rendered
        return clean_html(str(summary), max_length=max_length)
    return None
```

- [ ] **Step 5: Run RSS tests to verify nothing broke**

```bash
python -m pytest tests/test_cogs_rss.py -v
```

Expected: both tests PASS (including `test_extract_entry_description_preserves_markdown`)

- [ ] **Step 6: Run full test suite to catch regressions**

```bash
python -m pytest -v
```

Expected: all tests PASS

- [ ] **Step 7: Commit**

```bash
git add axitools/cogs/rss.py
git commit -m "refactor: rss.py uses shared rendering utilities"
```

---

## Task 3: Update `cogs/update_notes.py` to use `rendering.py`

**Files:**
- Modify: `axitools/cogs/update_notes.py`

- [ ] **Step 1: Verify the existing update_notes test passes**

```bash
python -m pytest tests/test_cogs_update_notes.py -v
```

Expected: 1 test PASS

- [ ] **Step 2: Add import to `axitools/cogs/update_notes.py`**

Add this import near the top (after existing imports):

```python
from ..rendering import clean_markdown
```

- [ ] **Step 3: Update `_render_comment_content` to use `clean_markdown` from rendering**

The existing `_render_comment_content` method calls `self._clean_markdown(markdown)`. Replace that with `clean_markdown(markdown)` from the import:

```python
def _render_comment_content(self, content) -> str:
    for element in content.select("script, style"):
        element.decompose()
    markdown = html_to_markdown(
        str(content), heading_style="ATX", bullets="-*+", strip=["img"]
    )
    return clean_markdown(markdown)
```

- [ ] **Step 4: Delete the two now-unused instance methods**

Delete `_clean_markdown(self, text)` and `_ensure_bullet_prefix(self, text)` from `UpdateNotesCog`.

- [ ] **Step 5: Run tests to verify nothing broke**

```bash
python -m pytest tests/test_cogs_update_notes.py tests/test_cogs_rss.py -v
```

Expected: all tests PASS

- [ ] **Step 6: Run full test suite**

```bash
python -m pytest -v
```

Expected: all tests PASS

- [ ] **Step 7: Commit**

```bash
git add axitools/cogs/update_notes.py
git commit -m "refactor: update_notes.py uses shared rendering utilities"
```

---

## Task 4: Create `axitools/config_status.py`

**Files:**
- Create: `axitools/config_status.py`
- Create: `tests/test_config_status.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config_status.py
from axitools.config_status import ConfigStatus, StatusField


def test_status_field_states():
    for state in ("ok", "warn", "missing"):
        field = StatusField(label="Test", value="value", state=state)
        assert field.state == state


def test_config_status_defaults():
    status = ConfigStatus(title="Builds", fields=[])
    assert status.setup_command is None
    assert status.fields == []


def test_config_status_with_setup_command():
    fields = [
        StatusField(label="Channel", value="not set", state="missing"),
    ]
    status = ConfigStatus(title="📋 Builds", fields=fields, setup_command="/config")
    assert status.setup_command == "/config"
    assert len(status.fields) == 1


def test_config_status_field_labels():
    field = StatusField(label="Build channel", value="#builds", state="ok")
    assert field.label == "Build channel"
    assert field.value == "#builds"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_config_status.py -v
```

Expected: `ModuleNotFoundError: No module named 'axitools.config_status'`

- [ ] **Step 3: Implement `axitools/config_status.py`**

```python
"""Shared dataclasses for per-cog configuration status reporting."""
from __future__ import annotations

from dataclasses import dataclass, field
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_config_status.py -v
```

Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add axitools/config_status.py tests/test_config_status.py
git commit -m "feat: add ConfigStatus interface for per-cog status reporting"
```

---

## Task 5: Create `axitools/export.py`

**Files:**
- Create: `axitools/export.py`
- Create: `tests/test_export.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_export.py
import csv
import io

from axitools.export import MemberExportRow, members_to_csv


def _read_csv(discord_file) -> list[list[str]]:
    content = discord_file.fp.read().decode("utf-8")
    return list(csv.reader(io.StringIO(content)))


def test_members_to_csv_header_only_when_empty():
    result = members_to_csv([])
    rows = _read_csv(result)
    assert len(rows) == 1
    assert rows[0] == [
        "Discord ID", "Discord Name", "Account Name",
        "Guild IDs", "Guild Names", "Roles", "Characters",
    ]


def test_members_to_csv_single_row():
    row = MemberExportRow(
        member_id=123456,
        member_display="TestUser (testuser)",
        account_names=["Account.1234"],
        guild_ids=["abc123"],
        guild_names=["My Guild"],
        roles=["Officer", "Member"],
        characters=["Warrior", "Ranger"],
    )
    rows = _read_csv(members_to_csv([row]))
    assert len(rows) == 2
    assert rows[1][0] == "123456"
    assert rows[1][1] == "TestUser (testuser)"
    assert rows[1][2] == "Account.1234"
    assert rows[1][3] == "abc123"
    assert rows[1][4] == "My Guild"
    assert rows[1][5] == "Officer; Member"
    assert rows[1][6] == "Warrior; Ranger"


def test_members_to_csv_multiple_account_names():
    row = MemberExportRow(
        member_id=1,
        member_display="User (user)",
        account_names=["Acc.1234", "Alt.5678"],
        guild_ids=[],
        guild_names=[],
        roles=[],
        characters=[],
    )
    rows = _read_csv(members_to_csv([row]))
    assert rows[1][2] == "Acc.1234; Alt.5678"


def test_members_to_csv_filename():
    result = members_to_csv([])
    assert result.filename == "select_query.csv"


def test_members_to_csv_utf8_encoded():
    row = MemberExportRow(
        member_id=1,
        member_display="Ünïcödé (unicode)",
        account_names=["Café.1234"],
        guild_ids=[],
        guild_names=[],
        roles=[],
        characters=[],
    )
    result = members_to_csv([row])
    content = result.fp.read()
    assert isinstance(content, bytes)
    assert "Café.1234".encode("utf-8") in content
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_export.py -v
```

Expected: `ModuleNotFoundError: No module named 'axitools.export'`

- [ ] **Step 3: Implement `axitools/export.py`**

```python
"""CSV export utilities for member data."""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_export.py -v
```

Expected: 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add axitools/export.py tests/test_export.py
git commit -m "feat: add members_to_csv export utility"
```

---

## Task 6: Create `axitools/member_filter.py`

**Files:**
- Create: `axitools/member_filter.py`
- Create: `tests/test_member_filter.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_member_filter.py
from axitools.member_filter import BlanketCondition, blanket_condition_matches


def test_blanket_condition_eq_matches_list_case_insensitive():
    row = {"character_name": ["One", "Two"]}
    assert blanket_condition_matches(
        BlanketCondition(field="character_name", operator="==", value="two"),
        row,
    )


def test_blanket_condition_eq_no_match():
    row = {"character_name": ["One", "Two"]}
    assert not blanket_condition_matches(
        BlanketCondition(field="character_name", operator="==", value="three"),
        row,
    )


def test_blanket_condition_contains_match():
    row = {"character_name": ["One", "Two"]}
    assert blanket_condition_matches(
        BlanketCondition(field="character_name", operator="~=", value="on"),
        row,
    )


def test_blanket_condition_not_eq():
    row = {"character_name": ["One", "Two"]}
    assert not blanket_condition_matches(
        BlanketCondition(field="character_name", operator="!=", value="two"),
        row,
    )


def test_blanket_condition_missing_field():
    row = {"account_name": "Test.1234"}
    assert not blanket_condition_matches(
        BlanketCondition(field="character_name", operator="==", value="x"),
        row,
    )


def test_blanket_condition_scalar_value():
    row = {"account_name": "Test.1234"}
    assert blanket_condition_matches(
        BlanketCondition(field="account_name", operator="==", value="test.1234"),
        row,
    )
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_member_filter.py -v
```

Expected: `ModuleNotFoundError: No module named 'axitools.member_filter'`

- [ ] **Step 3: Move `_FilterSet`, `_BlanketCondition` and implement `blanket_condition_matches`**

Look in `axitools/cogs/select.py` for `_FilterSet` (lines 25–37) and `_BlanketCondition` (lines 40–44) and the `_blanket_condition_matches` static method on `SelectCog`. Create `axitools/member_filter.py` with the following (note: public names drop the leading underscore):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_member_filter.py -v
```

Expected: 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add axitools/member_filter.py tests/test_member_filter.py
git commit -m "feat: add member_filter module with BlanketCondition"
```

---

## Task 7: Create `axitools/query_schema.py`

This module receives the static methods from `SelectCog` that handle query parsing and AI schema generation. The existing tests in `test_cogs_select.py` call these as `SelectCog._method()` — after this task we will update those tests in Task 8.

**Files:**
- Create: `axitools/query_schema.py`
- Create: `tests/test_query_schema.py`

- [ ] **Step 1: Read the existing static methods to move**

Open `axitools/cogs/select.py` and locate these static/class methods on `SelectCog`:
- `_parse_blanket_query(text)` — returns `(fields, conditions)`
- `_is_read_only_select_query_text(text)` — returns bool
- `_ai_response_text(payload)` — returns str
- `_extract_select_statement(text)` — returns str
- `_coerce_query_to_full_rows(prompt, query)` — returns str
- `_build_ai_schema_context_from_records(records, scope)` — returns str

Also locate the class constants used by these methods:
- `BLANKET_DEFAULT_FIELDS`
- `BLANKET_FIELD_ALIASES`
- `BLANKET_FIELD_LABELS`
- `AI_FORBIDDEN_KEYWORDS`

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_query_schema.py
import pytest

from axitools.member_filter import BlanketCondition
from axitools.query_schema import (
    BLANKET_DEFAULT_FIELDS,
    ai_response_text,
    coerce_query_to_full_rows,
    extract_select_statement,
    is_read_only_select_query_text,
    parse_blanket_query,
)
from axitools.storage import ApiKeyRecord


def test_parse_blanket_query_basic():
    fields, conditions = parse_blanket_query(
        "SELECT user, api_keys WHERE account_name == blah.123 AND character_name == 'blahblah'"
    )
    assert fields == ["user", "api_keys"]
    assert len(conditions) == 2
    assert conditions[0] == BlanketCondition(
        field="account_name", operator="==", value="blah.123"
    )
    assert conditions[1] == BlanketCondition(
        field="character_name", operator="==", value="blahblah"
    )


def test_parse_blanket_query_wildcard_and_from():
    fields, conditions = parse_blanket_query(
        "SELECT * FROM api_keys WHERE permission ~= guilds"
    )
    assert fields == list(BLANKET_DEFAULT_FIELDS)
    assert conditions == [
        BlanketCondition(field="permission", operator="~=", value="guilds")
    ]


def test_parse_blanket_query_invalid_field():
    with pytest.raises(ValueError):
        parse_blanket_query("SELECT super_secret WHERE account_name == x")


def test_parse_blanket_query_table_alias_field():
    fields, conditions = parse_blanket_query(
        "SELECT api_keys.name, api_keys.created_at WHERE api_keys.account_name == ondria.1592"
    )
    assert fields == ["api_keys", "created_at"]
    assert conditions == [
        BlanketCondition(field="account_name", operator="==", value="ondria.1592")
    ]


def test_is_read_only_select_query_text_valid():
    assert is_read_only_select_query_text(
        "SELECT user WHERE account_name == Test.1234"
    )


def test_is_read_only_select_query_text_rejects_update():
    assert not is_read_only_select_query_text("UPDATE api_keys SET name = 'x'")


def test_is_read_only_select_query_text_rejects_semicolon():
    assert not is_read_only_select_query_text(
        "SELECT user; DELETE FROM api_keys"
    )


def test_ai_response_text_chat_completions_style():
    payload = {
        "choices": [
            {
                "message": {
                    "content": "SELECT user, api_keys WHERE account_name == ondria.1592"
                }
            }
        ]
    }
    assert (
        ai_response_text(payload)
        == "SELECT user, api_keys WHERE account_name == ondria.1592"
    )


def test_extract_select_statement_from_code_fence():
    text = "Here you go:\n```sql\nSELECT user, api_keys WHERE account_name == ondria.1592\n```"
    assert (
        extract_select_statement(text)
        == "SELECT user, api_keys WHERE account_name == ondria.1592"
    )


def test_coerce_query_to_full_rows():
    prompt = "show full rows for ondria.1592"
    query = "SELECT user, api_keys WHERE account_name == ondria.1592"
    assert (
        coerce_query_to_full_rows(prompt=prompt, query=query)
        == "SELECT * WHERE account_name == ondria.1592"
    )


def test_build_ai_schema_context_from_records():
    from axitools.query_schema import build_ai_schema_context_from_records

    record = ApiKeyRecord(
        name="Main Key",
        key="secret",
        account_name="Ondria.1592",
        permissions=["account", "guilds"],
        guild_ids=["abcd-1234"],
        characters=["One"],
    )
    context = build_ai_schema_context_from_records(
        [(1, 2, record)],
        scope="mine",
    )
    assert "scope: mine" in context
    assert "sample_account_names: Ondria.1592" in context
    assert "sample_key_names: Main Key" in context
    assert "sample_guild_ids: abcd-1234" in context
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
python -m pytest tests/test_query_schema.py -v
```

Expected: `ModuleNotFoundError: No module named 'axitools.query_schema'`

- [ ] **Step 4: Create `axitools/query_schema.py` by moving static methods from `SelectCog`**

Create `axitools/query_schema.py`. Copy the body of each static method from `SelectCog` in `cogs/select.py`, converting them from `@staticmethod` / `@classmethod` definitions to module-level functions. Copy the four class constants (`BLANKET_DEFAULT_FIELDS`, `BLANKET_FIELD_ALIASES`, `BLANKET_FIELD_LABELS`, `AI_FORBIDDEN_KEYWORDS`) to module level in `query_schema.py`.

Import `BlanketCondition` from `axitools.member_filter` (not from `select.py`). The function signatures change as follows:

| SelectCog method | Module-level function in `query_schema.py` |
|---|---|
| `SelectCog._parse_blanket_query(text)` | `parse_blanket_query(text)` |
| `SelectCog._is_read_only_select_query_text(text)` | `is_read_only_select_query_text(text)` |
| `SelectCog._ai_response_text(payload)` | `ai_response_text(payload)` |
| `SelectCog._extract_select_statement(text)` | `extract_select_statement(text)` |
| `SelectCog._coerce_query_to_full_rows(prompt, query)` | `coerce_query_to_full_rows(prompt, query)` |
| `SelectCog._build_ai_schema_context_from_records(records, scope)` | `build_ai_schema_context_from_records(records, scope)` |

The file should begin:

```python
"""Query parsing and AI schema helpers for member search."""
from __future__ import annotations

# ... (copy imports from SelectCog that these functions use)
from .member_filter import BlanketCondition

# Copy BLANKET_DEFAULT_FIELDS, BLANKET_FIELD_ALIASES, BLANKET_FIELD_LABELS,
# AI_FORBIDDEN_KEYWORDS from SelectCog as module-level constants here.

# Then define each function with the body copied from the corresponding
# SelectCog static method, replacing any reference to _BlanketCondition
# with BlanketCondition.
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_query_schema.py -v
```

Expected: all 11 tests PASS

- [ ] **Step 6: Run full test suite**

```bash
python -m pytest -v
```

Expected: all tests PASS (the existing `test_cogs_select.py` still passes because `SelectCog` still has those methods — we haven't removed them yet)

- [ ] **Step 7: Commit**

```bash
git add axitools/query_schema.py tests/test_query_schema.py
git commit -m "feat: add query_schema module extracted from SelectCog"
```

---

## Task 8: Slim down `cogs/select.py`

Replace the duplicated logic in `SelectCog` with imports from the new modules, update `test_cogs_select.py` accordingly, and add `get_config_status()`.

**Files:**
- Modify: `axitools/cogs/select.py`
- Modify: `tests/test_cogs_select.py`

- [ ] **Step 1: Update `axitools/cogs/select.py` imports**

Add at the top of `cogs/select.py`:

```python
from ..export import MemberExportRow, members_to_csv
from ..member_filter import BlanketCondition, FilterSet, blanket_condition_matches
from ..query_schema import (
    BLANKET_DEFAULT_FIELDS,
    BLANKET_FIELD_ALIASES,
    BLANKET_FIELD_LABELS,
    AI_FORBIDDEN_KEYWORDS,
    ai_response_text,
    build_ai_schema_context_from_records,
    coerce_query_to_full_rows,
    extract_select_statement,
    is_read_only_select_query_text,
    parse_blanket_query,
)
from ..config_status import ConfigStatus, StatusField
```

Remove `import csv` and `import io` from the top (they are now handled by `export.py`).

- [ ] **Step 2: Replace CSV export block with `members_to_csv`**

Locate the CSV export block in `SelectCog` (around lines 1761–1812):

```python
if as_csv:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([...])
    for (...) in matched:
        ...
        writer.writerow([...])
    buffer.seek(0)
    files = [discord.File(fp=io.BytesIO(buffer.getvalue().encode("utf-8")), filename="select_query.csv")]
```

Replace it with:

```python
if as_csv:
    export_rows = []
    for (
        member,
        account_names,
        characters,
        character_entries,
        _matched_guilds,
        _,
        _,
        guild_ids,
        filter_set,
    ) in matched:
        guild_labels = [guild_details.get(gid, gid) for gid in guild_ids]
        roles = [role.name for role in member.roles if not role.is_default()]
        characters_for_csv = (
            [name for name, _ in character_entries]
            if filter_set.character_provided
            else characters
        )
        export_rows.append(MemberExportRow(
            member_id=member.id,
            member_display=f"{member.display_name} ({member.name})",
            account_names=list(account_names),
            guild_ids=list(guild_ids),
            guild_names=guild_labels or ["No guilds"],
            roles=roles,
            characters=characters_for_csv,
        ))
    files = [members_to_csv(export_rows)]
```

- [ ] **Step 3: Replace `_FilterSet` and `_BlanketCondition` usages**

Throughout `SelectCog`, replace:
- `_FilterSet(...)` → `FilterSet(...)` 
- `_BlanketCondition(...)` → `BlanketCondition(...)`

Delete the `_FilterSet` and `_BlanketCondition` class definitions from the top of `select.py`.

- [ ] **Step 4: Replace static method calls with module-level function calls**

Replace each call to `SelectCog._method()` inside `SelectCog` methods with the imported module-level function:
- `self._parse_blanket_query(...)` → `parse_blanket_query(...)`
- `self._blanket_condition_matches(...)` → `blanket_condition_matches(...)`
- `self._is_read_only_select_query_text(...)` → `is_read_only_select_query_text(...)`
- `self._ai_response_text(...)` → `ai_response_text(...)`
- `self._extract_select_statement(...)` → `extract_select_statement(...)`
- `self._coerce_query_to_full_rows(...)` → `coerce_query_to_full_rows(...)`
- `self._build_ai_schema_context_from_records(...)` → `build_ai_schema_context_from_records(...)`

Delete the static method definitions from `SelectCog` (the constants and static methods moved to `query_schema.py`).

Remove the `BLANKET_DEFAULT_FIELDS`, `BLANKET_FIELD_ALIASES`, `BLANKET_FIELD_LABELS`, `AI_FORBIDDEN_KEYWORDS` class-level constants (now imported from `query_schema`).

- [ ] **Step 5: Add `get_config_status()` to `SelectCog`**

Add this method to `SelectCog` (after `__init__`):

```python
async def get_config_status(self, guild: discord.Guild) -> ConfigStatus:
    return ConfigStatus(
        title="🔍 Member Search",
        fields=[StatusField(label="Status", value="ready", state="ok")],
    )
```

- [ ] **Step 6: Update `tests/test_cogs_select.py`**

Change the import at the top of `tests/test_cogs_select.py` from:

```python
from axitools.cogs.select import SelectCog, _BlanketCondition
```

to:

```python
from axitools.cogs.select import SelectCog
from axitools.member_filter import BlanketCondition
from axitools.query_schema import (
    BLANKET_DEFAULT_FIELDS,
    ai_response_text,
    build_ai_schema_context_from_records,
    coerce_query_to_full_rows,
    extract_select_statement,
    is_read_only_select_query_text,
    parse_blanket_query,
)
```

Replace every call to `SelectCog._parse_blanket_query(...)` with `parse_blanket_query(...)`, and so on for each method. Replace `_BlanketCondition` with `BlanketCondition`. Remove any tests that are now exact duplicates of what's in `test_query_schema.py` and `test_member_filter.py`.

- [ ] **Step 7: Run the full test suite**

```bash
python -m pytest -v
```

Expected: all tests PASS

- [ ] **Step 8: Commit**

```bash
git add axitools/cogs/select.py tests/test_cogs_select.py
git commit -m "refactor: slim select.py cog, delegate to member_filter/query_schema/export"
```

---

## Task 9: Add `count_api_keys` to `StorageManager`

**Files:**
- Modify: `axitools/storage.py`
- Modify: `tests/test_storage.py`

- [ ] **Step 1: Write the failing test**

Open `tests/test_storage.py` and add:

```python
def test_count_api_keys_empty(tmp_path):
    from axitools.storage import StorageManager
    storage = StorageManager(tmp_path)
    assert storage.count_api_keys(12345) == 0
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
python -m pytest tests/test_storage.py::test_count_api_keys_empty -v
```

Expected: `AttributeError: 'StorageManager' object has no attribute 'count_api_keys'`

- [ ] **Step 3: Add `count_api_keys` to `StorageManager` in `axitools/storage.py`**

Find the API key section of `StorageManager` (near `get_user_api_keys`). Add:

```python
def count_api_keys(self, guild_id: int) -> int:
    """Return the total number of API keys registered for a guild."""
    with self._connect() as connection:
        row = connection.execute(
            "SELECT COUNT(1) FROM api_keys WHERE guild_id = ?",
            (str(guild_id),),
        ).fetchone()
    return row[0] if row else 0
```

Note: check whether `guild_id` is stored as a string or integer in the database by looking at nearby queries (e.g. `get_user_api_keys`). Match the same cast.

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_storage.py -v
```

Expected: all tests PASS including the new one

- [ ] **Step 5: Commit**

```bash
git add axitools/storage.py tests/test_storage.py
git commit -m "feat: add StorageManager.count_api_keys()"
```

---

## Task 10: Add `get_config_status()` to feature cogs

**Files:**
- Modify: `axitools/cogs/builds.py`
- Modify: `axitools/cogs/rss.py`
- Modify: `axitools/cogs/arcdps.py`
- Modify: `axitools/cogs/update_notes.py`
- Modify: `axitools/cogs/comps.py`
- Modify: `axitools/cogs/accounts.py`
- Modify: `axitools/cogs/audit.py`
- Modify: `tests/test_cogs_builds.py`
- Modify: `tests/test_cogs_rss.py`
- Modify: `tests/test_cogs_arcdps.py`
- Modify: `tests/test_cogs_update_notes.py`

In each cog:
1. Add `from ..config_status import ConfigStatus, StatusField` to the imports
2. Add the `get_config_status()` async method

---

### 10a: `BuildsCog.get_config_status()`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cogs_builds.py`:

```python
import pytest
from unittest.mock import MagicMock, AsyncMock

@pytest.mark.asyncio
async def test_builds_get_config_status_no_builds(tmp_path):
    from axitools.cogs.builds import BuildsCog
    from axitools.storage import StorageManager
    bot = MagicMock()
    bot.storage = StorageManager(tmp_path)
    bot.get_config.return_value = MagicMock(build_channel_id=None)
    cog = BuildsCog(bot)
    guild = MagicMock()
    guild.get_channel.return_value = None
    status = await cog.get_config_status(guild)
    assert status.title == "📋 Builds"
    assert any(f.state == "warn" for f in status.fields)
```

- [ ] **Step 2: Run to verify failure**

```bash
python -m pytest tests/test_cogs_builds.py::test_builds_get_config_status_no_builds -v
```

Expected: `AttributeError: 'BuildsCog' object has no attribute 'get_config_status'`

- [ ] **Step 3: Add `get_config_status` to `BuildsCog` in `cogs/builds.py`**

Add this import: `from ..config_status import ConfigStatus, StatusField`

Add after `__init__`:

```python
async def get_config_status(self, guild: discord.Guild) -> ConfigStatus:
    builds = self.bot.storage.get_builds(guild.id)
    fields = [
        StatusField(
            label="Builds",
            value=str(len(builds)) if builds else "none registered",
            state="ok" if builds else "warn",
        )
    ]
    return ConfigStatus(title="📋 Builds", fields=fields, setup_command="/builds add")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_cogs_builds.py -v
```

---

### 10b: `RssFeedsCog.get_config_status()`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cogs_rss.py`:

```python
@pytest.mark.asyncio
async def test_rss_get_config_status_no_feeds(tmp_path, mock_bot_rss):
    from axitools.cogs.rss import RssFeedsCog
    from axitools.storage import StorageManager
    mock_bot_rss.storage = StorageManager(tmp_path)
    cog = RssFeedsCog(mock_bot_rss)
    cog._feed_poll.cancel()
    guild = MagicMock()
    status = await cog.get_config_status(guild)
    assert status.title == "📡 RSS"
    assert any(f.state == "missing" for f in status.fields)
```

- [ ] **Step 2: Run to verify failure**

```bash
python -m pytest tests/test_cogs_rss.py::test_rss_get_config_status_no_feeds -v
```

- [ ] **Step 3: Add `get_config_status` to `RssFeedsCog` in `cogs/rss.py`**

Add import: `from ..config_status import ConfigStatus, StatusField`

Add method:

```python
async def get_config_status(self, guild: discord.Guild) -> ConfigStatus:
    feeds = self.bot.storage.get_rss_feeds(guild.id)
    fields = [
        StatusField(
            label="RSS feeds",
            value=str(len(feeds)) if feeds else "none configured",
            state="ok" if feeds else "missing",
        )
    ]
    return ConfigStatus(title="📡 RSS", fields=fields, setup_command="/rss set")
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_cogs_rss.py -v
```

---

### 10c: `ArcDpsUpdatesCog.get_config_status()`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cogs_arcdps.py`:

```python
@pytest.mark.asyncio
async def test_arcdps_get_config_status_no_channel():
    from axitools.cogs.arcdps import ArcDpsUpdatesCog
    bot = MagicMock()
    bot.get_config.return_value = MagicMock(arcdps_channel_id=None)
    bot.wait_until_ready = AsyncMock()
    cog = ArcDpsUpdatesCog(bot)
    cog._poll_arcdps.cancel()
    guild = MagicMock()
    status = await cog.get_config_status(guild)
    assert status.title == "🔔 ArcDPS"
    assert any(f.state == "missing" for f in status.fields)
```

- [ ] **Step 2: Run to verify failure**

```bash
python -m pytest tests/test_cogs_arcdps.py::test_arcdps_get_config_status_no_channel -v
```

- [ ] **Step 3: Add `get_config_status` to `ArcDpsUpdatesCog` in `cogs/arcdps.py`**

Add import: `from ..config_status import ConfigStatus, StatusField`

Add method:

```python
async def get_config_status(self, guild: discord.Guild) -> ConfigStatus:
    config = self.bot.get_config(guild.id)
    if config.arcdps_channel_id:
        ch = guild.get_channel(config.arcdps_channel_id)
        field = StatusField(
            label="Channel",
            value=ch.mention if ch else "channel not found",
            state="ok" if ch else "warn",
        )
    else:
        field = StatusField(label="Channel", value="not set", state="missing")
    return ConfigStatus(title="🔔 ArcDPS", fields=[field], setup_command="/config")
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_cogs_arcdps.py -v
```

---

### 10d: `UpdateNotesCog.get_config_status()`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cogs_update_notes.py`:

```python
@pytest.mark.asyncio
async def test_update_notes_get_config_status_no_channel():
    from axitools.cogs.update_notes import UpdateNotesCog
    bot = MagicMock()
    bot.get_config.return_value = MagicMock(update_notes_channel_id=None)
    cog = UpdateNotesCog(bot)
    cog._poll_updates.cancel()
    guild = MagicMock()
    status = await cog.get_config_status(guild)
    assert status.title == "📰 Update Notes"
    assert any(f.state == "missing" for f in status.fields)
```

- [ ] **Step 2: Run to verify failure**

```bash
python -m pytest tests/test_cogs_update_notes.py::test_update_notes_get_config_status_no_channel -v
```

- [ ] **Step 3: Add `get_config_status` to `UpdateNotesCog` in `cogs/update_notes.py`**

Add import: `from ..config_status import ConfigStatus, StatusField`

Add method:

```python
async def get_config_status(self, guild: discord.Guild) -> ConfigStatus:
    config = self.bot.get_config(guild.id)
    if config.update_notes_channel_id:
        ch = guild.get_channel(config.update_notes_channel_id)
        field = StatusField(
            label="Channel",
            value=ch.mention if ch else "channel not found",
            state="ok" if ch else "warn",
        )
    else:
        field = StatusField(label="Channel", value="not set", state="missing")
    return ConfigStatus(title="📰 Update Notes", fields=[field], setup_command="/config")
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_cogs_update_notes.py -v
```

---

### 10e: `CompCog.get_config_status()`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cogs_comps.py`:

```python
@pytest.mark.asyncio
async def test_comps_get_config_status_empty(tmp_path):
    from axitools.cogs.comps import CompCog
    from axitools.storage import StorageManager
    bot = MagicMock()
    bot.storage = StorageManager(tmp_path)
    bot.get_config.return_value = MagicMock(comp_schedules=[])
    cog = CompCog(bot)
    guild = MagicMock()
    guild.id = 99999
    status = await cog.get_config_status(guild)
    assert status.title == "🎯 Compositions"
    assert any(f.label == "Presets" for f in status.fields)
    assert any(f.label == "Schedules" for f in status.fields)
```

- [ ] **Step 2: Run to verify failure**

```bash
python -m pytest tests/test_cogs_comps.py::test_comps_get_config_status_empty -v
```

- [ ] **Step 3: Add `get_config_status` to `CompCog` in `cogs/comps.py`**

Add import: `from ..config_status import ConfigStatus, StatusField`

Add method:

```python
async def get_config_status(self, guild: discord.Guild) -> ConfigStatus:
    config = self.bot.get_config(guild.id)
    presets = self.bot.storage.get_comp_presets(guild.id)
    fields = [
        StatusField(
            label="Presets",
            value=str(len(presets)) if presets else "none",
            state="ok" if presets else "warn",
        ),
        StatusField(
            label="Schedules",
            value=str(len(config.comp_schedules)) if config.comp_schedules else "none",
            state="ok" if config.comp_schedules else "warn",
        ),
    ]
    return ConfigStatus(title="🎯 Compositions", fields=fields, setup_command="/comp manage")
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_cogs_comps.py -v
```

---

### 10f: `AccountsCog.get_config_status()`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cogs_accounts.py`:

```python
@pytest.mark.asyncio
async def test_accounts_get_config_status_no_keys(tmp_path):
    from axitools.cogs.accounts import AccountsCog
    from axitools.storage import StorageManager
    bot = MagicMock()
    bot.storage = StorageManager(tmp_path)
    cog = AccountsCog(bot)
    guild = MagicMock()
    guild.id = 99999
    status = await cog.get_config_status(guild)
    assert status.title == "👤 Accounts"
    assert any(f.state == "warn" for f in status.fields)
```

- [ ] **Step 2: Run to verify failure**

```bash
python -m pytest tests/test_cogs_accounts.py::test_accounts_get_config_status_no_keys -v
```

- [ ] **Step 3: Add `get_config_status` to `AccountsCog` in `cogs/accounts.py`**

Add import: `from ..config_status import ConfigStatus, StatusField`

Add method:

```python
async def get_config_status(self, guild: discord.Guild) -> ConfigStatus:
    count = self.bot.storage.count_api_keys(guild.id)
    fields = [
        StatusField(
            label="API keys",
            value=str(count) if count > 0 else "none registered",
            state="ok" if count > 0 else "warn",
        )
    ]
    return ConfigStatus(title="👤 Accounts", fields=fields, setup_command="/apikey add")
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_cogs_accounts.py -v
```

---

### 10g: `AuditCog.get_config_status()`

- [ ] **Step 1: Write the failing test**

Add to a new section in `tests/test_cogs_config.py` or create `tests/test_cogs_audit.py`:

```python
# Add to tests/test_cogs_config.py or a new test_cogs_audit.py

@pytest.mark.asyncio
async def test_audit_get_config_status_unconfigured():
    from axitools.cogs.audit import AuditCog
    bot = MagicMock()
    bot.get_config.return_value = MagicMock(
        audit_channel_id=None,
        audit_gw2_guild_id=None,
    )
    cog = AuditCog(bot)
    guild = MagicMock()
    status = await cog.get_config_status(guild)
    assert status.title == "📊 Audit"
    missing = [f for f in status.fields if f.state == "missing"]
    assert len(missing) >= 1
```

- [ ] **Step 2: Run to verify failure**

```bash
python -m pytest -k "test_audit_get_config_status_unconfigured" -v
```

- [ ] **Step 3: Add `get_config_status` to `AuditCog` in `cogs/audit.py`**

Add import: `from ..config_status import ConfigStatus, StatusField`

Add method:

```python
async def get_config_status(self, guild: discord.Guild) -> ConfigStatus:
    config = self.bot.get_config(guild.id)
    fields = []
    if config.audit_channel_id:
        ch = guild.get_channel(config.audit_channel_id)
        fields.append(StatusField(
            label="Audit channel",
            value=ch.mention if ch else "channel not found",
            state="ok" if ch else "warn",
        ))
    else:
        fields.append(StatusField(
            label="Audit channel", value="not set", state="missing"
        ))
    if config.audit_gw2_guild_id:
        fields.append(StatusField(
            label="GW2 guild", value=config.audit_gw2_guild_id, state="ok"
        ))
    else:
        fields.append(StatusField(
            label="GW2 guild", value="not configured", state="missing"
        ))
    return ConfigStatus(title="📊 Audit", fields=fields, setup_command="/audit")
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest -k "audit" -v
```

- [ ] **Step 5: Run full test suite**

```bash
python -m pytest -v
```

Expected: all tests PASS

- [ ] **Step 6: Commit all cog status additions**

```bash
git add axitools/cogs/builds.py axitools/cogs/rss.py axitools/cogs/arcdps.py \
        axitools/cogs/update_notes.py axitools/cogs/comps.py \
        axitools/cogs/accounts.py axitools/cogs/audit.py \
        tests/test_cogs_builds.py tests/test_cogs_rss.py tests/test_cogs_arcdps.py \
        tests/test_cogs_update_notes.py tests/test_cogs_comps.py \
        tests/test_cogs_accounts.py
git commit -m "feat: add get_config_status() to all feature cogs"
```

---

## Task 11: Add `ConfigCog.get_config_status()` and implement `/status` command

**Files:**
- Modify: `axitools/cogs/config.py`
- Modify: `tests/test_cogs_config.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_cogs_config.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock

@pytest.mark.asyncio
async def test_config_get_config_status_no_roles_no_channel():
    from axitools.cogs.config import ConfigCog
    bot = MagicMock()
    bot.get_config.return_value = MagicMock(
        moderator_role_ids=[],
        build_channel_id=None,
    )
    cog = ConfigCog(bot)
    guild = MagicMock()
    guild.get_role.return_value = None
    guild.get_channel.return_value = None
    status = await cog.get_config_status(guild)
    assert status.title == "⚙️ Core"
    labels = {f.label for f in status.fields}
    assert "Moderator roles" in labels
    assert "Build channel" in labels
    missing = [f for f in status.fields if f.state == "missing"]
    assert any(f.label == "Build channel" for f in missing)


@pytest.mark.asyncio
async def test_status_command_unauthorized():
    from axitools.cogs.config import ConfigCog
    bot = MagicMock()
    bot.is_authorised.return_value = False
    cog = ConfigCog(bot)
    interaction = MagicMock()
    interaction.guild = MagicMock()
    interaction.user = MagicMock(spec=["guild_id", "roles", "guild_permissions"])
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    await cog.status_command.callback(cog, interaction)
    interaction.response.send_message.assert_called_once()
    call_kwargs = interaction.response.send_message.call_args[1]
    assert call_kwargs.get("ephemeral") is True
```

- [ ] **Step 2: Run to verify failures**

```bash
python -m pytest tests/test_cogs_config.py -k "test_config_get_config_status or test_status_command_unauthorized" -v
```

Expected: both tests FAIL

- [ ] **Step 3: Add imports to `axitools/cogs/config.py`**

Add to the top of `config.py`:

```python
import logging

from ..branding import BRAND_COLOUR
from ..config_status import ConfigStatus, StatusField

LOGGER = logging.getLogger(__name__)

STATE_ICONS: dict[str, str] = {"ok": "✅", "warn": "⚠️", "missing": "❌"}
```

- [ ] **Step 4: Add `get_config_status()` to `ConfigCog`**

Add after `__init__` in `ConfigCog`:

```python
async def get_config_status(self, guild: discord.Guild) -> ConfigStatus:
    config = self.bot.get_config(guild.id)
    fields: list[StatusField] = []

    if config.moderator_role_ids:
        roles = [guild.get_role(r) for r in config.moderator_role_ids]
        names = ", ".join(f"@{r.name}" for r in roles if r)
        fields.append(StatusField(
            label="Moderator roles",
            value=names or "configured (roles not found in guild)",
            state="ok",
        ))
    else:
        fields.append(StatusField(
            label="Moderator roles",
            value="admins only",
            state="warn",
        ))

    if config.build_channel_id:
        ch = guild.get_channel(config.build_channel_id)
        fields.append(StatusField(
            label="Build channel",
            value=ch.mention if ch else "channel not found",
            state="ok" if ch else "warn",
        ))
    else:
        fields.append(StatusField(
            label="Build channel", value="not set", state="missing"
        ))

    return ConfigStatus(title="⚙️ Core", fields=fields, setup_command="/config")
```

- [ ] **Step 5: Add `StatusView` and helper functions to `config.py`**

Add these before `ConfigCog`:

```python
def _is_first_run(statuses: list[ConfigStatus]) -> bool:
    for status in statuses:
        if status.title == "⚙️ Core":
            by_label = {f.label: f.state for f in status.fields}
            return (
                by_label.get("Moderator roles") in ("missing", "warn")
                and by_label.get("Build channel") == "missing"
            )
    return False


def _build_status_embed(
    guild: discord.Guild, statuses: list[ConfigStatus]
) -> discord.Embed:
    description: Optional[str] = None
    if _is_first_run(statuses):
        description = (
            "**Getting Started**\n"
            "1. `/config` — set moderator roles and build channel\n"
            "2. `/builds add` — add your first build\n"
            "3. Configure other features as needed\n"
        )
    embed = discord.Embed(
        title=f"AxiTools Status — {guild.name}",
        description=description,
        color=BRAND_COLOUR,
    )
    for status in statuses:
        lines = [
            f"{STATE_ICONS.get(f.state, '❓')} **{f.label}:** {f.value}"
            for f in status.fields
        ]
        embed.add_field(
            name=status.title, value="\n".join(lines) or "—", inline=False
        )
    return embed


class StatusView(discord.ui.View):
    def __init__(self, statuses: list[ConfigStatus]) -> None:
        super().__init__(timeout=120)
        seen: set[str] = set()
        for status in statuses:
            has_issues = any(f.state != "ok" for f in status.fields)
            if (
                status.setup_command
                and has_issues
                and status.setup_command not in seen
            ):
                seen.add(status.setup_command)
                self._add_hint_button(status.setup_command)

    def _add_hint_button(self, command: str) -> None:
        button = discord.ui.Button(
            label=command, style=discord.ButtonStyle.secondary
        )

        async def callback(interaction: discord.Interaction) -> None:
            await interaction.response.send_message(
                f"Run `{command}` to configure this.", ephemeral=True
            )

        button.callback = callback
        self.add_item(button)
```

- [ ] **Step 6: Add `/status` command to `ConfigCog`**

Add this method to `ConfigCog` (after `config_command`):

```python
@app_commands.command(
    name="status",
    description="Show the current AxiTools configuration for this server.",
)
async def status_command(self, interaction: discord.Interaction) -> None:
    if not interaction.guild:
        await interaction.response.send_message(
            "This command can only be used inside a server.", ephemeral=True
        )
        return
    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            "Unable to resolve your server membership.", ephemeral=True
        )
        return
    if not self.bot.is_authorised(
        interaction.guild,
        interaction.user,
        permissions=getattr(interaction, "permissions", None),
    ):
        await interaction.response.send_message(
            "You do not have permission to view AxiTools status.", ephemeral=True
        )
        return

    statuses: list[ConfigStatus] = []
    for cog in self.bot.cogs.values():
        if hasattr(cog, "get_config_status"):
            try:
                status = await cog.get_config_status(interaction.guild)
                statuses.append(status)
            except Exception:
                LOGGER.exception(
                    "Error getting config status from cog %s", type(cog).__name__
                )

    embed = _build_status_embed(interaction.guild, statuses)
    view = StatusView(statuses)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
python -m pytest tests/test_cogs_config.py -v
```

Expected: all tests PASS

- [ ] **Step 8: Run full test suite**

```bash
python -m pytest -v
```

Expected: all tests PASS

- [ ] **Step 9: Commit**

```bash
git add axitools/cogs/config.py tests/test_cogs_config.py
git commit -m "feat: add /status command with unified per-guild configuration view"
```
