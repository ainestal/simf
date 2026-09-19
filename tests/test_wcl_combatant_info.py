"""WCL CombatantInfo → Character — converter + adapter (no network).

Pins the WCL ``events(dataType: CombatantInfo)`` field mapping (validated
against a real Midnight report 2026-06-09) and that it feeds the *same*
COMBATANT_INFO → Character builder as the local-log path.
"""

from __future__ import annotations

import simf.io.wcl_api as wcl_api
from simf.io.combat_log import _COMBATANT_INFO_SLOT_ORDER
from simf.io.wcl_combatant_info import (
    character_from_wcl,
    combatant_info_full_from_wcl_event,
)


def _gear(n=18):
    """18 positional slots; head fully populated, shirt(3)+tabard(17) empty."""
    g = [{"id": 1000 + i, "itemLevel": 720} for i in range(n)]
    g[0] = {
        "id": 111,
        "itemLevel": 730,
        "bonusIDs": [6652, 1498],
        "gems": [{"id": 240894, "itemLevel": 610}],
        "permanentEnchant": 7961,
    }
    g[3] = {"id": 0}  # shirt empty
    g[17] = {"id": 0}  # tabard empty
    return g


def _event(spec_id=73, source_id=12):
    return {
        "sourceID": source_id,
        "specID": spec_id,
        "strength": 38_196,
        "agility": 1_200,
        "stamina": 36_516,
        "intellect": 50,
        "armor": 5_448,
        "dodge": 300,
        "parry": 900,
        "critMelee": 694,
        "critRanged": 99999,  # must NOT be used — guards against the positional crit bug
        "critSpell": 88888,
        "hasteMelee": 3_103,
        "mastery": 270,
        "versatilityDamageReduction": 443,
        "versatilityDamageDone": 443,
        "talentTree": [
            {"id": 112863, "rank": 1, "nodeID": 90950},
            {"id": 112864, "rank": 1, "nodeID": 90951},
        ],
        "gear": _gear(),
    }


# ── converter ────────────────────────────────────────────────────────────────


def test_stats_use_named_melee_fields():
    ci = combatant_info_full_from_wcl_event(_event())
    assert ci.stamina == 36_516
    assert ci.total_armor == 5_448
    assert ci.haste_rating == 3_103
    assert ci.mastery_rating == 270
    assert ci.versatility_rating == 443
    assert ci.parry_rating == 900
    # crit comes from critMelee — NOT critRanged/critSpell (the local
    # line-parser's positional read mis-sources those; we use the named field).
    assert ci.crit_rating == 694


def test_talent_ids_from_talent_tree():
    ci = combatant_info_full_from_wcl_event(_event())
    assert ci.talent_spell_ids == frozenset({112863, 112864})


def test_gear_positional_and_empty_preserved():
    ci = combatant_info_full_from_wcl_event(_event())
    assert len(ci.equipped) == 18
    assert ci.equipped[0].slot == _COMBATANT_INFO_SLOT_ORDER[0] == "head"
    assert ci.equipped[0].item_id == 111
    assert ci.equipped[0].ilvl == 730
    assert ci.equipped[0].bonus_ids == (6652, 1498)
    assert ci.equipped[0].gem_ids == (240894,)
    assert ci.equipped[0].enchant_id == 7961
    # weapon slots land where expected
    assert ci.equipped[15].slot == "main_hand"
    assert ci.equipped[16].slot == "off_hand"
    # empty slots preserved as id=0 (filtered later by _equipped_from_combatant_info)
    assert ci.equipped[3].item_id == 0


# ── adapter (character_from_wcl) ──────────────────────────────────────────────


def test_character_from_wcl_builds_result(monkeypatch):
    monkeypatch.setattr(wcl_api, "fetch_combatant_info_events", lambda c, f, t, **kw: [_event()])
    res = character_from_wcl("CODE", 1, "AnonPlayerX4-Realm-EU", token="tok")
    assert res is not None
    assert res.source == "wcl_combatant_info"
    assert res.char_data["class_spec"] == "protection_warrior"
    assert res.char_data["stamina"] == 36_516
    assert res.char_data["name"] == "AnonPlayerX4"
    assert res.char_data["region"] == "EU"
    # empty shirt/tabard filtered; 16 real items remain
    assert "head" in res.equipped and "main_hand" in res.equipped
    assert "shirt" not in res.equipped
    assert len(res.equipped) == 16


def test_picks_event_by_actor_id(monkeypatch):
    healer = _event(spec_id=270, source_id=5)  # Mistweaver — not a tank
    tank = _event(spec_id=73, source_id=12)
    monkeypatch.setattr(
        wcl_api, "fetch_combatant_info_events", lambda c, f, t, **kw: [healer, tank]
    )
    res = character_from_wcl("CODE", 1, "AnonPlayerX4", target_actor_id=12, token="tok")
    assert res.char_data["class_spec"] == "protection_warrior"


def test_falls_back_to_tank_spec_without_actor_id(monkeypatch):
    dps = _event(spec_id=251, source_id=5)  # Frost DK — not a tank
    tank = _event(spec_id=581, source_id=9)  # VDH
    monkeypatch.setattr(wcl_api, "fetch_combatant_info_events", lambda c, f, t, **kw: [dps, tank])
    res = character_from_wcl("CODE", 1, "Someone", token="tok")
    assert res.char_data["class_spec"] == "vengeance_demon_hunter"


def test_none_when_no_combatant_info(monkeypatch):
    monkeypatch.setattr(wcl_api, "fetch_combatant_info_events", lambda c, f, t, **kw: [])
    assert character_from_wcl("CODE", 1, "AnonPlayerX4", token="tok") is None


def test_none_when_no_tank_event(monkeypatch):
    healer = _event(spec_id=270, source_id=5)
    monkeypatch.setattr(wcl_api, "fetch_combatant_info_events", lambda c, f, t, **kw: [healer])
    assert character_from_wcl("CODE", 1, "AnonPlayerX4", token="tok") is None
