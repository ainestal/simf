"""Regression tests for COMBATANT_INFO → Character hydration.

Brutoh's idea #b (2026-05-25): when a combat log is ACL-on, the
COMBATANT_INFO row at run start carries spec / talents / equipped gear /
ratings. The hydrator turns that into the same `char_data` + `equipped`
shape the SimC paste pipeline produces, so loading a log lights up the
Gear and CD-plan surfaces without an extra paste step.

Acceptance lifted verbatim from ROADMAP Ideas Backlog:
  1. ACL-on log → Gear + CD-plan lit up without SimC paste.
  2. Equipped item IDs / ilvls / talents match COMBATANT_INFO exactly.
  3. ACL-off log → hydrator returns None → UI falls back to SimC prompt.
  4. SimC paste step is optional, not required.
"""

from __future__ import annotations

from pathlib import Path

from simf.io.character_from_combatant_info import (
    HydrateResult,
    _backout_armor,
    _equipped_from_combatant_info,
    _select_combatant_info,
    has_acl_combatant_info,
    hydrate_character,
)
from simf.io.combat_log import (
    CombatantInfoEquippedItem,
    CombatantInfoFull,
    iter_combatant_info_full,
)
from simf.io.combat_log_gear import _parse_gem_ids

# ── synthetic line builders ───────────────────────────────────────────────────


def _make_combatant_info_line(
    ts: str,
    guid: str,
    spec_id: int = 73,
    strength: int = 2182,
    agility: int = 428,
    stamina: int = 35884,
    intellect: int = 434,
    dodge_rating: int = 623,
    parry_rating: int = 0,
    crit_rating: int = 55,
    haste_rating: int = 1167,
    mastery_rating: int = 396,
    versatility_rating: int = 262,
    total_armor: int = 5517,
    *,
    talents: str = "(90324,112181,2),(90326,112183,1)",
    gear: list[tuple[int, int, int | None, tuple[int, ...], tuple[int, ...]]] | None = None,
) -> str:
    """One synthetic COMBATANT_INFO line that exercises every parsed field.

    Defaults mirror Brutoh's actual line from
    examples/WoWCombatLog-051026_073906.txt so a test against this output
    can be cross-checked by hand. Set `gear=None` to get an 18-empty-slot
    gear block; pass a list of (id, ilvl, enchant, bonus_ids, gem_ids)
    tuples to populate slots — first slot is HEAD, then NECK, etc.
    """
    if gear is None:
        gear_items = ["(0,0,(),(),())" for _ in range(18)]
    else:
        gear_items = []
        for i in range(18):
            if i < len(gear):
                item_id, ilvl, enchant, bonus_ids, gem_ids = gear[i]
                enchant_part = f"({enchant},0,0)" if enchant else "()"
                bonus_part = "(" + ",".join(str(b) for b in bonus_ids) + ")"
                gem_part = "(" + ",".join(str(g) for g in gem_ids) + ")"
                gear_items.append(f"({item_id},{ilvl},{enchant_part},{bonus_part},{gem_part})")
            else:
                gear_items.append("(0,0,(),(),())")
    gear_block = "[" + ",".join(gear_items) + "]"

    # 26-field prefix: COMBATANT_INFO + GUID + 24 simple values + spec_id.
    prefix = [
        "COMBATANT_INFO",
        guid,
        "0",  # [2] faction
        str(strength),  # [3]
        str(agility),  # [4]
        str(stamina),  # [5]
        str(intellect),  # [6]
        "0",  # [7]
        "0",  # [8]
        str(dodge_rating),  # [9]
        str(parry_rating),  # [10]
        str(crit_rating),  # [11] crit (melee/ranged/spell variants, always equal)
        str(crit_rating),  # [12]
        str(crit_rating),  # [13]
        "26",  # [14] speed (tertiary, unused — matches Brutoh's real log constant)
        "55",  # [15] leech (tertiary, unused — matches Brutoh's real log constant)
        str(haste_rating),  # [16]
        str(haste_rating),  # [17]
        str(haste_rating),  # [18]
        "37",  # [19] avoidance (tertiary, unused)
        str(mastery_rating),  # [20]
        str(versatility_rating),  # [21]
        str(versatility_rating),  # [22]
        str(versatility_rating),  # [23]
        str(total_armor),  # [24]
        str(spec_id),  # [25]
    ]
    talent_block = f"[{talents}]"
    return f"{ts}  {','.join(prefix)},{talent_block},(0,0,0,0),{gear_block},[],3,0,0,0\n"


def _damage_line(ts: str, dest_guid: str, dest_name: str) -> str:
    """A SPELL_DAMAGE line that ties a GUID to a Name-Realm-Region string."""
    return (
        f"{ts}  SPELL_DAMAGE,0x0,Boss,0x1,0x0,"
        f'{dest_guid},"{dest_name}",0x512,0x0,'
        f"123,Fireball,0x4,0,0,1000,1000,0,0,100,0,0,nil,nil\n"
    )


# ── parser tests (combat_log.iter_combatant_info_full) ────────────────────────


def test_iter_combatant_info_full_extracts_brutoh_style_row(tmp_path):
    """Parser pulls strength, stamina, haste, armor, spec_id from a synthetic
    line matching Brutoh's empirical Prot Warrior row exactly."""
    log = tmp_path / "test.log"
    log.write_text(
        _make_combatant_info_line(
            "5/10/2026 12:00:00.000",
            "Player-1-T",
            spec_id=73,
            strength=2182,
            stamina=35884,
            haste_rating=1167,
            mastery_rating=396,
            total_armor=5517,
        )
    )
    rows = list(iter_combatant_info_full(log))
    assert len(rows) == 1
    ci = rows[0]
    assert ci.guid == "Player-1-T"
    assert ci.spec_id == 73
    assert ci.strength == 2182
    assert ci.stamina == 35884
    assert ci.haste_rating == 1167
    assert ci.mastery_rating == 396
    assert ci.total_armor == 5517
    # 18 slots always emitted, even when all empty.
    assert len(ci.equipped) == 18


def test_iter_combatant_info_full_parses_gear_slot_order(tmp_path):
    """Slot ordering in the gear block is HEAD/NECK/SHOULDER/SHIRT/CHEST/…/TABARD —
    a regression on this assignment would silently misplace every item."""
    log = tmp_path / "test.log"
    gear = [
        (151333, 289, 7961, (13440, 6652, 12667, 13577, 12699, 12806), ()),  # head
        (50228, 272, None, (12801, 13440, 6652, 13668, 12699), (240983, 295)),  # neck
        (249950, 276, 7970, (6652, 13440, 13340, 13574, 12798), ()),  # shoulder
        (0, 0, None, (), ()),  # shirt
        (249955, 276, 7987, (6652, 13440, 13336, 13575, 12798), ()),  # chest
    ]
    log.write_text(_make_combatant_info_line("5/10/2026 12:00:00.000", "Player-1-T", gear=gear))
    [ci] = list(iter_combatant_info_full(log))
    by_slot = {it.slot: it for it in ci.equipped}
    assert by_slot["head"].item_id == 151333
    assert by_slot["head"].ilvl == 289
    assert by_slot["head"].enchant_id == 7961
    assert by_slot["head"].bonus_ids == (13440, 6652, 12667, 13577, 12699, 12806)
    assert by_slot["neck"].item_id == 50228
    # Log's raw sub-tuple is a (gemId, gemIlvl) PAIR — only the id survives.
    assert by_slot["neck"].gem_ids == (240983,)
    assert by_slot["chest"].item_id == 249955
    # Shirt slot stays empty (item_id=0) without skipping — slot order would
    # otherwise drift one position to the left for everything past it.
    assert by_slot["shirt"].item_id == 0


def test_iter_combatant_info_full_reads_crit_rating_from_field_11_13(tmp_path):
    """Regression for the crit-rating field-index bug: fields [14]/[15] are
    small tertiary rolls (speed/leech), not crit. This line's stat fields are
    transcribed byte-for-byte from Brutoh's real ACL row (examples/WoWCombatLog-
    051026_073906.txt; GUID field swapped for a synthetic placeholder) — not
    built via `_make_combatant_info_line` — so the test can't share a
    mistaken field-position assumption with the parser it's checking. Before
    the fix this asserted crit_rating == 55 (the
    [14]/[15] max); the real crit-rating triplet at [11-13] is 623, which
    also matches Brutoh's character-sheet crit (640 rating, 13.91% x 46
    rating-per-pct — same order of magnitude, buff-drift apart) instead of
    the wildly-too-low 55."""
    log = tmp_path / "test.log"
    log.write_text(
        "5/10/2026 07:43:52.3561  COMBATANT_INFO,Player-1379-AAAA0001,0,2182,428,"
        "35884,434,0,0,623,0,623,623,623,26,55,1167,1167,1167,37,396,262,262,262,"
        "5517,73,[],(),[(0,0,(),(),())],[],3,0,0,0\n"
    )
    [ci] = list(iter_combatant_info_full(log))
    assert ci.crit_rating == 623
    assert ci.haste_rating == 1167
    assert ci.mastery_rating == 396
    assert ci.total_armor == 5517


def test_iter_combatant_info_full_gem_pair_extracts_id_not_ilvl(tmp_path):
    """Regression for the gem-pair parsing bug: the gear block's gem
    sub-tuple is (gemId, gemIlvl) PAIRS, confirmed empirically across the
    whole examples/ corpus (every observed gem sub-tuple has length 0, 2,
    or 4 — never odd). A single ilvl-295 gem must parse to one gem id, not
    two; a double-gemmed item to two ids, not four."""
    log = tmp_path / "test.log"
    log.write_text(
        "5/10/2026 07:43:52.3561  COMBATANT_INFO,Player-1379-AAAA0001,0,2182,428,"
        "35884,434,0,0,623,0,623,623,623,26,55,1167,1167,1167,37,396,262,262,262,"
        "5517,73,[],(),"
        "[(50228,272,(),(),(240983,295,240898,295)),(151333,289,(),(),(240983,295))],"
        "[],3,0,0,0\n"
    )
    [ci] = list(iter_combatant_info_full(log))
    by_slot = {it.slot: it for it in ci.equipped}
    assert by_slot["head"].gem_ids == (240983, 240898)
    assert by_slot["neck"].gem_ids == (240983,)


def test_parse_gem_ids_extracts_id_from_each_pair():
    """Unit-level pin on `_parse_gem_ids` itself: every gem sub-tuple shape
    actually observed across examples/ (386 COMBATANT_INFO lines, 30,757
    gem sub-tuples) has length 0, 2, or 4 — never odd — confirming the
    (gemId, gemIlvl) pair shape. `_parse_int_tuple` (the pre-fix behavior)
    would return the full flat tuple here, keeping the ilvls as fake gems."""
    assert _parse_gem_ids("()") == ()
    assert _parse_gem_ids("(240983,295)") == (240983,)
    assert _parse_gem_ids("(240983,295,240898,295)") == (240983, 240898)


def test_iter_combatant_info_full_returns_empty_on_acl_off(tmp_path):
    """ACL-off logs emit zero COMBATANT_INFO lines — parser yields nothing."""
    log = tmp_path / "test.log"
    log.write_text(_damage_line("5/10/2026 12:00:00.000", "Player-1-T", "Tankman-Uldum-EU"))
    assert list(iter_combatant_info_full(log)) == []


# ── hydrator unit tests ───────────────────────────────────────────────────────


def test_hydrate_character_builds_full_char_data(tmp_path):
    """ACL-on log + valid target → HydrateResult with spec, stats, equipped."""
    log = tmp_path / "test.log"
    gear = [
        (151333, 289, 7961, (13440, 6652, 12667, 13577, 12699, 12806), ()),
        (50228, 272, None, (12801,), (240983,)),
    ] + [(0, 0, None, (), ())] * 16
    log.write_text(
        _make_combatant_info_line("5/10/2026 12:00:00.000", "Player-1-T", gear=gear)
        + _damage_line("5/10/2026 12:00:01.000", "Player-1-T", "Brutoh-Uldum-EU")
    )

    r = hydrate_character(log, "Brutoh-Uldum-EU")
    assert r is not None
    assert r.source == "combatant_info"
    assert r.char_data["class_spec"] == "protection_warrior"
    assert r.char_data["name"] == "Brutoh"
    assert r.char_data["server"] == "Uldum"
    assert r.char_data["region"] == "EU"
    assert r.char_data["strength"] == 2182
    assert r.char_data["stamina"] == 35884
    assert r.char_data["haste_rating"] == 1167
    # Race default is 'human' (no Earthen back-out) — armor passes through.
    assert r.char_data["armor_from_gear"] == 5517


def test_hydrate_character_matches_item_ids_exactly(tmp_path):
    """Acceptance #2 — equipped item_ids / ilvls / bonus_ids must match the
    COMBATANT_INFO row byte-for-byte. A drift here silently corrupts the
    Gear surface (wrong gear stats, wrong trial-swap baselines, etc.)."""
    log = tmp_path / "test.log"
    gear = [
        (151333, 289, 7961, (13440, 6652, 12667, 13577, 12699, 12806), ()),  # head
        (50228, 272, None, (12801, 13440), (240983, 295)),  # neck
        (249950, 276, 7970, (6652, 13440), ()),  # shoulder
        (0, 0, None, (), ()),  # shirt
        (249955, 276, 7987, (6652, 13440, 13336), ()),  # chest
        (249949, 289, None, (6652, 12667, 13440, 12806), ()),  # waist
        (249951, 272, 8159, (6652, 12801, 13440, 13339, 13575, 3157), ()),  # legs
        (249954, 272, None, (6652, 12801, 13440, 3157), ()),  # feet
        (237834, 285, None, (12214, 12497, 12066, 8960, 12384, 8792, 13622, 12667), ()),  # wrist
        (249953, 276, None, (6652, 13337, 13574, 12798), ()),  # hands
        (251115, 289, 8025, (13440, 6652, 13668, 12699, 12806), (240894, 295)),  # finger1
        (151311, 276, 7967, (13440, 6652, 13668, 12699, 12798), (240902, 295)),  # finger2
        (252420, 289, None, (13440, 6652, 12699, 12806), ()),  # trinket1
        (250241, 289, None, (13440, 6652, 12699, 12806), ()),  # trinket2
        (193712, 276, None, (13440, 40, 13577, 12699, 12798), ()),  # back
        (262731, 276, 7982, (6652, 12798), ()),  # main_hand
        (237831, 285, None, (12214, 12497, 12066, 8960, 12384, 8790, 13622), ()),  # off_hand
        (0, 0, None, (), ()),  # tabard
    ]
    log.write_text(
        _make_combatant_info_line("5/10/2026 12:00:00.000", "Player-1-T", gear=gear)
        + _damage_line("5/10/2026 12:00:01.000", "Player-1-T", "Brutoh-Uldum-EU")
    )

    r = hydrate_character(log, "Brutoh-Uldum-EU")
    assert r is not None
    eq = r.equipped
    # 16 non-empty slots (shirt + tabard intentionally empty in M+).
    assert len(eq) == 16
    # Spot-check the slots that matter most for survivability calcs.
    assert eq["head"].item_id == 151333
    assert eq["head"].ilvl == 289
    assert eq["head"].enchant_id == 7961
    assert eq["head"].bonus_ids == [13440, 6652, 12667, 13577, 12699, 12806]
    assert eq["off_hand"].item_id == 237831
    assert eq["off_hand"].ilvl == 285
    assert eq["off_hand"].bonus_ids == [12214, 12497, 12066, 8960, 12384, 8790, 13622]
    # Raw log sub-tuple (240894,295) is one (gemId,gemIlvl) pair — one gem.
    assert eq["finger1"].gem_ids == [240894]


def test_hydrate_character_returns_none_when_acl_off(tmp_path):
    """Acceptance #3 — no COMBATANT_INFO → None → UI falls back to SimC."""
    log = tmp_path / "test.log"
    log.write_text(_damage_line("5/10/2026 12:00:00.000", "Player-1-T", "Tankman-Uldum-EU"))
    assert hydrate_character(log, "Tankman-Uldum-EU") is None
    assert has_acl_combatant_info(log) is False


def test_hydrate_character_returns_none_when_target_not_in_log(tmp_path):
    """Target whose name never appears in the log → None. Real cause is
    typo / wrong character; we want the SimC fallback to engage, not a
    silently-wrong hydration to ship."""
    log = tmp_path / "test.log"
    log.write_text(
        _make_combatant_info_line("5/10/2026 12:00:00.000", "Player-1-T")
        + _damage_line("5/10/2026 12:00:01.000", "Player-1-T", "RealTank-Uldum-EU")
    )
    assert hydrate_character(log, "WrongName-Uldum-EU") is None


def test_hydrate_character_picks_correct_active_player(tmp_path):
    """Multi-player log — hydrator must follow the target_name selection
    and not collide with another party member's COMBATANT_INFO row."""
    log = tmp_path / "test.log"
    log.write_text(
        # Tank: Prot Warrior, str 2182.
        _make_combatant_info_line("5/10/2026 12:00:00.000", "Player-1-T", spec_id=73, strength=2182)
        # Healer: Holy Priest, int 5000.
        + _make_combatant_info_line(
            "5/10/2026 12:00:00.100",
            "Player-1-H",
            spec_id=257,
            strength=100,
            intellect=5000,
        )
        + _damage_line("5/10/2026 12:00:01.000", "Player-1-T", "Tankman-Uldum-EU")
        + _damage_line("5/10/2026 12:00:02.000", "Player-1-H", "Healwoman-Uldum-EU")
    )

    r_tank = hydrate_character(log, "Tankman-Uldum-EU")
    assert r_tank is not None
    assert r_tank.char_data["class_spec"] == "protection_warrior"
    assert r_tank.char_data["strength"] == 2182

    r_healer = hydrate_character(log, "Healwoman-Uldum-EU")
    assert r_healer is not None
    assert r_healer.char_data["class_spec"] == "holy_priest"
    assert r_healer.char_data["strength"] == 100


def test_hydrate_character_returns_none_on_unknown_spec_id(tmp_path):
    """Unknown spec_id → None. Bail rather than guess; the SimC fallback
    knows the spec from the user's paste."""
    log = tmp_path / "test.log"
    log.write_text(
        _make_combatant_info_line("5/10/2026 12:00:00.000", "Player-1-T", spec_id=9999)
        + _damage_line("5/10/2026 12:00:01.000", "Player-1-T", "Tankman-Uldum-EU")
    )
    assert hydrate_character(log, "Tankman-Uldum-EU") is None


def test_hydrate_character_invokes_resolve_stats_fn_only_when_ratings_zero(tmp_path):
    """The Wowhead-stat fallback exists for malformed logs where every
    rating field is zero. With ACL on and ratings populated, the hook
    must NOT fire — double-fetching is the bug the SimC pipeline has too."""
    log = tmp_path / "test.log"
    log.write_text(
        _make_combatant_info_line(
            "5/10/2026 12:00:00.000",
            "Player-1-T",
            haste_rating=1167,
            crit_rating=55,
            mastery_rating=396,
            versatility_rating=262,
        )
        + _damage_line("5/10/2026 12:00:01.000", "Player-1-T", "Tankman-Uldum-EU")
    )
    calls: list[dict] = []

    def fake_resolve(items):
        calls.append(items)
        return {"haste_rating": 99999, "stamina": 99999}

    r = hydrate_character(log, "Tankman-Uldum-EU", resolve_stats_fn=fake_resolve)
    assert r is not None
    assert calls == []  # never called when log has ratings
    assert r.char_data["haste_rating"] == 1167  # log values prevail


def test_hydrate_character_invokes_resolve_stats_fn_when_ratings_zero(tmp_path):
    """If every rating IS zero (malformed log), the Wowhead fallback fires
    and patches stats from the gear-DB. Item gear stays from the log."""
    log = tmp_path / "test.log"
    gear = [(151333, 289, 7961, (13440,), ())] + [(0, 0, None, (), ())] * 17
    log.write_text(
        _make_combatant_info_line(
            "5/10/2026 12:00:00.000",
            "Player-1-T",
            haste_rating=0,
            crit_rating=0,
            mastery_rating=0,
            versatility_rating=0,
            gear=gear,
        )
        + _damage_line("5/10/2026 12:00:01.000", "Player-1-T", "Tankman-Uldum-EU")
    )

    def fake_resolve(items):
        return {"haste_rating": 1500, "stamina": 30000}

    r = hydrate_character(log, "Tankman-Uldum-EU", resolve_stats_fn=fake_resolve)
    assert r is not None
    assert r.char_data["haste_rating"] == 1500  # filled by fallback
    # Equipped gear still comes from the log (acceptance #2).
    assert r.equipped["head"].item_id == 151333


def test_hydrate_character_extracts_talent_spell_ids(tmp_path):
    """Talents land in `char_data` via `_spec_to_talents_loadout` default;
    the per-spell ids round-trip through `iter_combatant_info_full` so the
    armor-multiplier auto-detect path (Reinforced Plates) still works."""
    log = tmp_path / "test.log"
    log.write_text(
        _make_combatant_info_line(
            "5/10/2026 12:00:00.000",
            "Player-1-T",
            talents="(90324,112181,2),(90326,112183,1),(90328,112185,1)",
        )
        + _damage_line("5/10/2026 12:00:01.000", "Player-1-T", "Tankman-Uldum-EU")
    )
    [ci] = list(iter_combatant_info_full(log))
    # Spell IDs are the middle value of each (node, spell, rank) tuple.
    assert ci.talent_spell_ids == frozenset({112181, 112183, 112185})

    r = hydrate_character(log, "Tankman-Uldum-EU")
    assert r is not None
    # Loadout name maps to spec default; the calibrate-k path threads the
    # spell IDs through `Character.detected_talent_spell_ids` separately.
    assert r.char_data["talents"] == "brutoh-actual"


def test_hydrate_character_sets_decoded_talents_from_real_entry_id(tmp_path):
    """A real, recognized entry id (Reinforced Plates' 112235 — confirmed
    byte-for-byte against a real log, see docs/validation/
    talent_string_decoder_2026_07_13.md) must land in char_data's
    `decoded_talents`, not just get parsed and discarded (the bug this
    feature closes)."""
    log = tmp_path / "test.log"
    log.write_text(
        _make_combatant_info_line(
            "5/10/2026 12:00:00.000",
            "Player-1-T",
            talents="(90368,112235,2),(90326,112183,1)",
        )
        + _damage_line("5/10/2026 12:00:01.000", "Player-1-T", "Tankman-Uldum-EU")
    )
    r = hydrate_character(log, "Tankman-Uldum-EU")
    assert r is not None
    assert r.char_data["decoded_talents"] == frozenset({"reinforced_plates"})


def test_hydrate_character_no_decoded_talents_when_nothing_recognized(tmp_path):
    log = tmp_path / "test.log"
    log.write_text(
        _make_combatant_info_line(
            "5/10/2026 12:00:00.000",
            "Player-1-T",
            talents="(1,999999999,1)",
        )
        + _damage_line("5/10/2026 12:00:01.000", "Player-1-T", "Tankman-Uldum-EU")
    )
    r = hydrate_character(log, "Tankman-Uldum-EU")
    assert r is not None
    assert "decoded_talents" not in r.char_data


# ── race armor back-out ───────────────────────────────────────────────────────


def test_backout_armor_strips_earthen_titan_wrought_frame():
    """Earthen race carries a 10% armor multiplier (Titan-Wrought Frame).
    COMBATANT_INFO reports post-multiplier armor; Character.total_armor()
    re-applies it. Double-counting would inflate eHP by 10% silently."""
    assert _backout_armor(5517, "earthen") == round(5517 / 1.10)


def test_backout_armor_passes_through_non_earthen_races():
    """Non-Earthen races have no armor racial — pass through unchanged."""
    assert _backout_armor(5000, "human") == 5000
    assert _backout_armor(5000, "tauren") == 5000


# ── equipped-from-combatant-info shape ────────────────────────────────────────


def test_equipped_from_combatant_info_skips_empty_slots():
    """Empty slots (item_id=0) must not appear in the equipped dict — the
    Gear surface counts populated slots and an empty entry there would
    pollute the n_eq tally."""
    ci = CombatantInfoFull(
        guid="Player-1-T",
        spec_id=73,
        talent_spell_ids=frozenset(),
        strength=0,
        agility=0,
        stamina=0,
        intellect=0,
        dodge_rating=0,
        parry_rating=0,
        crit_rating=0,
        haste_rating=0,
        mastery_rating=0,
        versatility_rating=0,
        total_armor=0,
        equipped=(
            CombatantInfoEquippedItem(
                slot="head",
                item_id=151333,
                ilvl=289,
                enchant_id=7961,
                bonus_ids=(13440,),
                gem_ids=(),
            ),
            CombatantInfoEquippedItem(
                slot="shirt",
                item_id=0,
                ilvl=0,
                enchant_id=None,
                bonus_ids=(),
                gem_ids=(),
            ),
        ),
    )
    out = _equipped_from_combatant_info(ci)
    assert set(out.keys()) == {"head"}
    assert out["head"].item_id == 151333


# ── snapshot selection (_select_combatant_info) ───────────────────────────────


def _ci(spec_id: int, stamina: int, total_armor: int, agility: int = 1970) -> CombatantInfoFull:
    """Minimal CombatantInfoFull for snapshot-selection tests."""
    return CombatantInfoFull(
        guid="Player-1-G",
        spec_id=spec_id,
        talent_spell_ids=frozenset(),
        strength=0,
        agility=agility,
        stamina=stamina,
        intellect=0,
        dodge_rating=0,
        parry_rating=0,
        crit_rating=0,
        haste_rating=0,
        mastery_rating=0,
        versatility_rating=0,
        total_armor=total_armor,
        equipped=(),
    )


def test_select_combatant_info_guardian_uses_out_of_form_caster_armor():
    """A Guardian emits a fresh COMBATANT_INFO per pull; the in-form ones carry
    already-Bear-Form-multiplied (+ live Ironfur) armor that the engine's ×3.2
    would double-count. Selection sources total_armor from the out-of-form
    (min-stamina) caster snapshot, but KEEPS the last snapshot's (in-form) stamina
    — the log path's stamina is in-form, and max_hp() applies NO Bear Form ×1.40
    on this path (only the SimC caster-stamina path opts in)."""
    snaps = [
        _ci(104, stamina=23_839, total_armor=919),  # caster (out of form)
        _ci(104, stamina=33_375, total_armor=2_958),  # bear, 0 Ironfur
        _ci(104, stamina=33_225, total_armor=7_847),  # bear + Ironfur (the "last")
    ]
    ci = _select_combatant_info(snaps)
    assert ci.total_armor == 919  # caster armor → ×3.2 gives the right bear base
    assert ci.stamina == 33_225  # in-form stamina kept (no ×1.40 on the log path)


def test_select_combatant_info_non_shapeshifter_bit_identical_when_last_is_minimum():
    """Warrior (spec 73) has no rotational armor buff, so the last snapshot is
    normally already the lowest-armor one observed — selection must return it
    unchanged (identity, not a copy) rather than swap in a same-or-worse value."""
    snaps = [
        _ci(73, stamina=35_884, total_armor=5_600),
        _ci(73, stamina=35_884, total_armor=5_400),  # last, already the minimum
    ]
    assert _select_combatant_info(snaps) is snaps[-1]


def test_select_combatant_info_paladin_prefers_unbuffed_minimum_armor():
    """Shield of the Righteous is a temporary self-buff that inflates
    COMBATANT_INFO's live total_armor (Midnight: +192% of Strength for 4.5s,
    spell 132403). A Paladin chaining SotR into boss pulls can have EVERY
    post-start snapshot read buffed, including the last one — selection must
    fall back to the minimum observed armor (the unbuffed floor), matching the
    pattern found in Bruttah's real Pit of Saron +2 log (2026-07-03)."""
    snaps = [
        _ci(66, stamina=29_610, total_armor=5_124),  # CHALLENGE_MODE_START, unbuffed
        _ci(66, stamina=29_610, total_armor=5_124),
        _ci(66, stamina=29_610, total_armor=5_124),
        _ci(66, stamina=29_610, total_armor=9_099),  # last — SotR active at the snapshot instant
    ]
    ci = _select_combatant_info(snaps)
    assert ci.total_armor == 5_124
    assert ci.stamina == 29_610  # unaffected field still carried from `last`
    assert ci is not snaps[-1]  # a corrected copy, not the buffed original


def test_select_combatant_info_single_snapshot_returned_as_is():
    snaps = [_ci(104, stamina=33_375, total_armor=2_958)]
    assert _select_combatant_info(snaps) is snaps[0]


def test_select_combatant_info_guardian_no_caster_snapshot_left_unchanged():
    """If the window has no genuinely-lower out-of-form snapshot (the min-stamina
    snapshot's armor isn't actually lower than the last's), don't fabricate one —
    return the last unchanged rather than swapping in a worse value."""
    snaps = [
        _ci(104, stamina=23_839, total_armor=6_000),  # min-stam but armor not lower
        _ci(104, stamina=33_225, total_armor=5_801),  # last
    ]
    assert _select_combatant_info(snaps) is snaps[-1]


# ── hero-talent / ledger BUFF gate detection on the hydrate path ──────────────


def _buff_aura_line(ts: str, guid: str, name: str, spell_id: int) -> str:
    """A SPELL_AURA_APPLIED BUFF line (what detect_active_buffs scans for)."""
    return (
        f'{ts}  SPELL_AURA_APPLIED,{guid},"{name}",0x512,0x0,'
        f'{guid},"{name}",0x512,0x0,{spell_id},"Buff",0x40,BUFF\n'
    )


def test_hydrate_detects_elunes_chosen_buff_gate(tmp_path):
    """Loading a Guardian from a log detects the Fury of Elune buff (202770) and
    sets active_buff_spell_ids, so the Ironfur haste model activates on the
    hydrate/gear-panel path — not only in replay. This is what makes haste's
    survival value visible when the character is loaded from a log."""
    log = tmp_path / "g.log"
    gear = [(250024, 289, None, (13440,), ())] + [(0, 0, None, (), ())] * 17
    log.write_text(
        _make_combatant_info_line("5/10/2026 12:00:00.000", "Player-1-G", spec_id=104, gear=gear)
        + _damage_line("5/10/2026 12:00:01.000", "Player-1-G", "AnonGuardian1-AnonRealm1-EU")
        + _buff_aura_line(
            "5/10/2026 12:00:02.000", "Player-1-G", "AnonGuardian1-AnonRealm1-EU", 202770
        )
    )
    r = hydrate_character(log, "AnonGuardian1-AnonRealm1-EU")
    assert r is not None
    assert r.char_data.get("active_buff_spell_ids") == frozenset({202770})


def test_hydrate_no_buff_gate_without_aura(tmp_path):
    """A Guardian log WITHOUT the Fury of Elune buff → active_buff_spell_ids stays
    unset (haste model off; bit-identical to pre-detection)."""
    log = tmp_path / "g2.log"
    gear = [(250024, 289, None, (13440,), ())] + [(0, 0, None, (), ())] * 17
    log.write_text(
        _make_combatant_info_line("5/10/2026 12:00:00.000", "Player-1-G", spec_id=104, gear=gear)
        + _damage_line("5/10/2026 12:00:01.000", "Player-1-G", "AnonGuardian1-AnonRealm1-EU")
    )
    r = hydrate_character(log, "AnonGuardian1-AnonRealm1-EU")
    assert r is not None
    assert "active_buff_spell_ids" not in r.char_data


# ── race detection on the hydrate path ────────────────────────────────────────


def _racial_cast_line(ts: str, guid: str, name: str, spell_id: int) -> str:
    """A SPELL_CAST_SUCCESS line where `name` casts `spell_id` (a racial)."""
    return (
        f'{ts}  SPELL_CAST_SUCCESS,{guid},"{name}",0x512,0x0,'
        f'{guid},"{name}",0x512,0x0,{spell_id},"Racial",0x1\n'
    )


def test_hydrate_detects_tauren_race_from_war_stomp(tmp_path):
    """A Tauren casting War Stomp (20549) in the log → race='tauren', so the
    +5% Endurance HP the human default drops is recovered."""
    log = tmp_path / "g.log"
    gear = [(250024, 289, None, (13440,), ())] + [(0, 0, None, (), ())] * 17
    log.write_text(
        _make_combatant_info_line("5/10/2026 12:00:00.000", "Player-1-G", spec_id=104, gear=gear)
        + _damage_line("5/10/2026 12:00:01.000", "Player-1-G", "AnonGuardian1-AnonRealm1-EU")
        + _racial_cast_line(
            "5/10/2026 12:00:02.000", "Player-1-G", "AnonGuardian1-AnonRealm1-EU", 20549
        )
    )
    r = hydrate_character(log, "AnonGuardian1-AnonRealm1-EU")
    assert r is not None
    assert r.char_data["race"] == "tauren"


def test_hydrate_defaults_human_without_racial(tmp_path):
    """No racial cast → race stays 'human' (warrior-safe, bit-identical to before
    race detection)."""
    log = tmp_path / "g2.log"
    gear = [(250024, 289, None, (13440,), ())] + [(0, 0, None, (), ())] * 17
    log.write_text(
        _make_combatant_info_line("5/10/2026 12:00:00.000", "Player-1-G", spec_id=104, gear=gear)
        + _damage_line("5/10/2026 12:00:01.000", "Player-1-G", "AnonGuardian1-AnonRealm1-EU")
    )
    r = hydrate_character(log, "AnonGuardian1-AnonRealm1-EU")
    assert r is not None
    assert r.char_data["race"] == "human"


def test_build_hydrate_result_race_param_defaults_human():
    """build_hydrate_result (the shared WCL/local builder) defaults race='human'
    so the WCL path — which has no log to scan — is bit-identical."""
    from simf.io.character_from_combatant_info import build_hydrate_result

    ci = _ci(104, stamina=33_000, total_armor=6_000)
    r_default = build_hydrate_result(ci, "AnonGuardian1-AnonRealm1-EU")
    assert r_default is not None and r_default.char_data["race"] == "human"
    r_tauren = build_hydrate_result(ci, "AnonGuardian1-AnonRealm1-EU", race="tauren")
    assert r_tauren.char_data["race"] == "tauren"


# ── end-to-end against real log (acceptance #2 hard pin) ──────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent
# The Brutoh ACL log is stored in the main checkout's examples/ directory and
# may not be present in feature worktrees. Probe both: this worktree's
# examples/ first, then the canonical repo path. The test still skips if
# neither exists (e.g. a CI environment without examples/).
_REAL_LOG_NAME = "WoWCombatLog-051026_073906.txt"
_CANDIDATE_PATHS = [
    REPO_ROOT / "examples" / _REAL_LOG_NAME,
    Path.home() / "Documents" / "AI" / "simf" / "examples" / _REAL_LOG_NAME,
]
REAL_LOG = next((p for p in _CANDIDATE_PATHS if p.exists()), _CANDIDATE_PATHS[0])


def test_hydrate_character_real_brutoh_log_matches_simc_paste():
    """Acceptance #2 hard pin — Brutoh's actual ACL log → hydrator returns a
    Character whose equipped slots match `examples/brutoh.simc` item-by-item.

    Skipped when the real log isn't checked in (CI environments without
    examples/ shouldn't fail the suite). Synthetic-only coverage would let
    a field-position bug ship green; this is the test that catches it.
    """
    if not REAL_LOG.exists():
        import pytest

        pytest.skip(f"Real log {REAL_LOG} not present.")
    r = hydrate_character(REAL_LOG, "Brutoh-Uldum-EU")
    assert r is not None, "ACL log must hydrate"
    assert r.char_data["class_spec"] == "protection_warrior"
    assert r.char_data["name"] == "Brutoh"
    assert r.char_data["server"] == "Uldum"
    assert r.char_data["region"] == "EU"
    # Slot-by-slot match against examples/brutoh.simc. Items below are
    # pulled directly from that .simc file (lines 26-56) — a regression
    # here would either be a parser bug or a Blizzard slot-order change.
    eq = r.equipped
    assert eq["head"].item_id == 151333
    assert eq["head"].enchant_id == 7961
    assert eq["head"].bonus_ids == [13440, 6652, 12667, 13577, 12699, 12806]
    assert eq["neck"].item_id == 50228
    assert eq["shoulder"].item_id == 249950
    assert eq["chest"].item_id == 249955
    assert eq["main_hand"].item_id == 262731
    assert eq["off_hand"].item_id == 237831
    # Strength + haste from the log match values consistent with brutoh.yaml
    # (ratings vary slightly with raid buffs between COMBATANT_INFO emits).
    assert r.char_data["haste_rating"] == 1167
    assert r.char_data["mastery_rating"] == 396


def test_has_acl_combatant_info_true_for_real_brutoh_log():
    """Probe used by the UI before attempting hydration — must return True
    on Brutoh's ACL log."""
    if not REAL_LOG.exists():
        import pytest

        pytest.skip(f"Real log {REAL_LOG} not present.")
    assert has_acl_combatant_info(REAL_LOG) is True


# ── HydrateResult shape stays serialisable / dict-shaped ──────────────────────


def test_hydrate_result_is_a_dict_assignable_to_session_state(tmp_path):
    """The UI does `_ss()['char_data'] = result.char_data` and expects a
    plain dict that Character.from_dict() can swallow. Guard against the
    dataclass being mistakenly serialised as a frozen wrapper."""
    log = tmp_path / "test.log"
    log.write_text(
        _make_combatant_info_line("5/10/2026 12:00:00.000", "Player-1-T")
        + _damage_line("5/10/2026 12:00:01.000", "Player-1-T", "Tankman-Uldum-EU")
    )
    r = hydrate_character(log, "Tankman-Uldum-EU")
    assert isinstance(r, HydrateResult)
    assert isinstance(r.char_data, dict)
    assert isinstance(r.equipped, dict)
    # Character.from_dict filters to known fields, so unknown ones must not
    # crash the constructor.
    from simf.core.character import Character

    char = Character.from_dict(r.char_data)
    assert char.class_spec == "protection_warrior"
    assert char.stamina == 35884


def test_build_hydrate_result_guardian_no_caster_form_flag():
    """Log/WCL hydrate stores the in-form COMBATANT_INFO stamina, so it must NOT
    set stamina_in_caster_form (else max_hp() would double-count Bear Form ×1.40
    on top of already-in-form stamina)."""
    from simf.io.character_from_combatant_info import build_hydrate_result

    ci = _ci(104, stamina=33_000, total_armor=6_000)  # guardian, in-form stamina
    r = build_hydrate_result(ci, "AnonGuardian1-AnonRealm1-EU")
    assert r is not None
    assert r.char_data.get("stamina_in_caster_form", False) is False
