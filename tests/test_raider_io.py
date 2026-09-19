"""Raider.IO gear import — pure-transform tests (no network).

Exercises `character_from_raider_io` against a recorded-shape payload with a
stub stat resolver, so the field mapping is pinned without hitting the API.
The shape mirrors a real `…/characters/profile?fields=gear` response (per-slot
item_id / item_level / bonuses / gems / enchants; top-level class / race /
active_spec_name / gear.item_level_equipped).
"""

from __future__ import annotations

from simf.io.raider_io import character_from_raider_io

# Stub resolver: stand-in for item_db.resolve_equipped_stats (which is
# Wowhead-backed / networked). The transform must copy these onto char_data.
_STATS = {
    "strength": 2500,
    "stamina": 50_000,
    "armor_from_gear": 6_000,
    "haste_rating": 1_100,
    "versatility_rating": 1_200,
    "shield_armor": 950,
}


def _resolve_stub(equipped):
    return dict(_STATS)


def _payload(class_name="Warrior", spec="Protection", race="Earthen", items=None):
    if items is None:
        items = {
            "head": {
                "item_id": 49819,
                "item_level": 289,
                "bonuses": [13440, 6652],
                "gems": [240894],
                "enchants": [7961],
            },
            "neck": {
                "item_id": 50228,
                "item_level": 289,
                "bonuses": [13440],
                "gems": [240983],
                "enchants": [],
            },
            "mainhand": {"item_id": 11111, "item_level": 290, "bonuses": [1], "enchant": 6666},
            "offhand": {"item_id": 22222, "item_level": 288, "bonuses": []},
            "shirt": {"item_id": 0},  # empty slot — must be dropped
        }
    return {
        "name": "Brutoh",
        "race": race,
        "class": class_name,
        "active_spec_name": spec,
        "region": "eu",
        "realm": "Uldum",
        "gear": {"item_level_equipped": 289, "items": items},
    }


def test_transform_builds_hydrate_result():
    res = character_from_raider_io(_payload(), resolve_stats_fn=_resolve_stub)
    assert res is not None
    assert res.source == "raider_io"
    assert res.char_data["class_spec"] == "protection_warrior"
    assert res.char_data["race"] == "earthen"
    assert res.char_data["name"] == "Brutoh"
    assert res.char_data["region"] == "EU"
    assert res.char_data["server"] == "Uldum"
    # Talents default to the spec loadout (mirrors hydrate).
    assert res.char_data["talents"]  # non-empty


def test_stats_copied_from_resolver_including_shield_armor():
    """Resolver totals land on char_data, on top of the resolver-estimated
    corrections (base level-90 stats + recognized gem/enchant credit —
    2026-07-11) every Raider.IO load now applies. The default payload's own
    head gem (Flawless Versatile Peridot: +16 haste / +7 vers) and neck meta
    gem (Indecipherable Eversong Diamond: +32 primary) are REAL catalog
    entries, so their credit is expected here, not incidental noise."""
    from simf.core.constants import load_constants

    sc = load_constants()["stat_conversion"]
    res = character_from_raider_io(_payload(), resolve_stats_fn=_resolve_stub)
    assert res.char_data["strength"] == _STATS["strength"] + sc["paste_base_primary"] + 32
    assert res.char_data["stamina"] == _STATS["stamina"] + sc["paste_base_stamina"]
    assert res.char_data["armor_from_gear"] == _STATS["armor_from_gear"]
    assert res.char_data["haste_rating"] == _STATS["haste_rating"] + 16
    assert res.char_data["versatility_rating"] == _STATS["versatility_rating"] + 7
    # shield_armor specifically — the off-hand-derived block input, untouched
    # by the gem/enchant/base-stat corrections above.
    assert res.char_data["shield_armor"] == 950


def test_weapon_slot_vocabulary_mapped():
    res = character_from_raider_io(_payload(), resolve_stats_fn=_resolve_stub)
    assert "main_hand" in res.equipped
    assert "off_hand" in res.equipped
    assert "mainhand" not in res.equipped
    assert "offhand" not in res.equipped


def test_item_spec_fields_mapped():
    res = character_from_raider_io(_payload(), resolve_stats_fn=_resolve_stub)
    head = res.equipped["head"]
    assert head.item_id == 49819
    assert head.ilvl == 289
    assert head.bonus_ids == [13440, 6652]
    assert head.gem_ids == [240894]
    assert head.enchant_id == 7961
    # mainhand uses the singular `enchant` key, not `enchants`.
    assert res.equipped["main_hand"].enchant_id == 6666
    # offhand has no enchant → None.
    assert res.equipped["off_hand"].enchant_id is None


def test_empty_slot_dropped():
    res = character_from_raider_io(_payload(), resolve_stats_fn=_resolve_stub)
    assert "shirt" not in res.equipped  # item_id 0 → skipped


def test_non_tank_spec_returns_none():
    assert character_from_raider_io(_payload(spec="Arms"), resolve_stats_fn=_resolve_stub) is None
    assert (
        character_from_raider_io(
            _payload(class_name="Mage", spec="Frost"), resolve_stats_fn=_resolve_stub
        )
        is None
    )


def test_all_six_tank_specs_resolve():
    cases = {
        ("Paladin", "Protection"): "protection_paladin",
        ("Death Knight", "Blood"): "blood_death_knight",
        ("Druid", "Guardian"): "guardian_druid",
        ("Monk", "Brewmaster"): "brewmaster_monk",
        ("Demon Hunter", "Vengeance"): "vengeance_demon_hunter",
        ("Warrior", "Protection"): "protection_warrior",
    }
    for (cls, spec), expected in cases.items():
        res = character_from_raider_io(
            _payload(class_name=cls, spec=spec), resolve_stats_fn=_resolve_stub
        )
        assert res is not None and res.char_data["class_spec"] == expected


def test_agility_tank_with_no_strength_still_loads_as_character():
    """Regression (live crash on the public instance, 2026-06-29): a Guardian
    (agility primary) resolves with strength 0, and the online resolver drops
    zero-valued stats (``and val``), so char_data legitimately omits
    ``strength``. ``Character.from_dict`` MUST still load it (strength defaults
    to 0) — before the fix this raised ``TypeError: missing required argument:
    'strength'`` and 500'd the Gear surface for any Druid looked up by name."""
    from simf.core.character import Character

    def _agi_only(equipped):
        # No `strength` key at all — exactly what the resolver returns for a Druid.
        return {"agility": 5200, "stamina": 16570, "armor_from_gear": 789, "haste_rating": 900}

    res = character_from_raider_io(
        _payload(class_name="Druid", spec="Guardian"), resolve_stats_fn=_agi_only
    )
    assert res is not None
    assert "strength" not in res.char_data  # the resolver legitimately omitted it
    char = Character.from_dict(res.char_data)  # must NOT raise
    assert char.class_spec == "guardian_druid"
    # agility gets the level-90 base primary (2026-07-11 fix) PLUS the default
    # payload's neck meta gem (Indecipherable Eversong Diamond, primary: +32,
    # resolved onto agility for this agility-primary spec) — strength stays 0,
    # nothing resolves onto the tank's non-primary stat.
    from simf.core.constants import load_constants

    sc = load_constants()["stat_conversion"]
    assert char.strength == 0
    assert char.agility == 5200 + sc["paste_base_primary"] + 32


def test_all_tank_specs_import_even_when_resolver_returns_nothing():
    """Regression (public-instance crash, 2026-06-29): when item_db is OFFLINE
    (the public box) or an item isn't seed-cached, the stat resolver returns
    {} / a partial dict and the copy loop skips zero/absent stats — leaving
    char_data short a REQUIRED Character field (stamina / armor_from_gear) and
    500-ing the Gear surface. EVERY modeled tank spec must still import (the
    boundary backfills required fields to 0), for both an empty and a partial
    resolver. A degraded 0-stat import beats a crashed surface."""
    from simf.core.character import Character

    cases = {
        ("Warrior", "Protection"): "protection_warrior",
        ("Paladin", "Protection"): "protection_paladin",
        ("Death Knight", "Blood"): "blood_death_knight",
        ("Druid", "Guardian"): "guardian_druid",
        ("Monk", "Brewmaster"): "brewmaster_monk",
        ("Demon Hunter", "Vengeance"): "vengeance_demon_hunter",
    }
    for resolver in (lambda e: {}, lambda e: {"stamina": 16000}):  # empty + partial
        for (cls, spec), expected in cases.items():
            res = character_from_raider_io(
                _payload(class_name=cls, spec=spec), resolve_stats_fn=resolver
            )
            assert res is not None and res.char_data["class_spec"] == expected
            char = Character.from_dict(res.char_data)  # must NOT raise
            assert char.class_spec == expected
            # required fields are always present (defaulted to 0 when unresolved)
            assert char.stamina >= 0 and char.armor_from_gear >= 0


def test_error_payload_returns_none():
    assert (
        character_from_raider_io(
            {"error": "not found", "message": "..."}, resolve_stats_fn=_resolve_stub
        )
        is None
    )
    assert character_from_raider_io({}, resolve_stats_fn=_resolve_stub) is None


def test_empty_gear_returns_none():
    p = _payload(items={"shirt": {"item_id": 0}})  # only an empty slot
    assert character_from_raider_io(p, resolve_stats_fn=_resolve_stub) is None


# ─── resolver-estimated corrections shared with ui/helpers/simc_load.py's
# resolver-fallback branch (2026-07-11) — closes 3 gaps confirmed against
# that already-fixed sibling path: gem/enchant crediting, level-90 base
# stamina/primary, and Guardian's stamina_in_caster_form flag. Raider.IO is
# the DEFAULT path for a cold public visitor (ui/load.py forces
# source="raiderio" in public mode), so every one of these silently zeroed
# out gems/enchants and undercounted HP/primary on that path specifically.


def test_raider_io_credits_recognized_gem_and_enchant_stats():
    """A gemmed + enchanted item's OWN stats must land on char_data — the
    resolver stub below deliberately returns a stat dict that carries NEITHER
    the gem's mastery/versatility nor the enchant's haste, isolating the
    correction from the resolver's own per-item fetch (mirrors
    test_load_from_simc_folds_gem_and_enchant_stats_into_resolver_estimate)."""
    items = {
        "finger1": {"item_id": 251115, "enchants": [8025], "gems": [240918]},
    }
    p = _payload(items=items)

    def resolver(equipped):
        return {"stamina": 995}

    res = character_from_raider_io(p, resolve_stats_fn=resolver)
    assert res is not None
    # Gem 240918 (Flawless Masterful Lapis): versatility_rating 16, mastery_rating 7.
    assert res.char_data["versatility_rating"] == 16
    assert res.char_data["mastery_rating"] == 7
    # Enchant 8025 (Silvermoon's Alacrity): haste_rating 29.
    assert res.char_data["haste_rating"] == 29


def test_raider_io_adds_level_90_base_character_stats():
    """A bare per-item resolve is GEAR-ONLY — the level-90 base stamina/
    primary every real character carries before gear must be added on top, or
    a Raider.IO-loaded character is ~25% short on HP and ~half its primary
    stat (same gap the /simc-paste resolver fallback already had fixed)."""
    from simf.core.constants import load_constants

    sc = load_constants()["stat_conversion"]
    items = {"head": {"item_id": 1}}  # no gems/enchants — isolates base-stat addition
    p = _payload(items=items)

    def resolver(equipped):
        return {"strength": 2000, "stamina": 30000}

    res = character_from_raider_io(p, resolve_stats_fn=resolver)
    assert res is not None
    assert res.char_data["strength"] == 2000 + sc["paste_base_primary"]
    assert res.char_data["stamina"] == 30000 + sc["paste_base_stamina"]


def test_raider_io_guardian_load_sets_caster_form_flag():
    """A Guardian Druid loaded via Raider.IO must get
    stamina_in_caster_form=True so Character.max_hp() applies Bear Form's
    +40% HP multiplier — resolver-derived stamina is the out-of-form value.
    Before the fix this was never set on this path, silently losing ~29% max
    HP on every Raider.IO-loaded Guardian."""
    items = {"head": {"item_id": 1}}
    p = _payload(class_name="Druid", spec="Guardian", items=items)

    def resolver(equipped):
        return {"agility": 3000, "stamina": 20000}

    res = character_from_raider_io(p, resolve_stats_fn=resolver)
    assert res is not None
    assert res.char_data["stamina_in_caster_form"] is True


def test_raider_io_non_guardian_load_leaves_caster_form_flag_unset():
    items = {"head": {"item_id": 1}}
    p = _payload(class_name="Warrior", spec="Protection", items=items)

    def resolver(equipped):
        return {"strength": 3000, "stamina": 20000}

    res = character_from_raider_io(p, resolve_stats_fn=resolver)
    assert res is not None
    assert "stamina_in_caster_form" not in res.char_data


def test_raider_io_fully_offline_resolver_stays_undegraded_marker_intact():
    """When the resolver returns {} (item_db fully OFFLINE — the public-box
    scenario the backfill loop above already handles), the resolver-estimated
    corrections must NOT fire: adding the base stamina/primary on top of a
    genuinely-unresolved 0 would make Character.is_degraded() miss a real
    "couldn't read your gear" case, since it only checks stamina<=0 /
    (strength<=0 and agility<=0)."""
    items = {"head": {"item_id": 1}}
    p = _payload(items=items)

    res = character_from_raider_io(p, resolve_stats_fn=lambda equipped: {})
    assert res is not None
    assert res.char_data["stamina"] == 0
    assert res.char_data.get("strength", 0) == 0
    assert res.char_data.get("agility", 0) == 0
