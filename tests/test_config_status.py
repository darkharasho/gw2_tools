from unittest.mock import MagicMock

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
    status = ConfigStatus(title="Builds", fields=fields, setup_command="/config")
    assert status.setup_command == "/config"
    assert len(status.fields) == 1


def test_config_status_field_labels():
    field = StatusField(label="Build channel", value="#builds", state="ok")
    assert field.label == "Build channel"
    assert field.value == "#builds"


def _make_mock_bot(config):
    bot = MagicMock()
    bot.get_config.return_value = config
    return bot


# ------------------------------------------------------------------
# BuildsCog
# ------------------------------------------------------------------

def test_builds_cog_status_ok():
    from axitools.cogs.builds import BuildsCog

    config = MagicMock()
    config.build_channel_id = 12345
    cog = BuildsCog.__new__(BuildsCog)
    cog.bot = _make_mock_bot(config)

    status = cog.get_config_status(guild_id=1)

    assert isinstance(status, ConfigStatus)
    assert status.title == "GW2 Builds"
    assert any(f.state == "ok" for f in status.fields)
    assert any("12345" in f.value for f in status.fields)


def test_builds_cog_status_missing():
    from axitools.cogs.builds import BuildsCog

    config = MagicMock()
    config.build_channel_id = None
    cog = BuildsCog.__new__(BuildsCog)
    cog.bot = _make_mock_bot(config)

    status = cog.get_config_status(guild_id=1)

    assert any(f.state == "missing" for f in status.fields)


# ------------------------------------------------------------------
# ArcDpsUpdatesCog
# ------------------------------------------------------------------

def test_arcdps_cog_status_ok():
    from axitools.cogs.arcdps import ArcDpsUpdatesCog

    config = MagicMock()
    config.arcdps_channel_id = 99999
    cog = ArcDpsUpdatesCog.__new__(ArcDpsUpdatesCog)
    cog.bot = _make_mock_bot(config)

    status = cog.get_config_status(guild_id=1)

    assert isinstance(status, ConfigStatus)
    assert status.title == "ArcDPS Updates"
    assert any(f.state == "ok" for f in status.fields)
    assert any("99999" in f.value for f in status.fields)


def test_arcdps_cog_status_missing():
    from axitools.cogs.arcdps import ArcDpsUpdatesCog

    config = MagicMock()
    config.arcdps_channel_id = None
    cog = ArcDpsUpdatesCog.__new__(ArcDpsUpdatesCog)
    cog.bot = _make_mock_bot(config)

    status = cog.get_config_status(guild_id=1)

    assert any(f.state == "missing" for f in status.fields)


# ------------------------------------------------------------------
# UpdateNotesCog
# ------------------------------------------------------------------

def test_update_notes_cog_status_ok():
    from axitools.cogs.update_notes import UpdateNotesCog

    config = MagicMock()
    config.update_notes_channel_id = 55555
    cog = UpdateNotesCog.__new__(UpdateNotesCog)
    cog.bot = _make_mock_bot(config)

    status = cog.get_config_status(guild_id=1)

    assert isinstance(status, ConfigStatus)
    assert status.title == "GW2 Update Notes"
    assert any(f.state == "ok" for f in status.fields)


def test_update_notes_cog_status_missing():
    from axitools.cogs.update_notes import UpdateNotesCog

    config = MagicMock()
    config.update_notes_channel_id = None
    cog = UpdateNotesCog.__new__(UpdateNotesCog)
    cog.bot = _make_mock_bot(config)

    status = cog.get_config_status(guild_id=1)

    assert any(f.state == "missing" for f in status.fields)


# ------------------------------------------------------------------
# RssFeedsCog
# ------------------------------------------------------------------

def test_rss_cog_status_ok():
    from axitools.cogs.rss import RssFeedsCog

    config = MagicMock()
    bot = _make_mock_bot(config)
    bot.storage.get_rss_feeds.return_value = [MagicMock(), MagicMock()]
    cog = RssFeedsCog.__new__(RssFeedsCog)
    cog.bot = bot

    status = cog.get_config_status(guild_id=1)

    assert isinstance(status, ConfigStatus)
    assert status.title == "RSS Feeds"
    assert any(f.state == "ok" for f in status.fields)
    assert any("2" in f.value for f in status.fields)


def test_rss_cog_status_missing():
    from axitools.cogs.rss import RssFeedsCog

    config = MagicMock()
    bot = _make_mock_bot(config)
    bot.storage.get_rss_feeds.return_value = []
    cog = RssFeedsCog.__new__(RssFeedsCog)
    cog.bot = bot

    status = cog.get_config_status(guild_id=1)

    assert any(f.state == "missing" for f in status.fields)


# ------------------------------------------------------------------
# CompCog
# ------------------------------------------------------------------

def test_comps_cog_status_ok():
    from axitools.cogs.comps import CompCog

    config = MagicMock()
    config.comp_schedules = [MagicMock(), MagicMock(), MagicMock()]
    cog = CompCog.__new__(CompCog)
    cog.bot = _make_mock_bot(config)

    status = cog.get_config_status(guild_id=1)

    assert isinstance(status, ConfigStatus)
    assert status.title == "Guild Compositions"
    assert any(f.state == "ok" for f in status.fields)
    assert any("3" in f.value for f in status.fields)


def test_comps_cog_status_missing():
    from axitools.cogs.comps import CompCog

    config = MagicMock()
    config.comp_schedules = []
    cog = CompCog.__new__(CompCog)
    cog.bot = _make_mock_bot(config)

    status = cog.get_config_status(guild_id=1)

    assert any(f.state == "missing" for f in status.fields)


# ------------------------------------------------------------------
# AccountsCog
# ------------------------------------------------------------------

def test_accounts_cog_status_ok():
    from axitools.cogs.accounts import AccountsCog

    config = MagicMock()
    bot = _make_mock_bot(config)
    bot.storage.count_api_keys.return_value = 5
    cog = AccountsCog.__new__(AccountsCog)
    cog.bot = bot

    status = cog.get_config_status(guild_id=1)

    assert isinstance(status, ConfigStatus)
    assert status.title == "GW2 Accounts"
    assert any(f.state == "ok" for f in status.fields)
    assert any("5" in f.value for f in status.fields)


def test_accounts_cog_status_missing():
    from axitools.cogs.accounts import AccountsCog

    config = MagicMock()
    bot = _make_mock_bot(config)
    bot.storage.count_api_keys.return_value = 0
    cog = AccountsCog.__new__(AccountsCog)
    cog.bot = bot

    status = cog.get_config_status(guild_id=1)

    assert any(f.state == "missing" for f in status.fields)


# ------------------------------------------------------------------
# AuditCog
# ------------------------------------------------------------------

def test_audit_cog_status_ok():
    from axitools.cogs.audit import AuditCog

    config = MagicMock()
    config.audit_channel_id = 11111
    config.audit_gw2_guild_id = "abc-123"
    cog = AuditCog.__new__(AuditCog)
    cog.bot = _make_mock_bot(config)

    status = cog.get_config_status(guild_id=1)

    assert isinstance(status, ConfigStatus)
    assert status.title == "Audit Logging"
    assert all(f.state == "ok" for f in status.fields)


def test_audit_cog_status_missing():
    from axitools.cogs.audit import AuditCog

    config = MagicMock()
    config.audit_channel_id = None
    config.audit_gw2_guild_id = None
    cog = AuditCog.__new__(AuditCog)
    cog.bot = _make_mock_bot(config)

    status = cog.get_config_status(guild_id=1)

    assert all(f.state == "missing" for f in status.fields)
