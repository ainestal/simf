"""WoW spec_id → (class_spec slug, role) lookup.

Spec IDs are stable across patches — Blizzard treats them as a public API
(WoW armory URLs, talent calculators, the `GetSpecializationInfo` Lua API).
Captured here so combat-log parsers can map `COMBATANT_INFO.spec_id` to
the simf-internal `class_spec` slug ("protection_warrior" etc.) and to
tank/healer/DPS role without re-deriving from class table each call.

Source: WoWHead spec list cross-referenced against WoW Midnight 12.0.5
COMBATANT_INFO events. Add new specs (e.g. future Hero specs that get
their own ID) by appending — the missing-spec path falls back to "dps"
which is the safest assumption.
"""

from __future__ import annotations

# Spec ID → simf internal slug. Slug format `{spec}_{class}` matches
# elsewhere in the codebase (death_analysis, simc_import, character).
SPEC_ID_TO_CLASS_SPEC: dict[int, str] = {
    # Death Knight
    250: "blood_death_knight",
    251: "frost_death_knight",
    252: "unholy_death_knight",
    # Demon Hunter
    577: "havoc_demon_hunter",
    581: "vengeance_demon_hunter",
    # Druid
    102: "balance_druid",
    103: "feral_druid",
    104: "guardian_druid",
    105: "restoration_druid",
    # Evoker
    1467: "devastation_evoker",
    1468: "preservation_evoker",
    1473: "augmentation_evoker",
    # Hunter
    253: "beast_mastery_hunter",
    254: "marksmanship_hunter",
    255: "survival_hunter",
    # Mage
    62: "arcane_mage",
    63: "fire_mage",
    64: "frost_mage",
    # Monk
    268: "brewmaster_monk",
    269: "windwalker_monk",
    270: "mistweaver_monk",
    # Paladin
    65: "holy_paladin",
    66: "protection_paladin",
    70: "retribution_paladin",
    # Priest
    256: "discipline_priest",
    257: "holy_priest",
    258: "shadow_priest",
    # Rogue
    259: "assassination_rogue",
    260: "outlaw_rogue",
    261: "subtlety_rogue",
    # Shaman
    262: "elemental_shaman",
    263: "enhancement_shaman",
    264: "restoration_shaman",
    # Warlock
    265: "affliction_warlock",
    266: "demonology_warlock",
    267: "destruction_warlock",
    # Warrior
    71: "arms_warrior",
    72: "fury_warrior",
    73: "protection_warrior",
}

TANK_SPEC_IDS: frozenset[int] = frozenset(
    {
        250,  # Blood DK
        581,  # Vengeance DH
        104,  # Guardian Druid
        268,  # Brewmaster Monk
        66,  # Protection Paladin
        73,  # Protection Warrior
    }
)

HEALER_SPEC_IDS: frozenset[int] = frozenset(
    {
        105,  # Restoration Druid
        1468,  # Preservation Evoker
        270,  # Mistweaver Monk
        65,  # Holy Paladin
        256,  # Discipline Priest
        257,  # Holy Priest
        264,  # Restoration Shaman
    }
)


def spec_to_role(spec_id: int) -> str:
    """Map a spec ID to "tank", "healer", or "dps".

    Unknown spec IDs return "dps" — safer than crashing or returning None,
    since a missing spec almost always means we're seeing a brand-new spec
    introduced in a patch we haven't updated for, and treating it as DPS
    is the conservative assumption (won't mis-route the tank slot).
    """
    if spec_id in TANK_SPEC_IDS:
        return "tank"
    if spec_id in HEALER_SPEC_IDS:
        return "healer"
    return "dps"


def class_spec_display_name(class_spec: str) -> str:
    """Render a slug like "protection_warrior" as "Protection Warrior"."""
    return " ".join(word.capitalize() for word in class_spec.split("_"))
