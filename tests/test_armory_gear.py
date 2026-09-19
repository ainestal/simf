"""Blizzard Armory gear import — transform + happy path (no network).

The Blizzard `/equipment` path can't be live-verified here (no client
credentials on CI / the dev box), so these pin the documented response shape
with mocked payloads. The Raider.IO path remains the live-verified default.
"""

from __future__ import annotations

from simf.io import armory

# Documented `/equipment` shape: equipped_items[].item.id / .slot.type /
# .level.value / .bonus_list[] / .sockets[].item.id / .enchantments[].enchantment_id
_EQUIP = {
    "equipped_items": [
        {
            "slot": {"type": "HEAD"},
            "item": {"id": 111},
            "level": {"value": 289},
            "name": "Helm",
            "bonus_list": [6652, 1498],
            "sockets": [{"item": {"id": 240894}}],
            "enchantments": [{"enchantment_id": 7961, "enchantment_slot": {"type": "PERMANENT"}}],
        },
        {"slot": {"type": "MAIN_HAND"}, "item": {"id": 222}, "level": {"value": 290}},
        {"slot": {"type": "OFF_HAND"}, "item": {"id": 333}, "level": {"value": 288}},
        {"slot": {"type": "TABARD"}, "item": {"id": 0}},  # empty → dropped
        {"slot": {"type": "SOME_NEW_SLOT"}, "item": {"id": 999}},  # unknown → dropped
    ]
}


def test_equipped_from_blizzard_maps_fields():
    eq = armory._equipped_from_blizzard(_EQUIP)
    assert {"head", "main_hand", "off_hand"} <= set(eq)
    assert "tabard" not in eq  # item_id 0
    assert "some_new_slot" not in eq  # unknown slot type
    head = eq["head"]
    assert head.item_id == 111
    assert head.ilvl == 289
    assert head.bonus_ids == [6652, 1498]
    assert head.gem_ids == [240894]
    assert head.enchant_id == 7961


# Real `display_string` shapes, captured live 2026-07-01 (EU/Uldum/Brutoh) —
# see io/armory._enchant_display_name. Two distinct namings: a named scroll
# enchant ("Enchant <Slot> - <Name>") and a kit-style enchant that names
# itself by its stats instead (no "Enchant X - " prefix at all).
_NAMED_ENCHANT_RAW = (
    "Enchanted: Enchant Chest - Mark of the Worldsoul "
    "|A:Professions-ChatIcon-Quality-12-Tier2:20:20|a"
)
_KIT_ENCHANT_RAW = (
    "Enchanted: +41 Agility/Strength & +115 Stamina "
    "|A:Professions-ChatIcon-Quality-12-Tier2:20:20|a"
)


def test_enchant_display_name_strips_prefix_and_icon_markup():
    assert armory._enchant_display_name(_NAMED_ENCHANT_RAW) == "Mark of the Worldsoul"
    assert armory._enchant_display_name(_KIT_ENCHANT_RAW) == "+41 Agility/Strength & +115 Stamina"
    assert armory._enchant_display_name(None) is None
    assert armory._enchant_display_name("") is None


def test_equipped_from_blizzard_captures_enchant_name():
    equip = {
        "equipped_items": [
            {
                "slot": {"type": "CHEST"},
                "item": {"id": 249955},
                "enchantments": [
                    {
                        "enchantment_id": 7987,
                        "enchantment_slot": {"type": "PERMANENT"},
                        "display_string": _NAMED_ENCHANT_RAW,
                    }
                ],
            }
        ]
    }
    eq = armory._equipped_from_blizzard(equip)
    assert eq["chest"].enchant_id == 7987
    assert eq["chest"].enchant_name == "Mark of the Worldsoul"


def test_equipped_from_blizzard_enchant_name_absent_without_display_string():
    """The pre-existing fixture (`_EQUIP`'s head enchant) has no
    `display_string` — enchant_name must degrade to None, not raise."""
    eq = armory._equipped_from_blizzard(_EQUIP)
    assert eq["head"].enchant_id == 7961
    assert eq["head"].enchant_name is None


def test_equipped_from_blizzard_ignores_temporary_enchant_for_name():
    """A weapon can carry a TEMPORARY consumable oil/stone alongside its
    PERMANENT enchant (real shape, live-observed on a main-hand weapon) —
    only the permanent one's name should ever be captured."""
    equip = {
        "equipped_items": [
            {
                "slot": {"type": "MAIN_HAND"},
                "item": {"id": 258525},
                "enchantments": [
                    {
                        "enchantment_id": 7983,
                        "enchantment_slot": {"type": "PERMANENT"},
                        "display_string": (
                            "Enchanted: Enchant Weapon - Berserker's Rage "
                            "|A:Professions-ChatIcon-Quality-12-Tier2:20:20|a"
                        ),
                    },
                    {
                        "enchantment_id": 8051,
                        "enchantment_slot": {"type": "TEMPORARY"},
                        "display_string": "Thalassian Phoenix Oil |A:...|a",
                    },
                ],
            }
        ]
    }
    eq = armory._equipped_from_blizzard(equip)
    assert eq["main_hand"].enchant_id == 7983
    assert eq["main_hand"].enchant_name == "Berserker's Rage"


def test_equipped_from_blizzard_defensive_on_missing_fields():
    eq = armory._equipped_from_blizzard(
        {"equipped_items": [{"slot": {"type": "CHEST"}, "item": {"id": 42}}]}
    )
    assert eq["chest"].item_id == 42
    assert eq["chest"].bonus_ids == []
    assert eq["chest"].gem_ids == []
    assert eq["chest"].enchant_id is None
    assert eq["chest"].ilvl is None


def test_equipped_from_blizzard_empty_payload():
    assert armory._equipped_from_blizzard({}) == {}
    assert armory._equipped_from_blizzard({"equipped_items": []}) == {}


def test_fetch_character_gear_none_when_unconfigured(monkeypatch):
    monkeypatch.setattr(armory, "is_configured", lambda: False)
    assert armory.fetch_character_gear("Brutoh", "uldum", "eu") is None


def test_fetch_character_gear_rejects_non_tank(monkeypatch):
    monkeypatch.setattr(armory, "is_configured", lambda: True)
    monkeypatch.setattr(armory, "_get_token", lambda region: "tok")
    monkeypatch.setattr(
        armory,
        "fetch_character_public",
        lambda r, n, reg: {"name": "Magus", "class_spec": "frost_mage"},
    )
    assert armory.fetch_character_gear("Magus", "uldum", "eu") is None


def test_fetch_character_gear_happy_path(monkeypatch):
    monkeypatch.setattr(armory, "is_configured", lambda: True)
    monkeypatch.setattr(armory, "_get_token", lambda region: "tok")
    monkeypatch.setattr(
        armory,
        "fetch_character_public",
        lambda r, n, reg: {
            "name": "Brutoh",
            "race": "earthen",
            "class_spec": "protection_warrior",
            "strength": 2500,
            "stamina": 50_000,
            "armor_from_gear": 6_000,
        },
    )
    monkeypatch.setattr(
        armory, "_fetch_json", lambda url, *a, **k: _EQUIP if url.endswith("/equipment") else None
    )
    import simf.io.item_db as item_db

    monkeypatch.setattr(
        item_db, "resolve_equipped_stats", lambda items, region="eu": {"shield_armor": 950}
    )

    res = armory.fetch_character_gear("Brutoh", "uldum", "eu")
    assert res is not None
    assert res.source == "blizzard"
    assert res.char_data["class_spec"] == "protection_warrior"
    assert res.char_data["stamina"] == 50_000  # exact /statistics value, not re-derived
    assert res.char_data["shield_armor"] == 950  # resolved from off_hand
    assert res.char_data["talents"]  # default loadout assigned
    assert {"head", "main_hand", "off_hand"} <= set(res.equipped)


def test_profile_namespace_has_no_battle_net_suffix(monkeypatch):
    """Regression: the profile namespace must be `profile-{region}`, NOT
    `profile-{region}.battle.net`. The suffix returns 403 Forbidden — verified
    live 2026-06-09; the plain form returns 200. This guards the fetch path
    that the mocked happy-path test can't exercise (it stubs _fetch_json)."""
    monkeypatch.setattr(armory, "is_configured", lambda: True)
    monkeypatch.setattr(armory, "_get_token", lambda region: "tok")
    monkeypatch.setattr(
        armory,
        "fetch_character_public",
        lambda r, n, reg: {"name": "Brutoh", "class_spec": "protection_warrior"},
    )
    seen = {}

    def _capture(url, token, namespace, region):
        seen["ns"] = namespace
        return _EQUIP if url.endswith("/equipment") else None

    monkeypatch.setattr(armory, "_fetch_json", _capture)
    import simf.io.item_db as item_db

    monkeypatch.setattr(item_db, "resolve_equipped_stats", lambda items, region="eu": {})
    armory.fetch_character_gear("Brutoh", "uldum", "eu")
    assert seen["ns"] == "profile-eu"
    assert ".battle.net" not in seen["ns"]


# Real /statistics payload shape, captured live 2026-06-10 (EU/Uldum/Brutoh).
# Rating entries carry NO `rating` key — `rating_bonus` is the percent
# contributed by rating and `rating_normalized` is the real post-squish
# rating (the value armory.py must actually use — see PR fixing the
# haste/crit/vers ~2.3x/2.2x/1.85x over-count from the stale pct*100
# convention PR #214 replaced everywhere else on 2026-06-28); versatility is
# a bare float that's ALSO already the real rating, whose percent
# additionally lives in `versatility_damage_done_bonus`; armor `effective`
# is post-racial.
_PROFILE = {
    "name": "Brutoh",
    "race": {"name": "Earthen Dwarf"},
    "character_class": {"name": "Warrior"},
    "active_spec": {"name": "Protection"},
    # no "health" on purpose — the /statistics fallback must back-fill it
}
_STATISTICS = {
    "health": 830192,
    "strength": {"base": 850, "effective": 2232},
    "agility": {"base": 60, "effective": 60},
    "stamina": {"base": 4600, "effective": 37736},
    "armor": {"base": 1562, "effective": 5758},
    "melee_haste": {"rating_bonus": 24.09091, "value": 29.104187, "rating_normalized": 1060},
    "melee_crit": {"rating_bonus": 16.804348, "value": 23.804348, "rating_normalized": 773},
    "mastery": {"rating_bonus": 10.369565, "value": 22.369566, "rating_normalized": 318},
    "versatility": 245.0,
    "versatility_damage_done_bonus": 4.537037,
    "parry": {"rating_bonus": 14.865385, "value": 24.20289, "rating_normalized": 773},
}


def test_fetch_character_public_parses_live_statistics_shape(monkeypatch):
    """Regression for the zero-secondaries bug (2026-06-10): the old parse
    looked for `entry["rating"]`, which the live payload doesn't have, so
    every Armory-hydrated character simmed with 0% haste/crit/mastery/vers
    through the online-lookup landing flow.

    Also regression for the stale-pct*100 bug (found 2026-07-11): armory.py
    was never updated when PR #214 (2026-06-28) replaced the self-fit
    rating_per_pct=100 convention with real per-stat constants
    (haste=44/crit=46/vers=54). haste/crit/versatility must come from the
    payload's own already-correct `rating_normalized`/`versatility` fields,
    NOT `rating_bonus * 100`. Mastery is the deliberate exception — its
    rating_per_pct constant is still an unreviewed self-fit 100, so mastery
    must stay on `rating_bonus * 100` (1037, unchanged) or mastery_pct()
    would under-count real mastery ~3.3x downstream."""
    monkeypatch.setattr(armory, "_get_token", lambda region: "tok")

    def fake_fetch(url, *a, **k):
        return _STATISTICS if url.endswith("/statistics") else _PROFILE

    monkeypatch.setattr(armory, "_fetch_json", fake_fetch)
    cd = armory.fetch_character_public("uldum", "brutoh", "eu")
    assert cd is not None
    assert cd["haste_rating"] == 1060  # rating_normalized, NOT rating_bonus*100 (2409)
    assert cd["crit_rating"] == 773  # rating_normalized, NOT rating_bonus*100 (1680)
    # Mastery stays on the OLD pct*100 convention — deliberately not switched
    # to rating_normalized (318) alongside haste/crit, see docstring above.
    assert cd["mastery_rating"] == 1037
    assert cd["versatility_rating"] == 245  # bare `versatility` field, NOT pct*100 (454)
    # Sheet parry is Riposte-derived from crit — crediting it would double-count.
    assert cd["parry_rating"] == 0
    # Sheet armor is post-racial (Earthen +10% baked into `effective`) and the
    # engine re-applies the racial multiplier — the parse must back it out.
    assert cd["armor_from_gear"] == 5234
    assert cd["strength"] == 2232
    assert cd["agility"] == 60
    assert cd["stamina"] == 37736
    # Profile omitted health — the /statistics value must back-fill it.
    assert cd["max_hp_override"] == 830192


# ─── Region allowlist (SSRF hardening, Phase 5 2026-06-13) ────────────────────
# `region` is interpolated straight into the request host, so the function must
# fail closed on anything outside the real Blizzard regions — never fetch.


def test_fetch_character_public_rejects_invalid_region(monkeypatch):
    """A bad/SSRF-y region must return None WITHOUT attempting any fetch
    (no interpolation of an attacker host into the Bearer-token request)."""
    monkeypatch.setattr(armory, "_get_token", lambda region: "faketoken")
    calls: list = []
    monkeypatch.setattr(armory, "_fetch_json", lambda *a, **k: calls.append(a))
    for bad in ("evil.com#", "http://attacker", "us.api.blizzard.com.evil", "", "US/../"):
        assert armory.fetch_character_public("realm", "name", region=bad) is None
    assert calls == [], "no fetch should be attempted for an invalid region"


def test_fetch_character_public_accepts_real_regions(monkeypatch):
    """A valid region proceeds to a host pinned to that region."""
    monkeypatch.setattr(armory, "_get_token", lambda region: "faketoken")
    seen: list[str] = []
    monkeypatch.setattr(armory, "_fetch_json", lambda url, *a, **k: seen.append(url))
    for good in ("us", "eu", "kr", "tw", "cn", "EU"):  # case-insensitive
        seen.clear()
        armory.fetch_character_public("realm", "name", region=good)
        assert seen, f"valid region {good!r} should reach a fetch"
        assert seen[0].startswith(f"https://{good.lower()}.api.blizzard.com/"), seen[0]
