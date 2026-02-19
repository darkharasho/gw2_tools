import pytest

from gw2_tools_bot.cogs.select import SelectCog, _BlanketCondition
from gw2_tools_bot.storage import ApiKeyRecord


def test_parse_blanket_query_basic():
    fields, conditions = SelectCog._parse_blanket_query(
        "SELECT user, api_keys WHERE account_name == blah.123 AND character_name == 'blahblah'"
    )
    assert fields == ["user", "api_keys"]
    assert len(conditions) == 2
    assert conditions[0] == _BlanketCondition(
        field="account_name", operator="==", value="blah.123"
    )
    assert conditions[1] == _BlanketCondition(
        field="character_name", operator="==", value="blahblah"
    )


def test_parse_blanket_query_wildcard_and_from():
    fields, conditions = SelectCog._parse_blanket_query(
        "SELECT * FROM api_keys WHERE permission ~= guilds"
    )
    assert fields == list(SelectCog.BLANKET_DEFAULT_FIELDS)
    assert conditions == [
        _BlanketCondition(field="permission", operator="~=", value="guilds")
    ]


def test_parse_blanket_query_invalid_field():
    with pytest.raises(ValueError):
        SelectCog._parse_blanket_query("SELECT super_secret WHERE account_name == x")


def test_blanket_condition_matches_list_values():
    row = {"character_name": ["One", "Two"], "account_name": "Test.1234"}
    assert SelectCog._blanket_condition_matches(
        _BlanketCondition(field="character_name", operator="==", value="two"),
        row,
    )
    assert SelectCog._blanket_condition_matches(
        _BlanketCondition(field="character_name", operator="~=", value="on"),
        row,
    )
    assert not SelectCog._blanket_condition_matches(
        _BlanketCondition(field="character_name", operator="!=", value="two"),
        row,
    )


def test_is_read_only_select_query_text():
    assert SelectCog._is_read_only_select_query_text(
        "SELECT user WHERE account_name == Test.1234"
    )
    assert not SelectCog._is_read_only_select_query_text(
        "UPDATE api_keys SET name = 'x'"
    )
    assert not SelectCog._is_read_only_select_query_text(
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
        SelectCog._ai_response_text(payload)
        == "SELECT user, api_keys WHERE account_name == ondria.1592"
    )


def test_extract_select_statement_from_code_fence():
    text = "Here you go:\n```sql\nSELECT user, api_keys WHERE account_name == ondria.1592\n```"
    assert (
        SelectCog._extract_select_statement(text)
        == "SELECT user, api_keys WHERE account_name == ondria.1592"
    )


def test_coerce_query_to_full_rows():
    prompt = "show full rows for ondria.1592"
    query = "SELECT user, api_keys WHERE account_name == ondria.1592"
    assert (
        SelectCog._coerce_query_to_full_rows(prompt=prompt, query=query)
        == "SELECT * WHERE account_name == ondria.1592"
    )


def test_build_ai_schema_context_from_records():
    record = ApiKeyRecord(
        name="Main Key",
        key="secret",
        account_name="Ondria.1592",
        permissions=["account", "guilds"],
        guild_ids=["abcd-1234"],
        characters=["One"],
    )
    context = SelectCog._build_ai_schema_context_from_records(
        [(1, 2, record)],
        scope="mine",
    )
    assert "scope: mine" in context
    assert "sample_account_names: Ondria.1592" in context
    assert "sample_key_names: Main Key" in context
    assert "sample_guild_ids: abcd-1234" in context


def test_parse_blanket_query_table_alias_field():
    fields, conditions = SelectCog._parse_blanket_query(
        "SELECT api_keys.name, api_keys.created_at WHERE api_keys.account_name == ondria.1592"
    )
    assert fields == ["api_keys", "created_at"]
    assert conditions == [
        _BlanketCondition(
            field="account_name",
            operator="==",
            value="ondria.1592",
        )
    ]
