"""Query parsing and AI schema helpers for member search."""
from __future__ import annotations

import re
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .member_filter import BlanketCondition

# ---------------------------------------------------------------------------
# Constants (mirrored from SelectCog class attributes)
# ---------------------------------------------------------------------------

BLANKET_DEFAULT_FIELDS: Tuple[str, ...] = (
    "discord_guild_id",
    "api_keys",
    "api_key",
    "user",
    "user_id",
    "account_name",
    "permission",
    "guild_id",
    "character_name",
    "created_at",
    "updated_at",
)

BLANKET_FIELD_ALIASES: Dict[str, str] = {
    "user": "user",
    "users": "user",
    "discord_user": "user",
    "discord_name": "discord_name",
    "discord_guild_id": "discord_guild_id",
    "user_id": "user_id",
    "api_keys": "api_keys",
    "api_key": "api_keys",
    "api_key_name": "api_keys",
    "key_name": "api_keys",
    "api_key_value": "api_key",
    "api_keys.key": "api_key",
    "api_keys.name": "api_keys",
    "api_keys.account_name": "account_name",
    "api_keys.permissions": "permission",
    "api_keys.guild_ids": "guild_id",
    "api_keys.characters": "character_name",
    "api_keys.created_at": "created_at",
    "api_keys.updated_at": "updated_at",
    "api_keys.user_id": "user_id",
    "api_keys.discord_guild_id": "discord_guild_id",
    "account": "account_name",
    "account_name": "account_name",
    "character": "character_name",
    "character_name": "character_name",
    "guild": "guild_id",
    "guild_id": "guild_id",
    "permission": "permission",
    "permissions": "permission",
    "created_at": "created_at",
    "updated_at": "updated_at",
}

BLANKET_FIELD_LABELS: Dict[str, str] = {
    "discord_guild_id": "Discord Guild ID",
    "user": "User",
    "discord_name": "Discord Name",
    "user_id": "User ID",
    "api_keys": "API Key Name",
    "api_key": "API Key",
    "account_name": "Account Name",
    "character_name": "Character Names",
    "guild_id": "Guild IDs",
    "permission": "Permissions",
    "created_at": "Created At",
    "updated_at": "Updated At",
}

AI_FORBIDDEN_KEYWORDS: Tuple[str, ...] = (
    "update",
    "delete",
    "insert",
    "drop",
    "alter",
    "create",
    "truncate",
    "grant",
    "revoke",
)

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _normalise_blanket_field(raw: str) -> Optional[str]:
    value = raw.strip().casefold()
    if not value:
        return None
    return BLANKET_FIELD_ALIASES.get(value)


def _strip_quotes(value: str) -> str:
    cleaned = value.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
        return cleaned[1:-1]
    return cleaned


def _prompt_requests_full_rows(prompt: str) -> bool:
    lowered = prompt.casefold()
    markers = (
        "full row",
        "full rows",
        "all fields",
        "all columns",
        "complete row",
        "everything",
        "entire row",
    )
    return any(marker in lowered for marker in markers)


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def parse_blanket_query(
    query: str,
) -> Tuple[List[str], List[BlanketCondition]]:
    """Parse a blanket SELECT query string.

    Returns a tuple of (selected_fields, conditions).
    Raises ValueError for invalid queries.
    """
    cleaned = query.strip()
    match = re.match(r"^\s*select\s+(.*)$", cleaned, flags=re.IGNORECASE)
    if not match:
        raise ValueError("Query must start with SELECT.")

    remainder = match.group(1).strip()
    where_clause = ""
    where_match = re.search(r"\s+where\s+", remainder, flags=re.IGNORECASE)
    if where_match:
        where_clause = remainder[where_match.end():].strip()
        remainder = remainder[: where_match.start()].strip()
    if not remainder:
        raise ValueError("SELECT fields are required.")

    from_match = re.search(r"\s+from\s+", remainder, flags=re.IGNORECASE)
    select_part = remainder
    if from_match:
        select_part = remainder[: from_match.start()].strip()
        from_part = remainder[from_match.end():].strip()
        if not from_part:
            raise ValueError("FROM clause was provided without table names.")

    raw_fields = [part.strip() for part in select_part.split(",") if part.strip()]
    if not raw_fields:
        raise ValueError("At least one SELECT field is required.")

    selected_fields: List[str] = []
    if len(raw_fields) == 1 and raw_fields[0] == "*":
        selected_fields = list(BLANKET_DEFAULT_FIELDS)
    else:
        for token in raw_fields:
            normalised = _normalise_blanket_field(token)
            if not normalised:
                raise ValueError(f"Unsupported SELECT field: `{token}`.")
            if normalised not in selected_fields:
                selected_fields.append(normalised)

    conditions: List[BlanketCondition] = []
    if where_clause:
        parts = [
            part.strip()
            for part in re.split(r"\s+and\s+", where_clause, flags=re.IGNORECASE)
            if part.strip()
        ]
        for part in parts:
            cond_match = re.match(
                r"^\s*([a-zA-Z_][\w.]*)\s*(==|=|!=|~=)\s*(.+?)\s*$",
                part,
            )
            if not cond_match:
                raise ValueError(
                    "Invalid WHERE condition. Use syntax like `account_name == Example.1234`."
                )
            raw_field, operator, raw_value = cond_match.groups()
            field = _normalise_blanket_field(raw_field)
            if not field:
                raise ValueError(f"Unsupported WHERE field: `{raw_field}`.")
            value = _strip_quotes(raw_value)
            if not value:
                raise ValueError(f"Condition value for `{raw_field}` cannot be empty.")
            normalised_operator = "==" if operator == "=" else operator
            conditions.append(
                BlanketCondition(field=field, operator=normalised_operator, value=value)
            )
    return selected_fields, conditions


def is_read_only_select_query_text(query: str) -> bool:
    """Return True if *query* is a non-empty read-only SELECT statement."""
    cleaned = query.strip()
    if not cleaned:
        return False
    if not re.match(r"^\s*select\b", cleaned, flags=re.IGNORECASE):
        return False
    lowered = cleaned.casefold()
    return not any(
        re.search(rf"\b{re.escape(keyword)}\b", lowered)
        for keyword in AI_FORBIDDEN_KEYWORDS
    )


def ai_response_text(payload: Mapping[str, object]) -> str:
    """Extract the text content from an AI response payload.

    Handles Responses API, Chat Completions, and deep-collect fallback formats.
    """

    def _collect_text(value: object, *, out: List[str]) -> None:
        if isinstance(value, str):
            text = value.strip()
            if text:
                out.append(text)
            return
        if isinstance(value, list):
            for item in value:
                _collect_text(item, out=out)
            return
        if isinstance(value, dict):
            for key, nested in value.items():
                if key in {"text", "output_text", "content"}:
                    _collect_text(nested, out=out)

    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()
    if isinstance(output_text, list):
        parts: List[str] = []
        for item in output_text:
            if isinstance(item, str) and item.strip():
                parts.append(item.strip())
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        if parts:
            return "\n".join(parts).strip()

    output = payload.get("output")
    if not isinstance(output, list):
        output = []
    chunks: List[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                chunks.append(text.strip())
            output_text_value = part.get("output_text")
            if isinstance(output_text_value, str) and output_text_value.strip():
                chunks.append(output_text_value.strip())
    if chunks:
        return "\n".join(chunks).strip()

    # Compatibility fallback for Chat Completions-like payloads.
    choices = payload.get("choices")
    if isinstance(choices, list):
        choice = choices[0] if choices else None
        if isinstance(choice, dict):
            message = choice.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()
                if isinstance(content, list):
                    fallback_parts: List[str] = []
                    for part in content:
                        if isinstance(part, dict):
                            text = part.get("text")
                            if isinstance(text, str) and text.strip():
                                fallback_parts.append(text.strip())
                    if fallback_parts:
                        return "\n".join(fallback_parts).strip()
    deep_chunks: List[str] = []
    _collect_text(payload, out=deep_chunks)
    for chunk in deep_chunks:
        if chunk and "select" in chunk.casefold():
            return chunk
    return "\n".join(deep_chunks).strip()


def extract_select_statement(text: str) -> str:
    """Extract a SELECT statement from AI-generated text, handling code fences."""
    cleaned = text.strip()
    if not cleaned:
        return ""

    if "```" in cleaned:
        blocks = re.findall(r"```(?:\w+)?\s*(.*?)```", cleaned, flags=re.DOTALL)
        for block in blocks:
            candidate = block.strip()
            if re.match(r"^\s*select\b", candidate, flags=re.IGNORECASE):
                return candidate

    match = re.search(r"(select\b.+)", cleaned, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return cleaned
    candidate = match.group(1).strip()
    # Keep the first statement if extra prose trails the query.
    if ";" in candidate:
        candidate = candidate.split(";", 1)[0].strip()
    return candidate


def coerce_query_to_full_rows(*, prompt: str, query: str) -> str:
    """Rewrite *query* to ``SELECT *`` if the prompt asks for full rows."""
    if not _prompt_requests_full_rows(prompt):
        return query
    match = re.match(r"^\s*select\s+(.+)$", query, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return query
    tail = match.group(1).strip()
    where_match = re.search(r"\s+where\s+", tail, flags=re.IGNORECASE)
    if where_match:
        where_clause = tail[where_match.start():].strip()
        return f"SELECT * {where_clause}"
    return "SELECT *"


def build_ai_schema_context_from_records(
    records: Sequence[Tuple[int, int, object]],
    *,
    scope: str,
) -> str:
    """Build a schema context block for the AI prompt from scoped API key records."""
    account_names: List[str] = []
    key_names: List[str] = []
    guild_ids: List[str] = []
    permissions: List[str] = []

    def _append_unique(bucket: List[str], value: str, *, limit: int = 25) -> None:
        cleaned = value.strip()
        if not cleaned or cleaned in bucket:
            return
        if len(bucket) >= limit:
            return
        bucket.append(cleaned)

    for _guild_id, _user_id, record in records:
        _append_unique(key_names, record.name)  # type: ignore[union-attr]
        _append_unique(account_names, record.account_name)  # type: ignore[union-attr]
        for gid in record.guild_ids or []:  # type: ignore[union-attr]
            _append_unique(guild_ids, gid)
        for perm in record.permissions or []:  # type: ignore[union-attr]
            _append_unique(permissions, perm)

    lines = [
        f"- scope: {scope}",
        f"- records_in_scope: {len(records)}",
        "- fields:",
        "  - discord_guild_id: Discord server ID for the stored key row",
        "  - user: Discord display label",
        "  - discord_name: Discord username",
        "  - user_id: Discord ID string",
        "  - api_keys: stored key name",
        "  - api_key: full API key value",
        "  - account_name: GW2 account name (e.g. Name.1234)",
        "  - character_name: list of GW2 character names",
        "  - guild_id: list of GW2 guild UUIDs",
        "  - permission: list of GW2 API key permissions",
        "  - created_at: row creation timestamp",
        "  - updated_at: row update timestamp",
        "- operator_notes:",
        "  - use == for exact value matches",
        "  - use ~= for partial/contains matches",
        "  - combine filters with AND only",
        f"- sample_account_names: {', '.join(account_names) if account_names else '(none)'}",
        f"- sample_key_names: {', '.join(key_names) if key_names else '(none)'}",
        f"- sample_guild_ids: {', '.join(guild_ids) if guild_ids else '(none)'}",
        f"- sample_permissions: {', '.join(permissions) if permissions else '(none)'}",
    ]
    return "\n".join(lines)
