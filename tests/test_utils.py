
import pytest
import discord
from unittest.mock import Mock
from gw2_tools_bot import utils, constants

def test_resolve_profession_valid():
    prof, spec = utils.resolve_profession("Guardian")
    assert prof == "Guardian"
    assert spec is None

    prof, spec = utils.resolve_profession("Firebrand")
    assert prof == "Guardian"
    assert spec == "Firebrand"

def test_resolve_profession_invalid():
    with pytest.raises(ValueError):
        utils.resolve_profession("InvalidClass")

def test_build_class_display():
    assert utils.build_class_display("Guardian", None) == "Guardian"
    assert utils.build_class_display("Guardian", "Firebrand") == "Firebrand (Guardian)"

def test_get_icon_and_color():
    # Test base profession
    icon, color = utils.get_icon_and_color("Guardian")
    assert "Guardian.png" in icon
    assert color == constants.PROFESSIONS["Guardian"].color

    # Test specialization
    icon, color = utils.get_icon_and_color("Firebrand")
    assert "firebrand.png" in icon.lower()
    assert color == constants.PROFESSIONS["Guardian"].color
