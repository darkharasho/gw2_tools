"""Constant values used across the GW2 Tools bot."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

PACKAGE_ROOT = Path(__file__).resolve().parent
MEDIA_PATH = PACKAGE_ROOT.parent / "media"
CLASS_ICON_PATH = MEDIA_PATH / "gw2classicons"


@dataclass(frozen=True)
class Profession:
    """Metadata for a Guild Wars 2 profession."""

    name: str
    color: int
    icon_file: str

    @property
    def icon_path(self) -> Path:
        return CLASS_ICON_PATH / self.icon_file


@dataclass(frozen=True)
class Specialization:
    """Metadata for a Guild Wars 2 elite specialization."""

    name: str
    profession: str
    icon_file: Optional[str] = None

    def icon_path(self, professions: Dict[str, Profession]) -> Path:
        if self.icon_file:
            return CLASS_ICON_PATH / self.icon_file
        return professions[self.profession].icon_path


PROFESSIONS: Dict[str, Profession] = {
    "Elementalist": Profession("Elementalist", 0xF68A35, "Elementalist.png"),
    "Engineer": Profession("Engineer", 0xB77C34, "Engineer.png"),
    "Guardian": Profession("Guardian", 0x0C8FD6, "Guardian.png"),
    "Mesmer": Profession("Mesmer", 0xB46DFF, "Mesmer.png"),
    "Necromancer": Profession("Necromancer", 0x3A9D23, "Necromancer.png"),
    "Ranger": Profession("Ranger", 0x4B8E4B, "Ranger.png"),
    "Revenant": Profession("Revenant", 0x79236F, "Revenant_icon.png"),
    "Thief": Profession("Thief", 0xA02E2D, "Thief.png"),
    "Warrior": Profession("Warrior", 0xC7892B, "Warrior.png"),
}


SPECIALIZATIONS: Dict[str, Specialization] = {
    # Elementalist
    "Tempest": Specialization("Tempest", "Elementalist", "Tempest.png"),
    "Weaver": Specialization("Weaver", "Elementalist", "Weaver.png"),
    "Catalyst": Specialization("Catalyst", "Elementalist", "Catalyst.png"),
    "Evoker": Specialization("Evoker", "Elementalist", "Evoker.png"),
    # Engineer
    "Scrapper": Specialization("Scrapper", "Engineer", "Scrapper.png"),
    "Holosmith": Specialization("Holosmith", "Engineer", "Holosmith.png"),
    "Mechanist": Specialization("Mechanist", "Engineer", "Mechanist.png"),
    "Amalgam": Specialization("Amalgam", "Engineer", "Amalgam.png"),
    # Guardian
    "Dragonhunter": Specialization("Dragonhunter", "Guardian", "Dragonhunter.png"),
    "Firebrand": Specialization("Firebrand", "Guardian", "Firebrand.png"),
    "Willbender": Specialization("Willbender", "Guardian", "Willbender.png"),
    "Luminary": Specialization("Luminary", "Guardian", "Luminary.png"),
    # Mesmer
    "Chronomancer": Specialization("Chronomancer", "Mesmer", "Chronomancer.png"),
    "Mirage": Specialization("Mirage", "Mesmer", "Mirage.png"),
    "Virtuoso": Specialization("Virtuoso", "Mesmer", "Virtuoso.png"),
    "Troubadour": Specialization("Troubadour", "Mesmer", "Troubadour.png"),
    # Necromancer
    "Reaper": Specialization("Reaper", "Necromancer", "Reaper.png"),
    "Scourge": Specialization("Scourge", "Necromancer", "Scourge.png"),
    "Harbinger": Specialization("Harbinger", "Necromancer", "Harbinger.png"),
    "Ritualist": Specialization("Ritualist", "Necromancer", "Ritualist.png"),
    # Ranger
    "Druid": Specialization("Druid", "Ranger", "Druid.png"),
    "Soulbeast": Specialization("Soulbeast", "Ranger", "Soulbeast.png"),
    "Untamed": Specialization("Untamed", "Ranger", "Untamed.png"),
    "Galeshot": Specialization("Galeshot", "Ranger", "Galeshot.png"),
    # Revenant
    "Herald": Specialization("Herald", "Revenant", "Herald.png"),
    "Renegade": Specialization("Renegade", "Revenant", "Renegade.png"),
    "Vindicator": Specialization("Vindicator", "Revenant", "Vindicator.png"),
    "Conduit": Specialization("Conduit", "Revenant", "Conduit.png"),
    # Thief
    "Daredevil": Specialization("Daredevil", "Thief", "Daredevil.png"),
    "Deadeye": Specialization("Deadeye", "Thief", "Deadeye.png"),
    "Specter": Specialization("Specter", "Thief", "Specter.png"),
    "Antiquary": Specialization("Antiquary", "Thief", "Antiquary.png"),
    # Warrior
    "Berserker": Specialization("Berserker", "Warrior", "Berserker.png"),
    "Spellbreaker": Specialization("Spellbreaker", "Warrior", "Spellbreaker.png"),
    "Bladesworn": Specialization("Bladesworn", "Warrior", "Bladesworn.png"),
    "Paragon": Specialization("Paragon", "Warrior", "Paragon.png"),
}

# Convenience list that combines professions and specializations for command choices.
CLASS_CHOICES = sorted(
    list(PROFESSIONS.keys()) + list(SPECIALIZATIONS.keys())
)

# Manual Guild Wars 2 WvW server mapping (see reference settings list).
WVW_SERVER_NAMES: Dict[int, str] = {
    11001: "Moogooloo",
    11002: "Rall's Rest",
    11003: "Domain of Torment",
    11004: "Yohlon Haven",
    11005: "Tomb of Drascir",
    11006: "Hall of Judgment",
    11007: "Throne of Balthazar",
    11008: "Dwayna's Temple",
    11009: "Abaddon's Prison",
    11010: "Cathedral of Blood",
    11011: "Lutgardis Conservatory",
    11012: "Mosswood",
}

# Alliance roster sheet tab names keyed by WvW world id.
WVW_ALLIANCE_SHEET_TABS: Dict[int, str] = {
    11001: "Moogooloo",
    11002: "RR",
    11003: "DoT",
    11004: "YH",
    11005: "ToD",
    11006: "HoJ",
    11007: "ThroB",
    11008: "DT",
    11009: "AP",
    11010: "CoB",
    11011: "LC",
    11012: "Mosswood",
}
