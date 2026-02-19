import pytest

from gw2_tools_bot.cogs.select import SelectCog, _BlanketCondition


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
