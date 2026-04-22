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
