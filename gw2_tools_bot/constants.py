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
    1001: "Anvil Rock",
    1002: "Borlis Pass",
    1003: "Yak's Bend",
    1004: "Henge of Denravi",
    1005: "Maguuma",
    1006: "Sorrow's Furnace",
    1007: "Gate of Madness",
    1008: "Jade Quarry",
    1009: "Fort Aspenwood",
    1010: "Ehmry Bay",
    1011: "Stormbluff Isle",
    1012: "Darkhaven",
    1013: "Sanctum of Rall",
    1014: "Crystal Desert",
    1015: "Isle of Janthir",
    1016: "Sea of Sorrows",
    1017: "Tarnished Coast",
    1018: "Northern Shiverpeaks",
    1019: "Blackgate",
    1020: "Ferguson's Crossing",
    1021: "Dragonbrand",
    1022: "Kaineng",
    1023: "Devona's Rest",
    1024: "Eredon Terrace",
    2001: "Fissure of Woe",
    2002: "Desolation",
    2003: "Gandara",
    2004: "Blacktide",
    2005: "Ring of Fire",
    2006: "Underworld",
    2007: "Far Shiverpeaks",
    2008: "Whiteside Ridge",
    2009: "Ruins of Surmia",
    2010: "Seafarer's Rest",
    2011: "Vabbi",
    2012: "Piken Square",
    2013: "Aurora Glade",
    2014: "Gunnar's Hold",
    2101: "Jade Sea [FR]",
    2102: "Fort Ranik [FR]",
    2103: "Augury Rock [FR]",
    2104: "Vizunah Square [FR]",
    2105: "Arborstone [FR]",
    2201: "Kodash [DE]",
    2202: "Riverside [DE]",
    2203: "Elona Reach [DE]",
    2204: "Abaddon's Mouth [DE]",
    2205: "Drakkar Lake [DE]",
    2206: "Miller's Sound [DE]",
    2207: "Dzagonur [DE]",
    2301: "Baruch Bay [SP]",
}
