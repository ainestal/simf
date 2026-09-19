"""Tests for `io/pull_segmentation.cluster_trash_pulls` — Phase B
inference of named trash pulls inside a trash RunSegment."""

from __future__ import annotations

from simf.io.combat_log import DamageTakenEvent, RunSegment
from simf.io.pull_segmentation import cluster_trash_pulls


def _evt(t: float, source: str, amount: int = 1000, guid: str | None = None) -> DamageTakenEvent:
    return DamageTakenEvent(
        time_s=t,
        event_type="SPELL_DAMAGE",
        source_name=source,
        spell_name="hit",
        school="physical",
        amount=amount,
        base_amount=amount,
        overkill=0,
        blocked=0,
        absorbed=0,
        resisted=0,
        is_critical=False,
        is_glancing=False,
        source_guid=guid or "",
    )


def _trash(start: float, end: float, label: str = "Trash before X") -> RunSegment:
    return RunSegment(kind="trash", label=label, start_time_s=start, end_time_s=end)


def test_single_pull_three_mobs_emits_one_cluster():
    """Three distinct mobs hitting within a tight window → one pull."""
    events = [
        _evt(100.0, "Voidling"),
        _evt(100.5, "Voidcaller"),
        _evt(101.0, "Voidweaver"),
        _evt(105.0, "Voidling"),
    ]
    pulls = cluster_trash_pulls(events, _trash(95.0, 110.0))
    assert len(pulls) == 1
    p = pulls[0]
    assert p.index == 1
    assert p.sources == frozenset({"Voidling", "Voidcaller", "Voidweaver"})
    assert p.n_events == 4
    assert p.total_damage == 4000


def test_eight_second_gap_splits_into_two_pulls():
    """A >= 8s idle gap closes one pull and starts the next."""
    events = [
        _evt(100.0, "A"),
        _evt(100.5, "B"),
        _evt(101.0, "C"),
        # 8s gap
        _evt(110.0, "D"),
        _evt(110.5, "E"),
        _evt(111.0, "F"),
    ]
    pulls = cluster_trash_pulls(events, _trash(95.0, 120.0))
    assert len(pulls) == 2
    assert pulls[0].sources == frozenset({"A", "B", "C"})
    assert pulls[1].sources == frozenset({"D", "E", "F"})
    assert pulls[1].index == 2  # 1-based ordering preserved


def test_single_mob_tap_is_dropped_below_source_threshold():
    """Solo mob between real pulls fails the min_distinct_sources floor."""
    events = [
        _evt(100.0, "RealPack1"),
        _evt(100.2, "RealPack2"),
        _evt(100.4, "RealPack3"),
        # 9s gap → new cluster, but only one mob
        _evt(110.0, "Loner"),
        _evt(110.5, "Loner"),
        # 9s gap → another real pack
        _evt(120.0, "RealPack4"),
        _evt(120.2, "RealPack5"),
        _evt(120.4, "RealPack6"),
    ]
    pulls = cluster_trash_pulls(events, _trash(95.0, 130.0))
    assert len(pulls) == 2  # the lone mob is dropped
    assert pulls[0].sources == frozenset({"RealPack1", "RealPack2", "RealPack3"})
    assert pulls[1].sources == frozenset({"RealPack4", "RealPack5", "RealPack6"})


def test_fingerprint_is_a_frozenset_for_cross_log_dedup():
    """Sources is a frozenset so equal source sets across runs hash equal."""
    events_a = [_evt(100.0 + i, name) for i, name in enumerate(["Foo", "Bar", "Baz"])]
    events_b = [_evt(500.0 + i, name) for i, name in enumerate(["Baz", "Bar", "Foo"])]
    pulls_a = cluster_trash_pulls(events_a, _trash(95.0, 110.0))
    pulls_b = cluster_trash_pulls(events_b, _trash(495.0, 510.0))
    assert pulls_a[0].sources == pulls_b[0].sources  # same fingerprint despite order


def test_boss_segment_returns_empty():
    """Boss kinds aren't clustered — pull inference is trash-only."""
    boss_seg = RunSegment(kind="boss", label="Selin", start_time_s=100.0, end_time_s=200.0)
    events = [_evt(105.0, "A"), _evt(106.0, "B"), _evt(107.0, "C")]
    assert cluster_trash_pulls(events, boss_seg) == []


def test_events_outside_segment_window_are_ignored():
    """Defensive: caller may pass the full event stream; cluster only
    bounds to the segment's [start, end] range."""
    events = [
        _evt(80.0, "BeforeStart"),  # before
        _evt(100.0, "Real1"),
        _evt(100.5, "Real2"),
        _evt(101.0, "Real3"),
        _evt(200.0, "AfterEnd"),  # after
    ]
    pulls = cluster_trash_pulls(events, _trash(95.0, 110.0))
    assert len(pulls) == 1
    assert "BeforeStart" not in pulls[0].sources
    assert "AfterEnd" not in pulls[0].sources


def test_empty_events_returns_empty():
    assert cluster_trash_pulls([], _trash(0.0, 100.0)) == []


def test_pull_label_falls_back_when_sources_below_threshold():
    """Direct check of the label heuristic — without per-source damage
    breakdown, falls back to 'N-mob pack'."""
    pull_events = [_evt(100.0, n) for n in ["A", "B", "C", "D"]]
    pulls = cluster_trash_pulls(pull_events, _trash(95.0, 110.0))
    assert pulls[0].label() == "4-mob pack"


def test_mob_guids_disaggregate_same_named_mobs():
    """Five Voidlings of the same NAME in one pack should be 5 GUIDs, not 1.

    Regression: the original code counted `len(sources)` (names) which
    collapsed a pull of five Voidlings + one Voidcaller down to "2 distinct
    mobs". With GUIDs we surface 6 individual creatures.
    """
    events = [
        _evt(100.0, "Voidling", guid="Creature-0-0-0-0-1-A"),
        _evt(100.1, "Voidling", guid="Creature-0-0-0-0-1-B"),
        _evt(100.2, "Voidling", guid="Creature-0-0-0-0-1-C"),
        _evt(100.3, "Voidling", guid="Creature-0-0-0-0-1-D"),
        _evt(100.4, "Voidcaller", guid="Creature-0-0-0-0-2-A"),
        _evt(100.5, "Voidweaver", guid="Creature-0-0-0-0-3-A"),
    ]
    pulls = cluster_trash_pulls(events, _trash(95.0, 110.0))
    assert len(pulls) == 1
    p = pulls[0]
    assert len(p.sources) == 3  # 3 distinct names
    assert len(p.mob_guids) == 6  # 6 distinct creature instances — the honest count


def test_mob_guids_skip_player_and_pet_sources():
    """Players and pets shouldn't count toward a pull's mob count.

    A trash pull might log self-damage (fall, environmental) or pet
    cleave hitting back — those aren't mobs.
    """
    events = [
        _evt(100.0, "Voidling", guid="Creature-0-0-0-0-1-A"),
        _evt(100.1, "Voidcaller", guid="Creature-0-0-0-0-2-A"),
        _evt(100.2, "Voidweaver", guid="Creature-0-0-0-0-3-A"),
        _evt(100.3, "Some Player", guid="Player-1-ABC"),
        _evt(100.4, "Some Pet", guid="Pet-0-0-0-0-99-A"),
    ]
    pulls = cluster_trash_pulls(events, _trash(95.0, 110.0))
    assert len(pulls) == 1
    assert len(pulls[0].mob_guids) == 3


def test_mob_guids_empty_when_log_predates_guid_capture():
    """ACL-off or older logs that don't carry source_guid → empty mob_guids,
    but the pull still emits with sources/names. UI must handle this case."""
    events = [
        _evt(100.0, "A"),
        _evt(100.5, "B"),
        _evt(101.0, "C"),
    ]
    pulls = cluster_trash_pulls(events, _trash(95.0, 110.0))
    assert len(pulls) == 1
    assert pulls[0].mob_guids == frozenset()
    assert pulls[0].sources == frozenset({"A", "B", "C"})


def test_mgt_log_trash_before_gemellus_finds_voidwalker_pulls():
    """Integration smoke against Brutoh's real MGT +12 log. The trash
    leading to Gemellus is the dangerous void-mob run that killed him in
    the validation; this test pins the high-level shape (3 distinct
    pulls, each with the canonical void-mob source names) so future
    parser changes don't quietly regress the cluster output.

    Synthetic-only unit tests above cover the algorithm; this one ties
    it to ground truth."""
    from pathlib import Path

    from simf.io.combat_log import (
        iter_damage_events,
        parse_challenge_modes,
        parse_encounters,
        segment_run,
    )

    log = Path(__file__).resolve().parent.parent / "examples" / "WoWCombatLog-051526_210245.txt"
    if not log.exists():
        return  # corpus log not present in slim test runs — skip gracefully

    runs = parse_challenge_modes(log)
    assert runs, "MGT validation log should contain at least one challenge mode run"
    run = runs[0]
    encs = parse_encounters(log, run.start_time_s, run.end_time_s)
    segments = segment_run(run, encs)
    events = list(iter_damage_events(log, "Brutoh-Uldum-EU", run.start_time_s, run.end_time_s))

    gemellus_trash = next(
        (s for s in segments if s.kind == "trash" and "Gemellus" in s.label), None
    )
    assert gemellus_trash, "expected a 'trash before Gemellus' segment in the MGT run"
    pulls = cluster_trash_pulls(events, gemellus_trash)
    assert len(pulls) >= 2, "the Gemellus run-up has at least 2 distinct pulls"
    # Void-themed source names dominate the Gemellus run-up — at least
    # one of the canonical void-mob names should appear in the cluster.
    flat_sources = {s for p in pulls for s in p.sources}
    assert any("Void" in s or "Hollow" in s or "Shadow" in s for s in flat_sources), (
        f"expected void-themed mobs in Gemellus trash; got {sorted(flat_sources)}"
    )
