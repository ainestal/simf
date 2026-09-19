"""Tests for the gear-card / slot-dialog enchant display rows
(build_enchant_rows*).

Pure presentation adapter over optimizer.enchant_suggester — the survival
scoring is tested in test_enchant_suggester; here we pin the per-slot
filtering, the current/recommended labels, the "not modeled" framing, the
"no enchant exists" honesty caption, the live-Blizzard-name preference, and
the once-per-sheet honesty note.
"""

from simf.ui.helpers.enchant_panel import (
    NOT_MODELED_NOTE,
    build_enchant_rows,
    build_enchant_rows_all,
    build_enchant_rows_by_slot,
    enchant_card_notes,
)

WORLDSOUL = 7987
NALORAKK_LIKE_UNCONFIRMED = None  # Mark of Nalorakk has no confirmed id yet
FOREST_HUNTER = 8159
SILVERMOON_ALACRITY = 8025
HEX_OF_LEECHING = 7961


def _warrior_marg():
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


class _Item:
    def __init__(self, slot, enchant_id=None, enchant_name=None):
        self.slot = slot
        self.enchant_id = enchant_id
        self.enchant_name = enchant_name


def _brutoh_equipped():
    return {
        "chest": _Item("chest", WORLDSOUL),
        "finger1": _Item("finger1", SILVERMOON_ALACRITY),
        "head": _Item("head", HEX_OF_LEECHING),
        "back": _Item("back", None),  # confirmed no-enchant slot — still renders a row
        "off_hand": _Item("off_hand", None),  # shield for a warrior — structurally excluded
    }


def test_no_rows_for_structurally_excluded_slot():
    """A shield off_hand is the real "nothing to show" case — never had an
    enchant slot in any WoW expansion, unlike back/wrist/neck (which now get
    an honest caption instead of silence)."""
    rows = build_enchant_rows("off_hand", _brutoh_equipped(), _warrior_marg(), "protection_warrior")
    assert rows == []


def test_no_enchant_exists_slot_renders_honest_caption_not_silence():
    """back/wrist/neck/waist/hands used to render nothing at all, which read
    as broken. They must now render exactly one row with a plain, distinct
    caption — not silence, not a crash, and not the generic 'not modeled'
    wording (that's reserved for slots with a real catalog that just scores
    ~0)."""
    for slot in ("back", "wrist", "neck", "waist", "hands"):
        equipped = {slot: _Item(slot, None)}
        rows = build_enchant_rows(slot, equipped, _warrior_marg(), "protection_warrior")
        assert len(rows) == 1, f"{slot}: expected exactly one row, got {rows}"
        r = rows[0]
        assert r.no_enchant_exists is True
        assert r.modeled is False
        assert r.best_label is None
        assert r.delta_label == "no enchant exists"


def test_modeled_slot_recommends_switch_with_delta():
    rows = build_enchant_rows("chest", _brutoh_equipped(), _warrior_marg(), "protection_warrior")
    assert len(rows) == 1
    r = rows[0]
    assert r.modeled is True
    assert not r.is_optimal
    assert r.best_label == "Mark of Nalorakk"
    assert r.delta_ehp > 0
    assert r.delta_label.startswith("+") and "eHP" in r.delta_label
    # Nalorakk's id is unconfirmed — the row must not fabricate one.
    assert r.best_enchant_id is None
    # A genuinely modeled, cataloged slot must never trip the honesty flag.
    assert r.no_enchant_exists is False


def test_unmodeled_slot_shows_no_pick_and_not_modeled_label():
    rows = build_enchant_rows("head", _brutoh_equipped(), _warrior_marg(), "protection_warrior")
    assert len(rows) == 1
    r = rows[0]
    assert r.modeled is False
    assert r.best_label is None
    assert r.delta_label == "not modeled"
    assert r.current_label == "Empowered Hex of Leeching"
    # A cataloged-but-zero-value slot is not the "no enchant exists" case.
    assert r.no_enchant_exists is False


def test_current_label_prefers_live_blizzard_name_over_catalog():
    equipped = {
        "chest": _Item("chest", WORLDSOUL, enchant_name="Mark of the Worldsoul (rank 3)"),
    }
    rows = build_enchant_rows("chest", equipped, _warrior_marg(), "protection_warrior")
    assert rows[0].current_label == "Mark of the Worldsoul (rank 3)"


def test_current_label_live_name_used_even_when_id_unrecognized():
    """The live name is ground truth regardless of whether our small static
    catalog happens to know the numeric id — strictly better than the
    catalog-only 'unrecognized enchant' fallback."""
    equipped = {"feet": _Item("feet", 55555, enchant_name="Some New Boot Enchant")}
    rows = build_enchant_rows("feet", equipped, _warrior_marg(), "protection_warrior")
    assert rows[0].current_label == "Some New Boot Enchant"


def test_no_enchant_and_unrecognized_labels():
    equipped = {
        "chest": _Item("chest", None),  # empty
        "feet": _Item("feet", 999999999),  # non-catalog id
    }
    rows = build_enchant_rows("chest", equipped, _warrior_marg(), "protection_warrior")
    assert rows[0].current_label == "no enchant"
    rows = build_enchant_rows("feet", equipped, _warrior_marg(), "protection_warrior")
    assert rows[0].current_label == "unrecognized enchant"


# --- build_enchant_rows_by_slot: flat dict for the paperdoll card ----------


def test_rows_by_slot_is_flat_dict_keyed_by_slot():
    by_slot = build_enchant_rows_by_slot(_brutoh_equipped(), _warrior_marg(), "protection_warrior")
    # "back" is present (honest caption) now; "off_hand" (a shield here)
    # stays absent — the structural exclusion this bug never touched.
    assert set(by_slot) == {"chest", "finger1", "head", "back"}
    assert by_slot["head"].modeled is False
    assert by_slot["back"].no_enchant_exists is True
    assert by_slot["chest"].best_label == "Mark of Nalorakk"


def test_rows_by_slot_matches_build_enchant_rows_all():
    equipped = _brutoh_equipped()
    marg = _warrior_marg()
    rows = build_enchant_rows_all(equipped, marg, "protection_warrior")
    by_slot = build_enchant_rows_by_slot(equipped, marg, "protection_warrior")
    assert {r.slot for r in rows} == set(by_slot)
    for r in rows:
        assert by_slot[r.slot] == r


# --- enchant_card_notes: once-per-sheet honesty caption --------------------


def test_card_notes_flags_when_any_slot_unmodeled():
    by_slot = build_enchant_rows_by_slot(_brutoh_equipped(), _warrior_marg(), "protection_warrior")
    assert enchant_card_notes(by_slot) == [NOT_MODELED_NOTE]


def test_card_notes_not_triggered_by_no_enchant_exists_alone():
    """back/wrist/neck/waist/hands already explain themselves inline on their
    own row — a sheet with only those (no genuinely 'cataloged but ~0'
    slot) must not also surface the generic NOT_MODELED_NOTE."""
    equipped = {
        "chest": _Item("chest", WORLDSOUL),
        "back": _Item("back", None),
        "waist": _Item("waist", None),
    }
    by_slot = build_enchant_rows_by_slot(equipped, _warrior_marg(), "protection_warrior")
    assert by_slot["back"].no_enchant_exists is True
    assert by_slot["waist"].no_enchant_exists is True
    assert enchant_card_notes(by_slot) == []


def test_card_notes_empty_when_every_slot_modeled():
    equipped = {
        "chest": _Item("chest", WORLDSOUL),
        "finger1": _Item("finger1", SILVERMOON_ALACRITY),
    }
    by_slot = build_enchant_rows_by_slot(equipped, _warrior_marg(), "protection_warrior")
    assert enchant_card_notes(by_slot) == []


def test_card_notes_empty_for_no_slots():
    assert enchant_card_notes({}) == []


# --- is_optimal / is_identity_optimal under a large (paperdoll-style)
# epsilon: a real 2026-07 validation-round bug ------------------------------
#
# `build_enchant_rows_by_slot`'s live caller (the Gear-tab paperdoll) passes
# the same large, baseline-relative "meaningful upgrade" epsilon the
# item-swap gate uses — thousands of eHP, dwarfing a single enchant's own
# value. Before the fix, `is_optimal`'s epsilon clause fired regardless of
# whether the current enchant was ever actually identified, so BOTH an
# unrecognized real-world enchant (current_value forced to 0) and a
# genuinely empty-but-modeled slot rendered a confident "optimal" badge —
# reverting the `current_known`/`current_enchant_id is not None` gate below
# reproduces the bug and fails these tests.
_LARGE_PAPERDOLL_EPSILON = 100_000.0  # dwarfs any single enchant's own ΔeHP


def test_unrecognized_current_enchant_never_reports_optimal_under_large_epsilon():
    """The live repro: a real character's uncataloged enchant must never
    read 'optimal' just because the paperdoll's meaningful-upgrade epsilon
    is large relative to one enchant's value — it must surface the real
    modeled recommendation instead."""
    equipped = {"finger1": _Item("finger1", 999999999)}  # not in the catalog
    rows = build_enchant_rows(
        "finger1",
        equipped,
        _warrior_marg(),
        "protection_warrior",
        optimal_epsilon=_LARGE_PAPERDOLL_EPSILON,
    )
    assert len(rows) == 1
    r = rows[0]
    assert r.current_label == "unrecognized enchant"
    assert r.is_optimal is False, "unrecognized enchant must never read 'optimal'"
    assert r.is_identity_optimal is False
    # The real modeled recommendation must still surface, not be swallowed.
    assert r.best_label is not None
    assert r.delta_ehp > 0
    assert r.delta_label.startswith("+") and "eHP" in r.delta_label


def test_empty_modeled_slot_is_never_identity_optimal_under_large_epsilon():
    """Secondary check (same class of bug PR #276 fixed for gems): an EMPTY
    but modeled slot must never be indistinguishable from a genuine 'best
    pick' — leaving a slot unenchanted is never actually optimal once a real
    (if small) recommendation exists. Unlike a gem socket (which has a
    dedicated 'kept, below bar' render state), an enchant row has no third
    state yet, so `is_optimal` is also forced False here — the row renders
    as the honest, already-existing 'actionable swap' line instead of a bare
    'optimal' badge."""
    equipped = {"feet": _Item("feet", None)}  # genuinely empty, feet is stamina-modeled
    rows = build_enchant_rows(
        "feet",
        equipped,
        _warrior_marg(),
        "protection_warrior",
        optimal_epsilon=_LARGE_PAPERDOLL_EPSILON,
    )
    assert len(rows) == 1
    r = rows[0]
    assert r.current_label == "no enchant"
    assert r.modeled is True
    assert r.is_identity_optimal is False, "an empty slot is never genuinely the model's best pick"
    assert r.is_optimal is False, "an empty modeled slot must never read 'optimal'"
    assert r.best_label == "Shaladrassil's Roots"
    assert r.delta_ehp > 0


def test_genuinely_identity_optimal_enchant_still_reports_optimal():
    """The fix must not throw out the legitimate case: a KNOWN current
    enchant that really is the model's best (or beats the whole catalog)
    still reads as a confident 'optimal', even under a large epsilon."""
    haste_dominant_marg = {
        "stamina": {"p": 0.0, "m": 0.0},
        "armor_from_gear": {"p": 0.0, "m": 0.0},
        "versatility_rating": {"p": 0.0, "m": 0.0},
        "haste_rating": {"p": 50.0, "m": 40.0},
        "crit_rating": {"p": 0.0, "m": 0.0},
        "mastery_rating": {"p": 0.0, "m": 0.0},
        "strength": {"p": 0.0, "m": 0.0},
        "agility": {"p": 0.0, "m": 0.0},
    }
    equipped = {"finger1": _Item("finger1", SILVERMOON_ALACRITY)}
    rows = build_enchant_rows(
        "finger1",
        equipped,
        haste_dominant_marg,
        "protection_warrior",
        optimal_epsilon=_LARGE_PAPERDOLL_EPSILON,
    )
    assert len(rows) == 1
    r = rows[0]
    assert r.is_identity_optimal is True
    assert r.is_optimal is True
    assert r.best_label is None
    assert r.delta_ehp == 0.0
