"""Tier-set tracker tests — exercises the curated `data/tier_sets.yaml`
against Brutoh's known item-id pattern (Night Ender pieces in head/shoulder/
chest/hands/legs slots)."""

from __future__ import annotations

from dataclasses import dataclass

from simf.optimizer.tier_sets import (
    equipped_set_status,
    load_tier_sets,
    swap_breaks_threshold,
    swap_completes_threshold,
    tier_status_by_item_id,
)


@dataclass
class _Item:
    item_id: int


# Brutoh's actual Midnight S1 tier-set item ids drawn from brutoh-vault.simc.
NIGHT_ENDER_HEAD = 249950
NIGHT_ENDER_CHEST = 249955
NIGHT_ENDER_HANDS = 249953
NIGHT_ENDER_LEGS = 249951
NON_TIER = 151333  # Crown of the Dark Envoy — same slot as tier but not in set


def _equipped(*ids: int) -> dict:
    """Build an `equipped`-dict from a sequence of tier item-ids."""
    slots = ["head", "shoulder", "chest", "hands", "legs"]
    return {slot: _Item(item_id=ids[i]) for i, slot in enumerate(slots) if i < len(ids)}


def test_no_pieces_returns_empty():
    """Spec with no tier matches → no SetStatus entries."""
    out = equipped_set_status(_equipped(NON_TIER), "protection_warrior")
    assert out == []


def test_two_pieces_reports_2pc_active():
    out = equipped_set_status(
        _equipped(NIGHT_ENDER_HEAD, NIGHT_ENDER_CHEST),
        "protection_warrior",
    )
    assert len(out) == 1
    assert out[0].pieces == 2
    assert out[0].active_threshold == 2
    assert out[0].set.name == "Night Ender's Vesture"


def test_four_pieces_reports_4pc_active():
    out = equipped_set_status(
        _equipped(NIGHT_ENDER_HEAD, NIGHT_ENDER_CHEST, NIGHT_ENDER_HANDS, NIGHT_ENDER_LEGS),
        "protection_warrior",
    )
    assert out[0].pieces == 4
    assert out[0].active_threshold == 4


def test_three_pieces_drops_to_2pc():
    """3 pieces → 4pc not active but 2pc is. Highest threshold honored."""
    out = equipped_set_status(
        _equipped(NIGHT_ENDER_HEAD, NIGHT_ENDER_CHEST, NIGHT_ENDER_HANDS),
        "protection_warrior",
    )
    assert out[0].pieces == 3
    assert out[0].active_threshold == 2


def test_spec_filter_isolates_protection_warrior_set():
    """A non-Warrior spec must not match Night Ender's Vesture."""
    out = equipped_set_status(
        _equipped(NIGHT_ENDER_HEAD, NIGHT_ENDER_CHEST),
        "protection_paladin",
    )
    assert out == []


def test_swap_breaks_4pc_threshold():
    """Trial-swap a tier head out for a non-tier head — was 4pc, now 3pc."""
    equipped = _equipped(
        NIGHT_ENDER_HEAD,
        NIGHT_ENDER_CHEST,
        NIGHT_ENDER_HANDS,
        NIGHT_ENDER_LEGS,
    )
    broken = swap_breaks_threshold(equipped, "protection_warrior", "head", _Item(item_id=NON_TIER))
    assert broken is not None
    assert broken.pieces == 4
    assert broken.active_threshold == 4


def test_swap_does_not_break_when_pieces_remain_above_threshold():
    """Drop from 4pc to 3pc — but the test we want here is 5→4 (still 4pc).
    Use 2 → 2 transition: swap a non-tier into a non-tier slot."""
    equipped = _equipped(NIGHT_ENDER_HEAD, NIGHT_ENDER_CHEST)
    # Swap chest → another tier chest (same set) — count stays 2.
    broken = swap_breaks_threshold(
        equipped, "protection_warrior", "chest", _Item(item_id=NIGHT_ENDER_CHEST)
    )
    assert broken is None


def test_swap_returns_none_when_no_set_active():
    """No equipped tier pieces → swapping doesn't break anything."""
    equipped = _equipped(NON_TIER)
    broken = swap_breaks_threshold(
        equipped, "protection_warrior", "head", _Item(item_id=NIGHT_ENDER_HEAD)
    )
    assert broken is None


# ---------------------------------------------------------------------------
# Registry-level invariants — ensure every entry in data/tier_sets.yaml is
# structurally complete. Cheap insurance against typos in future additions.
# ---------------------------------------------------------------------------

EXPECTED_TANK_SPECS = {
    "protection_warrior",
    "protection_paladin",
    "brewmaster_monk",
    "guardian_druid",
    "blood_death_knight",
    "vengeance_demon_hunter",
}


def test_registry_covers_every_tank_spec():
    """Each Midnight S1 tank spec needs at least one curated tier set so the
    paperdoll badge can light up regardless of who's loaded."""
    sets = load_tier_sets()
    specs = {s.spec for s in sets}
    missing = EXPECTED_TANK_SPECS - specs
    assert not missing, f"tier_sets.yaml missing entries for: {sorted(missing)}"


def test_every_registry_entry_is_structurally_complete():
    """Each entry must have id/name/spec/bonus_2pc/bonus_4pc populated and a
    non-empty item_ids list of ints. Catches half-typed YAML before it ships."""
    for s in load_tier_sets():
        assert s.id, "tier-set entry missing id"
        assert s.name, f"tier-set {s.id} missing name"
        assert s.spec, f"tier-set {s.id} missing spec"
        assert s.bonus_2pc, f"tier-set {s.id} missing bonus_2pc"
        assert s.bonus_4pc, f"tier-set {s.id} missing bonus_4pc"
        assert s.item_ids, f"tier-set {s.id} has no item_ids"
        assert all(isinstance(i, int) and i > 0 for i in s.item_ids), (
            f"tier-set {s.id} has non-positive-int item_ids: {sorted(s.item_ids)}"
        )


def test_registry_ids_are_unique():
    """No duplicate tier-set ids — duplicates would double-count piece totals."""
    ids = [s.id for s in load_tier_sets()]
    assert len(ids) == len(set(ids)), f"duplicate tier-set ids: {ids}"


# ---------------------------------------------------------------------------
# swap_completes_threshold — the inverse of swap_breaks_threshold. A vault
# piece is often tier; normalized stats-only comparison undersells a swap that
# *completes* a 2pc/4pc, so the UI needs to flag the gained bonus.
# ---------------------------------------------------------------------------


def test_swap_completes_2pc_threshold():
    """1 tier piece (no bonus) → swap a second tier piece in → 2pc gained."""
    equipped = _equipped(NIGHT_ENDER_HEAD)  # only head is tier → threshold 0
    gained = swap_completes_threshold(
        equipped, "protection_warrior", "shoulder", _Item(item_id=NIGHT_ENDER_CHEST)
    )
    assert gained is not None
    assert gained.pieces == 2
    assert gained.active_threshold == 2
    assert gained.set.name == "Night Ender's Vesture"


def test_swap_completes_4pc_threshold():
    """3 tier pieces (2pc) → swap a fourth in → 4pc gained."""
    equipped = _equipped(NIGHT_ENDER_HEAD, NIGHT_ENDER_CHEST, NIGHT_ENDER_HANDS)
    gained = swap_completes_threshold(
        equipped, "protection_warrior", "legs", _Item(item_id=NIGHT_ENDER_LEGS)
    )
    assert gained is not None
    assert gained.active_threshold == 4


def test_swap_completes_none_when_no_new_threshold():
    """Already 2pc and the swap doesn't add a tier piece → no gain flagged."""
    equipped = _equipped(NIGHT_ENDER_HEAD, NIGHT_ENDER_CHEST)  # 2pc already
    gained = swap_completes_threshold(
        equipped, "protection_warrior", "legs", _Item(item_id=NON_TIER)
    )
    assert gained is None


def test_swap_completes_none_when_swap_breaks_instead():
    """Swapping a tier piece OUT for non-tier can't complete a threshold."""
    equipped = _equipped(NIGHT_ENDER_HEAD, NIGHT_ENDER_CHEST)  # 2pc
    gained = swap_completes_threshold(
        equipped, "protection_warrior", "head", _Item(item_id=NON_TIER)
    )
    assert gained is None


# ---------------------------------------------------------------------------
# tier_status_by_item_id — per-card lookup for the paperdoll's own set badge
# (2026-07-28: the header strip's aggregate badge names the set/count once
# for the whole page, but a single gear card gave no hint it was even part
# of that set; a reader had to match item names against the header by eye).
# ---------------------------------------------------------------------------


def test_tier_status_by_item_id_maps_every_active_sets_member_ids():
    """Once ANY piece of a set is equipped, every member id of that set
    (registry-wide, not just the currently-equipped ones) resolves to the
    SAME SetStatus object — a bag/vault swap candidate that's a different
    piece of the same set must still report the real current pieces/
    active_threshold, not just whatever happens to already be worn."""
    equipped = _equipped(NIGHT_ENDER_HEAD, NIGHT_ENDER_CHEST, NIGHT_ENDER_HANDS)
    by_id = tier_status_by_item_id(equipped, "protection_warrior")
    all_night_ender_ids = {s.item_ids for s in load_tier_sets() if s.id == "night_ender_warrior"}
    assert len(all_night_ender_ids) == 1
    assert set(by_id) == all_night_ender_ids.pop()
    assert by_id[NIGHT_ENDER_HEAD] is by_id[NIGHT_ENDER_CHEST] is by_id[NIGHT_ENDER_HANDS]
    assert by_id[NIGHT_ENDER_HEAD].pieces == 3
    assert by_id[NIGHT_ENDER_HEAD].active_threshold == 2
    # A tier piece the character doesn't currently own (legs) is still a
    # legitimate lookup key — the map is keyed off the SET's registry, not
    # just what's equipped, so an owned-but-unequipped or bag/vault swap
    # candidate of the same set still resolves.
    assert by_id[NIGHT_ENDER_LEGS] is by_id[NIGHT_ENDER_HEAD]


def test_tier_status_by_item_id_empty_when_no_set_pieces_equipped():
    equipped = _equipped(NON_TIER)
    assert tier_status_by_item_id(equipped, "protection_warrior") == {}


def test_tier_status_by_item_id_non_member_item_absent():
    """A non-tier item (even one in a tier slot) must not appear as a key —
    only genuine set members do."""
    equipped = _equipped(NIGHT_ENDER_HEAD, NON_TIER)
    by_id = tier_status_by_item_id(equipped, "protection_warrior")
    assert NON_TIER not in by_id
