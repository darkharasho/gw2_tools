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
