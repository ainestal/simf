"""Tests for the slot-dialog gem-suggestion display rows (build_gem_rows).

Pure presentation adapter over optimizer.gem_suggester — the survival scoring
is tested in test_gem_suggester; here we pin the per-slot filtering, the
current/recommended labels, the ΔeHP formatting, and the caveats.
"""

from dataclasses import dataclass, field

from simf.optimizer.gem_suggester import GemCandidate, GemSuggestion
from simf.ui.helpers.gem_panel import (
    META_DROP_NOTE,
    _row_from_suggestion,
    build_gem_rows,
    build_gem_rows_all,
    build_gem_rows_by_slot,
    gem_card_notes,
    gem_section_notes,
)

EVERSONG = 240983  # +32 primary, unique
MASTERFUL_PERIDOT = 240892  # +16 haste, +7 mastery
VERSATILE_PERIDOT = 240894  # +16 haste, +7 vers
VERSATILE_LAPIS = 240912  # +17 vers


def _guardian_ec_marg():
    return {
        "stamina": {"p": 20.0, "m": 16.0},
        "armor_from_gear": {"p": 5.0, "m": 0.0},
        "versatility_rating": {"p": 6.0, "m": 5.0},
        "haste_rating": {"p": 10.0, "m": 0.0},
        "crit_rating": {"p": 0.0, "m": 0.0},
        "mastery_rating": {"p": 0.0, "m": 0.0},
        "strength": {"p": 0.0, "m": 0.0},
        "agility": {"p": 18.0, "m": 0.0},
    }


@dataclass
class _Item:
    slot: str
    gem_ids: list = field(default_factory=list)


def _anonguardian1_equipped():
    return {
        "neck": _Item("neck", [EVERSONG]),
        "finger1": _Item("finger1", [MASTERFUL_PERIDOT]),
        "finger2": _Item("finger2", [VERSATILE_PERIDOT]),
        "head": _Item("head", []),  # no sockets
    }


def test_no_rows_for_socketless_slot():
    rows = build_gem_rows("head", _anonguardian1_equipped(), _guardian_ec_marg(), "guardian_druid")
    assert rows == []
    rows = build_gem_rows("waist", _anonguardian1_equipped(), _guardian_ec_marg(), "guardian_druid")
    assert rows == []


def test_neck_eversong_is_optimal_kept():
    rows = build_gem_rows("neck", _anonguardian1_equipped(), _guardian_ec_marg(), "guardian_druid")
    assert len(rows) == 1
    r = rows[0]
    assert r.is_optimal  # keeps the meta, no churn
    assert r.best_label is None
    assert "Eversong Diamond" in r.current_label
    # current_gem_id is what the card Wowhead-links when optimal (best_gem_id
    # stays None here, mirroring best_label's None-when-optimal convention).
    assert r.current_gem_id == EVERSONG
    assert r.best_gem_id is None


def test_finger_masterful_peridot_gets_upgrade_with_delta_and_caveat():
    rows = build_gem_rows(
        "finger1", _anonguardian1_equipped(), _guardian_ec_marg(), "guardian_druid"
    )
    assert len(rows) == 1
    r = rows[0]
    assert not r.is_optimal
    assert r.best_label is not None
    assert r.delta_ehp > 0
    assert r.delta_label.startswith("+") and "eHP" in r.delta_label
    # The recommended gem carries haste → the Elune's-Chosen caveat must show.
    assert any("Elune's Chosen" in c for c in r.caveats)
    # best_gem_id is what the card Wowhead-links for the upgrade — a real,
    # different id from what's currently socketed (not just a copy of it).
    assert r.best_gem_id is not None and r.best_gem_id != MASTERFUL_PERIDOT


def test_unique_meta_caveat_when_meta_recommended():
    """A socket with no meta anywhere → the meta is recommended somewhere and
    carries the unique caveat."""
    equipped = {
        "finger1": _Item("finger1", [VERSATILE_LAPIS]),
        "finger2": _Item("finger2", [VERSATILE_LAPIS]),
    }
    # Across both ring sockets, the agi Eversong (meta) wins one socket.
    rows_f1 = build_gem_rows("finger1", equipped, _guardian_ec_marg(), "guardian_druid")
    rows_f2 = build_gem_rows("finger2", equipped, _guardian_ec_marg(), "guardian_druid")
    all_rows = rows_f1 + rows_f2
    meta_rows = [r for r in all_rows if r.best_label and "Eversong Diamond" in r.best_label]
    assert len(meta_rows) == 1
    assert any("Unique" in c for c in meta_rows[0].caveats)


def _warrior_marg():
    # Strength carries little/no survival here so the primary-stat Eversong meta
    # is dropped for a vers gem — exercises the meta-drop path.
    return {
        "stamina": {"p": 20.0, "m": 16.0},
        "armor_from_gear": {"p": 5.0, "m": 0.0},
        "versatility_rating": {"p": 6.0, "m": 5.0},
        "haste_rating": {"p": 0.0, "m": 0.0},
        "crit_rating": {"p": 0.0, "m": 0.0},
        "mastery_rating": {"p": 0.0, "m": 0.0},
        "strength": {"p": 0.0, "m": 0.0},
        "agility": {"p": 0.0, "m": 0.0},
    }


def test_current_is_meta_flagged_when_meta_dropped():
    equipped = {
        "neck": _Item("neck", [EVERSONG]),  # str-meta, worth ~0 survival here
        "finger1": _Item("finger1", [VERSATILE_LAPIS]),
    }
    rows = build_gem_rows("neck", equipped, _warrior_marg(), "protection_warrior")
    assert len(rows) == 1
    r = rows[0]
    assert r.current_is_meta is True
    assert not r.is_optimal  # the throughput meta is dropped for a survival gem
    assert r.delta_ehp > 0


def test_current_is_meta_false_for_non_meta_gem():
    equipped = {"finger1": _Item("finger1", [VERSATILE_PERIDOT])}
    rows = build_gem_rows("finger1", equipped, _warrior_marg(), "protection_warrior")
    assert rows and rows[0].current_is_meta is False


# --- gem_section_notes: spec/build-level honesty caveats -------------------


def test_section_notes_guardian_ec_mastery_only():
    """Elune's-Chosen Guardian (haste valued): mastery-not-modeled note only —
    no Druid-of-the-Claw haste steering (haste IS valued)."""
    notes = gem_section_notes("guardian_druid", _guardian_ec_marg())
    assert any("mastery" in n.lower() for n in notes)
    assert not any("druid of the claw" in n.lower() for n in notes)


def test_section_notes_guardian_dotc_adds_haste_steering():
    """Non-Elune's-Chosen Guardian (haste worth 0): both the mastery note AND
    the 'haste does nothing' steering."""
    non_ec = _guardian_ec_marg()
    non_ec["haste_rating"] = {"p": 0.0, "m": 0.0}
    notes = gem_section_notes("guardian_druid", non_ec)
    assert any("mastery" in n.lower() for n in notes)
    assert any("haste does nothing" in n.lower() for n in notes)


def test_section_notes_empty_for_non_guardian():
    assert gem_section_notes("protection_warrior", _warrior_marg()) == []


def test_section_notes_mastery_note_suppressed_when_mastery_is_scoring():
    """2026-07-05 gem-trust review: this note fired unconditionally and went
    stale the moment mastery got real survival credit — directly contradicting
    a mastery gem the same screen labels 'optimal'. Must gate on the live
    marginal exactly like the haste note does."""
    marg = _guardian_ec_marg()
    marg["mastery_rating"] = {"p": 400.0, "m": 50.0}
    notes = gem_section_notes("guardian_druid", marg)
    assert not any("mastery" in n.lower() for n in notes)


# --- _row_from_suggestion: "optimal" must not conflate two different truths


def _candidate(item_id, name, stats, unique=False):
    return GemCandidate(item_id=item_id, name=name, stats=stats, unique_equipped=unique)


def test_below_threshold_real_gap_is_kept_not_identity_optimal():
    """A real, computed gap exists but is smaller than the swap-worthiness
    bar: `is_optimal` is True (nothing forced as an action) but
    `is_identity_optimal` is False — callers MUST render this as a neutral
    'kept' state, never a confident 'optimal' badge, and it must still name
    the real best candidate (found on AnonGuardian1's live rings: equipped gems
    scored thousands of eHP below the model's actual best pick, both still
    labeled 'optimal')."""
    best = _candidate(
        999, "Flawless Masterful Lapis", {"versatility_rating": 16, "mastery_rating": 7}
    )
    current = _candidate(
        111, "Flawless Versatile Peridot", {"haste_rating": 16, "versatility_rating": 7}
    )
    s = GemSuggestion(
        slot="finger1",
        index=0,
        current_gem_id=111,
        current_name=current.name,
        current_known=True,
        current_value=2911.0,
        best=best,
        best_value=8381.0,
        delta_ehp=5470.0,
        is_meta_socket=False,
        ranked=[(best, 8381.0), (current, 2911.0)],
    )
    row = _row_from_suggestion(s, "guardian_druid", optimal_epsilon=10_000.0)
    assert row.is_optimal
    assert not row.is_identity_optimal
    assert row.best_label == "Flawless Masterful Lapis"
    assert row.best_gem_id == 999
    assert row.delta_ehp == 5470.0


def test_empty_socket_below_threshold_is_never_identity_optimal():
    """The most damaging surfaced instance (ui-craft-critic, 2026-07-05): an
    empty, ungemmed socket rendered '💎 empty socket · optimal'. An empty
    socket's current_gem_id is None, which can never equal a real best gem's
    item_id, so it must never be identity-optimal — callers branch on that,
    not on `is_optimal` alone, to avoid calling nothing "the best"."""
    best = _candidate(
        999, "Flawless Masterful Lapis", {"versatility_rating": 16, "mastery_rating": 7}
    )
    s = GemSuggestion(
        slot="head",
        index=0,
        current_gem_id=None,
        current_name=None,
        current_known=True,
        current_value=0.0,
        best=best,
        best_value=500.0,
        delta_ehp=500.0,
        is_meta_socket=False,
        ranked=[(best, 500.0)],
    )
    row = _row_from_suggestion(s, "guardian_druid", optimal_epsilon=10_000.0)
    assert row.current_label == "empty socket"
    assert not row.is_identity_optimal
    assert row.best_label == "Flawless Masterful Lapis"


def test_empty_socket_ignores_the_baseline_relative_epsilon():
    """2026-08-07 fix: an empty socket has zero opportunity cost to fill, so
    it no longer gets the caller's baseline-relative "meaningful upgrade"
    grace — a real 500 eHP gem is actionable here even though it's far below
    a 10,000 eHP bar (typical of a well-geared character's total-eHP-relative
    threshold). Before the fix this rendered "kept, below swap bar" forever
    for every unfilled socket on such a character."""
    best = _candidate(
        999, "Flawless Masterful Lapis", {"versatility_rating": 16, "mastery_rating": 7}
    )
    s = GemSuggestion(
        slot="head",
        index=0,
        current_gem_id=None,
        current_name=None,
        current_known=True,
        current_value=0.0,
        best=best,
        best_value=500.0,
        delta_ehp=500.0,
        is_meta_socket=False,
        ranked=[(best, 500.0)],
    )
    row = _row_from_suggestion(s, "guardian_druid", optimal_epsilon=10_000.0)
    assert not row.is_optimal


def test_empty_socket_still_below_the_absolute_floor_is_optimal():
    """The absolute floor itself is near-zero, not zero — a best candidate
    worth less than it (a near-impossible but well-defined edge case) still
    gets the below-bar grace rather than a false "actionable" claim."""
    best = _candidate(999, "Trivial Gem", {"versatility_rating": 1})
    s = GemSuggestion(
        slot="head",
        index=0,
        current_gem_id=None,
        current_name=None,
        current_known=True,
        current_value=0.0,
        best=best,
        best_value=0.1,
        delta_ehp=0.1,
        is_meta_socket=False,
        ranked=[(best, 0.1)],
    )
    row = _row_from_suggestion(s, "guardian_druid", optimal_epsilon=10_000.0)
    assert row.is_optimal
    assert not row.is_identity_optimal


def test_unrecognized_current_gem_never_gets_kept_grace_under_large_epsilon():
    """Sibling to enchant_panel's `current_known` gate (commit 19023d3): an
    unrecognized currently-socketed gem scores `current_value=0` by
    construction (gem_suggester can't price a gem it doesn't know), so its
    `delta_ehp` is not a trustworthy "how close is current to best" signal —
    granting it the epsilon grace produced a live, confusing 'unrecognized
    gem · kept — best: X, below swap bar' line (a real gap stated with false
    confidence about a gem the model never actually compared). Must fall
    through to the actionable-swap render instead, same as
    `is_optimal=False` for any other real gap."""
    best = _candidate(
        999, "Flawless Masterful Lapis", {"versatility_rating": 16, "mastery_rating": 7}
    )
    s = GemSuggestion(
        slot="neck",
        index=0,
        current_gem_id=405863,  # not in the catalog
        current_name=None,
        current_known=False,
        current_value=0.0,
        best=best,
        best_value=327.0,
        delta_ehp=327.0,
        is_meta_socket=False,
        ranked=[(best, 327.0)],
    )
    row = _row_from_suggestion(s, "protection_warrior", optimal_epsilon=100_000.0)
    assert row.current_label == "unrecognized gem"
    assert row.is_optimal is False, "unrecognized gem must never read 'kept'/'optimal'"
    assert row.is_identity_optimal is False
    assert row.best_label == "Flawless Masterful Lapis"
    assert row.delta_ehp == 327.0


def test_runner_up_label_names_the_next_distinct_candidate():
    """2026-07-05 review (engaged_tank): `GemSuggestion.ranked` already holds
    every candidate's score but was discarded at render — the runner-up
    answers 'why did the next-best option lose'."""
    best = _candidate(1, "Best Gem", {"versatility_rating": 17})
    runner = _candidate(2, "Runner Gem", {"mastery_rating": 17})
    current = _candidate(3, "Current Gem", {"haste_rating": 17})
    s = GemSuggestion(
        slot="finger1",
        index=0,
        current_gem_id=3,
        current_name=current.name,
        current_known=True,
        current_value=100.0,
        best=best,
        best_value=500.0,
        delta_ehp=400.0,
        is_meta_socket=False,
        ranked=[(best, 500.0), (runner, 300.0), (current, 100.0)],
    )
    row = _row_from_suggestion(s, "guardian_druid", optimal_epsilon=1.0)
    assert "Runner Gem" in row.runner_up_label
    # Delta against current (300 - 100 = 200), NOT the raw absolute score
    # (300) — the label must be comparable to `delta_label`'s own unit, or a
    # bigger absolute score reads as "the runner-up wins" when it doesn't.
    assert "+200" in row.runner_up_label
    assert "300" not in row.runner_up_label


def test_runner_up_label_empty_when_no_third_distinct_candidate():
    best = _candidate(1, "Best Gem", {"versatility_rating": 17})
    current = _candidate(3, "Current Gem", {"haste_rating": 17})
    s = GemSuggestion(
        slot="finger1",
        index=0,
        current_gem_id=3,
        current_name=current.name,
        current_known=True,
        current_value=100.0,
        best=best,
        best_value=500.0,
        delta_ehp=400.0,
        is_meta_socket=False,
        ranked=[(best, 500.0), (current, 100.0)],
    )
    row = _row_from_suggestion(s, "guardian_druid", optimal_epsilon=1.0)
    assert row.runner_up_label == ""


def test_unrecognized_and_empty_labels():
    equipped = {
        "neck": _Item("neck", [405863]),  # non-catalog gem
        "finger1": _Item("finger1", []),  # socketless here
    }
    rows = build_gem_rows("neck", equipped, _guardian_ec_marg(), "guardian_druid")
    assert len(rows) == 1
    assert rows[0].current_label == "unrecognized gem"


# --- build_gem_rows_by_slot: per-slot groups for the paperdoll card --------


def test_rows_by_slot_omits_socketless_items():
    equipped = {"head": _Item("head", [])}
    by_slot = build_gem_rows_by_slot(equipped, _guardian_ec_marg(), "guardian_druid")
    assert by_slot == {}


def test_rows_by_slot_single_socket_slot_has_one_row():
    equipped = {"neck": _Item("neck", [EVERSONG])}
    by_slot = build_gem_rows_by_slot(equipped, _guardian_ec_marg(), "guardian_druid")
    assert list(by_slot) == ["neck"]
    assert len(by_slot["neck"]) == 1
    assert by_slot["neck"][0].is_optimal is True


def test_rows_by_slot_multi_socket_item_keeps_one_row_per_socket():
    """A two-socket item groups to TWO rows under its slot, in socket order —
    no lossy aggregate, so a card can name each socket's own gem."""
    equipped = {"chest": _Item("chest", [EVERSONG, MASTERFUL_PERIDOT])}
    by_slot = build_gem_rows_by_slot(equipped, _guardian_ec_marg(), "guardian_druid")
    assert set(by_slot) == {"chest"}
    rows = by_slot["chest"]
    assert len(rows) == 2
    assert [r.index for r in rows] == [0, 1]
    assert rows[0].is_optimal is True  # Eversong kept
    assert rows[1].is_optimal is False  # Masterful Peridot upgrades


def test_rows_by_slot_matches_build_gem_rows_all():
    """Cross-check against the source of truth: every row build_gem_rows_all
    returns shows up exactly once, under its own slot."""
    equipped = _anonguardian1_equipped()
    marg = _guardian_ec_marg()
    rows = build_gem_rows_all(equipped, marg, "guardian_druid")
    by_slot = build_gem_rows_by_slot(equipped, marg, "guardian_druid")
    flattened = [r for group in by_slot.values() for r in group]
    assert sorted(flattened, key=lambda r: (r.slot, r.index)) == sorted(
        rows, key=lambda r: (r.slot, r.index)
    )


# --- gem_card_notes: once-per-sheet honesty captions for the card paperdoll


def test_card_notes_includes_guardian_spec_notes():
    equipped = {"finger1": _Item("finger1", [VERSATILE_PERIDOT])}  # no meta involved
    marg = _guardian_ec_marg()
    by_slot = build_gem_rows_by_slot(equipped, marg, "guardian_druid")
    notes = gem_card_notes("guardian_druid", marg, by_slot)
    assert any("mastery" in n.lower() for n in notes)
    assert META_DROP_NOTE not in notes  # nothing meta-related dropped here


def test_card_notes_adds_meta_drop_note_when_a_meta_is_dropped():
    equipped = {"neck": _Item("neck", [EVERSONG]), "finger1": _Item("finger1", [VERSATILE_LAPIS])}
    marg = _warrior_marg()  # strength worthless here → Eversong gets dropped
    by_slot = build_gem_rows_by_slot(equipped, marg, "protection_warrior")
    notes = gem_card_notes("protection_warrior", marg, by_slot)
    assert META_DROP_NOTE in notes


def test_card_notes_no_meta_drop_note_when_meta_is_kept():
    equipped = _anonguardian1_equipped()  # Guardian keeps the agi Eversong (optimal)
    marg = _guardian_ec_marg()
    by_slot = build_gem_rows_by_slot(equipped, marg, "guardian_druid")
    notes = gem_card_notes("guardian_druid", marg, by_slot)
    assert META_DROP_NOTE not in notes


def test_card_notes_dedupes_per_row_caveats():
    """Two rings both independently recommending a haste-carrying gem would
    otherwise duplicate the Elune's-Chosen caveat — gem_card_notes shows it
    once. Zeroing agility keeps the Eversong meta out of contention (else it
    could claim one ring instead, leaving only one caveat-bearing row to begin
    with — not actually exercising dedup)."""
    marg = _guardian_ec_marg()
    marg["agility"] = {"p": 0.0, "m": 0.0}
    equipped = {
        "finger1": _Item("finger1", [MASTERFUL_PERIDOT]),
        "finger2": _Item("finger2", [MASTERFUL_PERIDOT]),
    }
    by_slot = build_gem_rows_by_slot(equipped, marg, "guardian_druid")
    rows = by_slot["finger1"] + by_slot["finger2"]
    assert all(not r.is_optimal and "Elune's Chosen" in " ".join(r.caveats) for r in rows), (
        "test setup must produce two independent EC-caveat rows to test dedup"
    )
    notes = gem_card_notes("guardian_druid", marg, by_slot)
    ec_notes = [n for n in notes if "Elune's Chosen" in n]
    assert len(ec_notes) == 1
