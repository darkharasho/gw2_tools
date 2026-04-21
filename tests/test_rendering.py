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


def test_clean_markdown_normalises_bullets():
    result = clean_markdown("* item one\n\n+ item two")
    assert result == "- item one\n\n- item two"


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
