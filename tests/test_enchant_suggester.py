"""Enchant suggester tests — catalog integrity, ΔeHP scoring, slot-category
mapping, dual-wield off_hand gating, and the "not modeled" detection.

Marginals are synthetic so each assertion is hand-checkable; the real live
marginals come from the sim path (survivability_weights). Shapes match
``ehp_marginals()`` exactly: ``{stat: {"p": .., "m": ..}}``.
"""

import pytest

from simf.optimizer.enchant_suggester import (
    EnchantSlot,
    candidate_enchants,
    enchant_slots_from_equipped,
    enchant_survival_value,
    enchantable_slots,
    find_enchant_by_id,
    load_enchant_catalog,
    suggest_enchants,
)

# Live-Blizzard-confirmed ids used across the suite (2026-07-01, Brutoh EU-Uldum).
WORLDSOUL = 7987  # Mark of the Worldsoul, +50 primary, chest
FOREST_HUNTER = 8159  # Forest Hunter's Armor Kit, +41 primary +115 stamina, legs
SHALADRASSIL = 7993  # Shaladrassil's Roots, +186 stamina (+unmodeled leech), feet
SILVERMOON_ALACRITY = 8025  # +29 haste, finger
HEX_OF_LEECHING = 7961  # unmodeled (leech only), head
BLESSING_OF_SPEED = (
    7988  # unmodeled (speed only), head — live-confirmed 2026-07-30 (Bruttah EU-Uldum)
)
ACUITY_OF_THE_RENDOREI = 8039  # unmodeled DPS proc, weapon — live-confirmed 2026-07-31 (Bruttah)


def _warrior_marg():
    """Plate tank: strength carries parry value (phys only), armor + vers +
    stamina valued, agility/haste/crit/mastery zero."""
    return {
        "stamina": {"p": 20.0, "m": 16.0},
        "armor_from_gear": {"p": 5.0, "m": 0.0},
        "versatility_rating": {"p": 6.0, "m": 5.0},
        "haste_rating": {"p": 0.0, "m": 0.0},
        "crit_rating": {"p": 0.0, "m": 0.0},
        "mastery_rating": {"p": 3.0, "m": 2.0},
        "strength": {"p": 4.0, "m": 0.0},
        "agility": {"p": 0.0, "m": 0.0},
    }


# --------------------------------------------------------------------------
# Catalog integrity
# --------------------------------------------------------------------------


def test_catalog_loads_known_enchants():
    by_id = {int(e["id"]): e for e in load_enchant_catalog() if e.get("id")}
    assert by_id[WORLDSOUL]["stats"] == {"primary": 50}
    assert by_id[FOREST_HUNTER]["stats"] == {"primary": 41, "stamina": 115}
    assert by_id[SILVERMOON_ALACRITY]["stats"] == {"haste_rating": 29}


def test_catalog_has_unconfirmed_id_entries():
    """Real, deliberate: several researched siblings have no confirmed id
    yet — they still work as suggester candidates (see module docstring in
    data/enchants.yaml). This is not a data-loading bug."""
    assert any(not e.get("id") for e in load_enchant_catalog())


def test_find_enchant_by_id():
    assert find_enchant_by_id(WORLDSOUL)["name"] == "Mark of the Worldsoul"
    assert find_enchant_by_id(None) is None
    assert find_enchant_by_id(999999999) is None


def test_blessing_of_speed_id_regression():
    """A real user report (2026-07-30, Bruttah EU-Uldum) traced a missing
    hover-tooltip/link on this exact enchant to a missing catalog `id` — the
    entry existed (Wowhead-researched) but could never match a live
    character's actual enchant_id, silently degrading the paperdoll card's
    enchant line to plain, unlinked text. Pins the fix: id 7988 now resolves,
    and the name matches Blizzard's live string ("Blessing of Speed", no
    "Empowered" prefix — the earlier Wowhead-researched guess was wrong)."""
    entry = find_enchant_by_id(BLESSING_OF_SPEED)
    assert entry is not None
    assert entry["name"] == "Blessing of Speed"
    assert entry["slot"] == "head"


def test_acuity_of_the_rendorei_id_regression():
    """A second instance of the same bug class (2026-07-31, Bruttah
    EU-Uldum): the weapon enchant "Acuity of the Ren'dorei" wasn't in the
    catalog AT ALL (not even an unconfirmed-id entry), so it could never
    link/tooltip regardless of catalog matching. Pins the fix: id 8039 now
    resolves to a real weapon enchant."""
    entry = find_enchant_by_id(ACUITY_OF_THE_RENDOREI)
    assert entry is not None
    assert entry["name"] == "Acuity of the Ren'dorei"
    assert entry["slot"] == "weapon"


def test_back_wrist_and_neck_have_no_catalog_entries():
    """Structural finding: Midnight 12.0.5 removed cloak/bracer enchants, and
    neck enchants haven't existed since Legion — the catalog must not invent
    any of the three."""
    slots = {e["slot"] for e in load_enchant_catalog()}
    assert "back" not in slots
    assert "wrist" not in slots
    assert "neck" not in slots


# --------------------------------------------------------------------------
# Slot-category mapping + dual-wield gating
# --------------------------------------------------------------------------


def test_enchantable_slots_excludes_off_hand_for_shield_specs():
    slots = enchantable_slots("protection_warrior")
    assert "off_hand" not in slots
    assert "main_hand" in slots
    assert "back" not in slots
    assert "wrist" not in slots
    assert "neck" not in slots


def test_enchantable_slots_includes_off_hand_for_dual_wield_specs():
    for spec in ("brewmaster_monk", "vengeance_demon_hunter"):
        slots = enchantable_slots(spec)
        assert "off_hand" in slots
        assert "main_hand" in slots


def test_candidate_enchants_filters_by_category():
    ring_cands = candidate_enchants("finger", "protection_warrior")
    assert all(
        "haste_rating" in c.stats
        or "mastery_rating" in c.stats
        or "versatility_rating" in c.stats
        or "crit_rating" in c.stats
        for c in ring_cands
    )
    chest_cands = candidate_enchants("chest", "protection_warrior")
    assert any(c.enchant_id == WORLDSOUL for c in chest_cands)
    assert not any(c.enchant_id == WORLDSOUL for c in ring_cands)


def test_candidate_enchants_resolves_primary_per_spec():
    warrior = candidate_enchants("chest", "protection_warrior")
    guardian = candidate_enchants("chest", "guardian_druid")
    worldsoul_w = next(c for c in warrior if c.enchant_id == WORLDSOUL)
    worldsoul_g = next(c for c in guardian if c.enchant_id == WORLDSOUL)
    assert worldsoul_w.stats == {"strength": 50}
    assert worldsoul_g.stats == {"agility": 50}


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


def test_enchant_survival_value_respects_school_mix():
    marg = _warrior_marg()
    vers = {"versatility_rating": 29}
    phys = enchant_survival_value(vers, marg, [{"school_mix": {"physical": 1.0}}])
    magic = enchant_survival_value(vers, marg, [{"school_mix": {"physical": 0.0}}])
    assert phys == pytest.approx(29 * 6.0)
    assert magic == pytest.approx(29 * 5.0)


def test_unmodeled_stats_score_zero():
    """Leech/Avoidance/Speed aren't canonical marginal keys — an enchant
    whose only stats are those (encoded as an empty dict in the catalog)
    scores exactly 0, same mechanism that zeroes an unvalued secondary."""
    assert enchant_survival_value({}, _warrior_marg()) == 0.0


# --------------------------------------------------------------------------
# suggest_enchants: end-to-end per-slot recommendations
# --------------------------------------------------------------------------


def test_chest_recommends_switch_when_stamina_dominates():
    """Worldsoul (+50 str) vs Nalorakk (+32 str +93 stamina): with stamina
    heavily valued, Nalorakk's extra stamina should win despite less str."""
    slots = [EnchantSlot("chest", WORLDSOUL)]
    out = suggest_enchants(slots, _warrior_marg(), class_spec="protection_warrior")
    assert out[0].modeled is True
    assert out[0].best.name == "Mark of Nalorakk"
    assert out[0].delta_ehp > 0


def test_ring_switches_off_worthless_haste():
    """Silvermoon's Alacrity (+29 haste, worth 0 for a warrior) should lose
    to a secondary the marginals actually value (mastery here)."""
    marg = _warrior_marg()
    marg["mastery_rating"] = {"p": 50.0, "m": 40.0}  # make mastery dominant
    slots = [EnchantSlot("finger1", SILVERMOON_ALACRITY)]
    out = suggest_enchants(slots, marg, class_spec="protection_warrior")
    assert "mastery_rating" in out[0].best.stats
    assert out[0].delta_ehp > 0


def test_no_slot_ever_recommends_a_downgrade():
    slots = [
        EnchantSlot("chest", WORLDSOUL),
        EnchantSlot("legs", FOREST_HUNTER),
        EnchantSlot("finger1", SILVERMOON_ALACRITY),
        EnchantSlot("finger2", None),
        EnchantSlot("feet", SHALADRASSIL),
        EnchantSlot("head", HEX_OF_LEECHING),
    ]
    for marg, spec in (
        (_warrior_marg(), "protection_warrior"),
        (_warrior_marg(), "guardian_druid"),
    ):
        out = suggest_enchants(slots, marg, class_spec=spec)
        assert all(s.delta_ehp >= 0 for s in out), (
            f"{spec}: negative delta in {[(s.slot, s.delta_ehp) for s in out]}"
        )


def test_head_slot_is_never_modeled():
    """Every head option is Avoidance/Leech/Speed — none scoreable — so the
    suggester must flag this slot, not fabricate a confident pick."""
    for current in (None, HEX_OF_LEECHING, 123456789):
        out = suggest_enchants(
            [EnchantSlot("head", current)], _warrior_marg(), class_spec="protection_warrior"
        )
        assert out[0].modeled is False


def test_feet_slot_is_modeled_via_stamina():
    """Feet options grant Stamina (modeled) alongside an unmodeled tertiary —
    unlike head, this slot SHOULD produce a confident, scored pick."""
    out = suggest_enchants(
        [EnchantSlot("feet", None)], _warrior_marg(), class_spec="protection_warrior"
    )
    assert out[0].modeled is True
    assert out[0].best is not None
    assert out[0].delta_ehp > 0


def test_weapon_slot_is_never_modeled():
    """Every Midnight weapon enchant is a DPS proc or an unmodeled-magnitude
    absorb proc — no flat survival stat exists at all."""
    out = suggest_enchants(
        [EnchantSlot("main_hand", 7983)], _warrior_marg(), class_spec="protection_warrior"
    )
    assert out[0].modeled is False


def test_no_enchant_exists_slots_flagged_distinctly_from_not_modeled():
    """back/wrist/neck/waist/hands aren't merely 'unmodeled' (a catalog
    exists but scores ~0) — they have NO catalog at all, a confirmed
    structural finding, so the suggester must flag them with their own
    reason, not the generic one."""
    for slot in ("back", "wrist", "neck", "waist", "hands"):
        out = suggest_enchants(
            [EnchantSlot(slot, None)], _warrior_marg(), class_spec="protection_warrior"
        )
        assert out[0].modeled is False
        assert out[0].no_enchant_exists is True


def test_modeled_slot_has_no_enchant_exists_flag_unset():
    """A slot with a real, scoreable catalog (chest) must not accidentally
    trip the no-enchant-exists honesty flag."""
    out = suggest_enchants(
        [EnchantSlot("chest", WORLDSOUL)], _warrior_marg(), class_spec="protection_warrior"
    )
    assert out[0].modeled is True
    assert out[0].no_enchant_exists is False


def test_unknown_current_enchant_marked_and_zero_valued():
    out = suggest_enchants(
        [EnchantSlot("finger1", 405863)],  # a non-catalog id
        _warrior_marg(),
        class_spec="protection_warrior",
    )
    assert out[0].current_known is False
    assert out[0].current_value == 0.0
    assert out[0].current_name is None


def test_empty_slots_returns_empty():
    assert suggest_enchants([], _warrior_marg(), class_spec="protection_warrior") == []


def test_keep_current_when_already_optimal():
    # Haste-dominant marginals so Silvermoon's Alacrity (a CONFIRMED-id
    # candidate) wins the ring pool outright — picking a slot whose winner
    # has no confirmed id (e.g. chest's Mark of Nalorakk) would make "already
    # equipped" unrepresentable (there's no real id to equip it by).
    marg = _warrior_marg()
    marg["haste_rating"] = {"p": 50.0, "m": 40.0}
    out = suggest_enchants([EnchantSlot("finger1", None)], marg, class_spec="protection_warrior")
    best_id = out[0].best.enchant_id
    assert best_id == SILVERMOON_ALACRITY
    # Re-run with that best enchant already equipped — should keep it, delta 0.
    out2 = suggest_enchants(
        [EnchantSlot("finger1", best_id)], marg, class_spec="protection_warrior"
    )
    assert out2[0].delta_ehp == 0.0


# --------------------------------------------------------------------------
# enchant_slots_from_equipped
# --------------------------------------------------------------------------


class _FakeItem:
    def __init__(self, slot, enchant_id=None):
        self.slot = slot
        self.enchant_id = enchant_id


def test_enchant_slots_from_equipped_flattens_dataclass_and_dict():
    equipped = {
        "chest": _FakeItem("chest", WORLDSOUL),
        "finger1": {"enchant_id": SILVERMOON_ALACRITY},
        "back": _FakeItem("back", None),  # confirmed no-enchant slot — still included
        "off_hand": _FakeItem("off_hand", None),  # shield for a warrior — excluded
        "head": None,  # empty slot — excluded
    }
    slots = enchant_slots_from_equipped(equipped, "protection_warrior")
    pairs = {(s.slot, s.enchant_id) for s in slots}
    assert ("chest", WORLDSOUL) in pairs
    assert ("finger1", SILVERMOON_ALACRITY) in pairs
    assert ("back", None) in pairs
    # off_hand (a shield here) and an empty slot never produce an entry.
    assert not any(s.slot in ("off_hand", "head") for s in slots)


def test_enchant_slots_from_equipped_includes_no_enchant_slots():
    """back/wrist/neck/waist/hands (confirmed no enchant exists) all still
    flatten into an EnchantSlot — this is what lets the UI render an honest
    caption instead of silently omitting the row."""
    equipped = {
        "back": _FakeItem("back", None),
        "wrist": _FakeItem("wrist", None),
        "neck": _FakeItem("neck", None),
        "waist": _FakeItem("waist", None),
        "hands": _FakeItem("hands", None),
    }
    slots = enchant_slots_from_equipped(equipped, "protection_warrior")
    assert {s.slot for s in slots} == {"back", "wrist", "neck", "waist", "hands"}


def test_enchant_slots_from_equipped_includes_off_hand_for_dual_wield():
    equipped = {"off_hand": _FakeItem("off_hand", 7983)}
    slots = enchant_slots_from_equipped(equipped, "brewmaster_monk")
    assert [(s.slot, s.enchant_id) for s in slots] == [("off_hand", 7983)]
