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
