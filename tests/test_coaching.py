"""Unit tests for the model-independent defensive-coverage coaching engine.

`core.coaching` is pure — it takes damage events + pre-parsed aura windows and
produces the hit-vs-coverage report. These tests pin the honesty rules
(talent-gating, no deleted abilities, no % in the headline, role-correct
metric) and the join math, all on synthetic data.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from simf.core import coaching
from simf.core.coaching import InterruptAbility, Lever
from simf.core.constants import load_constants
from simf.io.combat_log import DamageTakenEvent


def _ev(amount, time_s, *, school="physical", spell="Hit", spell_id=1, src="Mob", guid="g1"):
    return DamageTakenEvent(
        time_s=time_s,
        event_type="SPELL_DAMAGE",
        source_name=src,
        spell_name=spell,
        school=school,
        amount=amount,
        base_amount=amount,
        overkill=0,
        blocked=0,
        absorbed=0,
        resisted=0,
        is_critical=False,
        is_glancing=False,
        source_guid=guid,
        spell_id=spell_id,
    )


def _no_bleed(_name, _is_periodic=True):  # is_bleed_fn stub
    return False


def _build(
    events,
    *,
    levers,
    buff_windows=None,
    debuff=None,
    run_start=0.0,
    run_end=100.0,
    top_n=8,
    min_events=1,
    interrupted=frozenset(),
    interrupt_ability=None,
    own_interrupt_cast_times=(),
):
    return coaching.build_coverage_report(
        "protection_warrior",
        events,
        levers=levers,
        buff_windows=buff_windows or {},
        debuff_windows_by_source=debuff or {},
        run_start=run_start,
        run_end=run_end,
        is_bleed_fn=_no_bleed,
        top_n=top_n,
        min_events=min_events,
        interrupted_spell_ids=interrupted,
        interrupt_ability=interrupt_ability,
        own_interrupt_cast_times=own_interrupt_cast_times,
    )


SHIELD_WALL = Lever(871, "Shield Wall", "all", "coverage", "buff")
SHIELD_BLOCK = Lever(132404, "Shield Block", "physical", "coverage", "buff")
SHIELD_BLOCK_CONT = Lever(132404, "Shield Block", "physical", "continuous", "buff")
DEMO = Lever(1160, "Demoralizing Shout", "all", "coverage", "debuff_on_source", 8.0, tier="minor")
# Physical-scope MAJOR coverage levers (Guardian / DK) for bleed-gate tests.
INCARN = Lever(102558, "Incarnation", "physical", "coverage", "buff")
PUMMEL = InterruptAbility(6552, "Pummel", 15.0)


# ── school family / scope ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "school,family",
    [
        ("physical", "physical"),
        ("Physical", "physical"),
        ("fire", "magic"),
        ("nature", "magic"),
        ("shadow", "magic"),
        ("arcane", "magic"),
        ("frost", "magic"),
        ("holy", "magic"),
        ("unknown", "unknown"),
        (None, "unknown"),
    ],
)
def test_school_family(school, family):
    assert coaching.school_family(school) == family


def test_scope_covers():
    assert coaching._scope_covers("all", "physical")
    assert coaching._scope_covers("all", "magic")
    assert coaching._scope_covers("physical", "physical")
    assert not coaching._scope_covers("physical", "magic")
    assert coaching._scope_covers("magic", "magic")
    assert not coaching._scope_covers("magic", "physical")


# ── registry parsing + deleted-ability tripwire ──────────────────────────────


def test_levers_for_spec_warrior():
    levers = coaching.levers_for_spec("protection_warrior", load_constants())
    names = {lv.name for lv in levers}
    assert {"Shield Wall", "Last Stand", "Demoralizing Shout", "Shield Block"} <= names
    demo = next(lv for lv in levers if lv.name == "Demoralizing Shout")
    assert demo.detect == "debuff_on_source"
    assert demo.duration_s == 8.0
    sb = next(lv for lv in levers if lv.name == "Shield Block")
    assert sb.role == "continuous"


# ── coaching-lever registry audit, 2026-07-02 (non-Warrior/Guardian specs) ──


def test_levers_for_spec_protection_paladin():
    """Divine Shield stays listed (Final Stand makes it tank-viable); the two
    stale pre-Midnight cooldowns (120s/300s base) are refit to their current
    90s/180s values."""
    levers = coaching.levers_for_spec("protection_paladin", load_constants())
    names = {lv.name for lv in levers}
    assert {
        "Ardent Defender",
        "Guardian of Ancient Kings",
        "Divine Shield",
        "Shield of the Righteous",
    } <= names
    ad = next(lv for lv in levers if lv.name == "Ardent Defender")
    assert ad.cooldown_s == 90
    goak = next(lv for lv in levers if lv.name == "Guardian of Ancient Kings")
    assert goak.cooldown_s == 180
    ds = next(lv for lv in levers if lv.name == "Divine Shield")
    assert ds.role == "coverage" and ds.tier == "major"


def test_levers_for_spec_blood_death_knight():
    """Dancing Rune Weapon's parry-chance DR is a shave, not a wall (minor,
    not major); Icebound Fortitude's base cooldown is 120s, not 180s; Anti-Magic
    Zone and Rune Tap are new minor coverage levers found by the 2026-07-02 audit."""
    levers = coaching.levers_for_spec("blood_death_knight", load_constants())
    names = {lv.name for lv in levers}
    assert {
        "Vampiric Blood",
        "Icebound Fortitude",
        "Dancing Rune Weapon",
        "Anti-Magic Shell",
        "Bone Shield",
        "Anti-Magic Zone",
        "Rune Tap",
    } <= names
    ibf = next(lv for lv in levers if lv.name == "Icebound Fortitude")
    assert ibf.cooldown_s == 120
    drw = next(lv for lv in levers if lv.name == "Dancing Rune Weapon")
    assert drw.tier == "minor"
    amz = next(lv for lv in levers if lv.name == "Anti-Magic Zone")
    assert amz.school_scope == "magic" and amz.tier == "minor"
    rune_tap = next(lv for lv in levers if lv.name == "Rune Tap")
    assert rune_tap.tier == "minor"


def test_levers_for_spec_vengeance_demon_hunter():
    """Fiery Brand (the known gap) is now present as a self-buff, not a debuff
    on the enemy; Metamorphosis is physical-scope only (it's +200% armor, no
    flat all-school DR line); Netherwalk was cut from the kit and must stay gone."""
    levers = coaching.levers_for_spec("vengeance_demon_hunter", load_constants())
    names = {lv.name for lv in levers}
    assert {"Metamorphosis", "Fiery Brand", "Demon Spikes"} <= names
    assert "Netherwalk" not in names

    meta = next(lv for lv in levers if lv.name == "Metamorphosis")
    assert meta.school_scope == "physical"

    brand = next(lv for lv in levers if lv.name == "Fiery Brand")
    assert brand.detect == "buff"
    assert brand.school_scope == "all"
    assert brand.tier == "major"


def test_levers_for_spec_brewmaster_monk():
    """Celestial Brew is a reactive absorb-shield CD, not a keep-it-up tool —
    it must carry role: coverage (+ a cooldown_s) so it lands in the
    hit-vs-coverage join instead of being silently invisible to it."""
    levers = coaching.levers_for_spec("brewmaster_monk", load_constants())
    brew = next(lv for lv in levers if lv.name == "Celestial Brew")
    assert brew.role == "coverage"
    assert brew.cooldown_s is not None


def test_levers_for_spec_unknown_returns_empty():
    assert coaching.levers_for_spec(None, load_constants()) == []
    assert coaching.levers_for_spec("not_a_spec", load_constants()) == []


def test_rage_of_the_sleeper_is_not_in_any_registry():
    """200851 was deleted from the game in 12.0.0 — it must never be coached."""
    C = load_constants()
    for spec, rows in (C["coaching"]["defensives"]).items():
        for r in rows:
            assert int(r["spell_id"]) != 200851, f"phantom RotS in {spec}"


def test_coaching_module_has_no_phantom_spell_literal():
    """Defensive tripwire: 200851 must never appear as a live code literal.

    The honesty docstring is allowed to mention it as a warning (it's a string
    constant), but an `int` 200851 anywhere in real code would mean the deleted
    Rage of the Sleeper crept back in. An AST walk distinguishes the two.
    """
    import ast

    tree = ast.parse(Path(coaching.__file__).read_text())
    int_literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, int)
    ]
    assert 200851 not in int_literals


# ── talent-gating ────────────────────────────────────────────────────────────


def test_talent_gate_drops_undetected_levers():
    """A coverage lever with no windows is not considered at all."""
    events = [_ev(100, 10), _ev(90, 20)]
    # Shield Wall is in the registry but has NO windows → must be dropped.
    rep = _build(events, levers=[SHIELD_WALL], buff_windows={})
    assert rep.coverage_levers_considered == ()
    assert not rep.has_data  # nothing to coach on → soft path
    assert "no major defensive" in rep.headline.lower()


def test_talent_gate_keeps_detected_levers():
    events = [_ev(100, 10)]
    rep = _build(events, levers=[SHIELD_WALL], buff_windows={871: [(5.0, 15.0)]})
    assert rep.coverage_levers_considered == ("Shield Wall",)
    assert rep.has_data


# ── the coverage join ────────────────────────────────────────────────────────


def test_buff_join_covered_and_uncovered():
    inside = _ev(100, 10)
    outside = _ev(90, 50)
    rep = _build([inside, outside], levers=[SHIELD_WALL], buff_windows={871: [(5.0, 15.0)]})
    by_time = {h.rel_s: h for h in rep.top_hits}
    assert by_time[10.0].covered_by == ("Shield Wall",)
    assert by_time[50.0].covered_by == ()
    assert rep.n_covered == 1
    assert rep.n_total == 2


def test_school_scope_blocks_wrong_school():
    """A physical-only lever cannot cover a magic hit."""
    fire_hit = _ev(100, 10, school="fire")
    rep = _build([fire_hit], levers=[SHIELD_BLOCK], buff_windows={132404: [(5.0, 15.0)]})
    assert rep.top_hits[0].covered_by == ()  # physical scope, fire hit
    # same window, physical hit → covered
    phys_hit = _ev(100, 10, school="physical")
    rep2 = _build([phys_hit], levers=[SHIELD_BLOCK], buff_windows={132404: [(5.0, 15.0)]})
    assert rep2.top_hits[0].covered_by == ("Shield Block",)


def test_debuff_on_source_only_credits_the_right_mob():
    """Demo Shout covers a hit only if THAT hit's source carried the debuff."""
    hit_a = _ev(100, 10, src="A", guid="mobA")
    hit_b = _ev(90, 12, src="B", guid="mobB")
    rep = _build(
        [hit_a, hit_b],
        levers=[DEMO],
        debuff={1160: {"mobA": [(5.0, 18.0)]}},  # only mobA debuffed
    )
    by_guid = {h.source_name: h for h in rep.top_hits}
    assert by_guid["A"].covered_by == ("Demoralizing Shout",)
    assert by_guid["B"].covered_by == ()


# ── verdict text (answer-first, NO percentage in the headline) ────────────────


def test_headline_never_contains_a_percent():
    events = [_ev(100, 10), _ev(90, 50), _ev(80, 60)]
    rep = _build(events, levers=[SHIELD_WALL], buff_windows={871: [(5.0, 15.0)]})
    assert "%" not in rep.headline
    assert not re.search(r"\d+%", rep.headline)


def test_verdict_all_covered():
    events = [_ev(100, 10), _ev(90, 12)]
    rep = _build(events, levers=[SHIELD_WALL], buff_windows={871: [(5.0, 30.0)]})
    assert rep.n_uncovered == 0
    assert "Every one of your 2 biggest hits" in rep.headline
    assert "Shield Wall" in rep.detail


def test_verdict_credits_covered_and_lists_uncovered_times():
    events = [_ev(100, 10), _ev(90, 200), _ev(80, 210)]
    rep = _build(events, levers=[SHIELD_WALL], buff_windows={871: [(5.0, 15.0)]}, run_end=300.0)
    assert "ate 2 of your 3 biggest hits" in rep.headline
    assert "1/3 were covered by Shield Wall" in rep.detail
    # uncovered at 200s = 3:20 and 210s = 3:30, chronological
    assert "3:20" in rep.detail and "3:30" in rep.detail


def test_uncovered_times_chronological_and_capped():
    # 5 uncovered hits at 60,70,80,90,100s; only 3 shown + "+2 more"
    events = [_ev(100 - i, t) for i, t in enumerate([60, 70, 80, 90, 100])]
    rep = _build(events, levers=[SHIELD_WALL], buff_windows={871: [(0.0, 1.0)]}, run_end=300.0)
    assert "1:00, 1:10, 1:20, +2 more" in rep.detail


# ── continuous uptime (role-correct metric) ──────────────────────────────────


def test_continuous_lever_is_uptime_not_coverage():
    """A continuous lever must not enter the coverage join, only the uptime line."""
    phys = _ev(100, 50, school="physical")
    rep = _build(
        [phys], levers=[SHIELD_BLOCK_CONT], buff_windows={132404: [(0.0, 25.0)]}, run_end=100.0
    )
    # Not counted as a coverage lever:
    assert rep.coverage_levers_considered == ()
    assert not rep.has_data  # no coverage levers → soft path
    # But its uptime IS surfaced:
    assert len(rep.continuous) == 1
    assert rep.continuous[0].name == "Shield Block"
    assert rep.continuous[0].uptime_pct == pytest.approx(25.0)


def test_continuous_uptime_clamps_to_run_window():
    rep = _build(
        [_ev(100, 10)],
        levers=[SHIELD_WALL, SHIELD_BLOCK_CONT],
        buff_windows={871: [(0.0, 5.0)], 132404: [(-10.0, 200.0)]},
        run_start=0.0,
        run_end=100.0,
    )
    cont = rep.continuous[0]
    assert cont.uptime_pct == pytest.approx(100.0)  # clamped to [0, 100]


# ── degenerate inputs ────────────────────────────────────────────────────────


# ── minor-tier (partial) coverage — Demoralizing Shout must not read as a soak ──


def test_minor_only_hit_is_partial_not_clean_covered():
    """A hit covered ONLY by a minor lever (Demo Shout −20%) is `partial`, not a
    clean major soak, and must not be counted in n_covered."""
    hit = _ev(100, 10, src="A", guid="mobA")
    rep = _build([hit], levers=[DEMO], debuff={1160: {"mobA": [(5.0, 18.0)]}})
    h = rep.top_hits[0]
    assert h.partial and not h.covered
    assert h.minor_by == ("Demoralizing Shout",)
    assert h.major_by == ()
    assert rep.n_covered == 0  # minor-only is NOT a clean soak
    assert rep.n_uncovered == 0  # but also NOT counted as "nothing up"
    assert rep.n_partial == 1


def test_minor_only_does_not_claim_every_hit_had_major_defensive():
    """The over-credit the review caught: all-minor coverage must NOT flip the
    verdict to 'every hit had a major defensive up'."""
    hits = [_ev(100, 10, guid="m"), _ev(90, 12, guid="m")]
    rep = _build(hits, levers=[DEMO], debuff={1160: {"m": [(0.0, 30.0)]}})
    assert "major defensive up" not in rep.headline.lower()
    assert "clean coverage" not in rep.detail.lower()
    # but it is honest that something partial was up (not "you ate ... nothing")
    assert "ate" not in rep.headline.lower()


def test_major_plus_minor_credits_major_and_notes_partial():
    covered = _ev(100, 10, guid="m")  # under Shield Wall
    partial = _ev(90, 40, guid="m")  # only Demo Shout
    rep = _build(
        [covered, partial],
        levers=[SHIELD_WALL, DEMO],
        buff_windows={871: [(5.0, 15.0)]},
        debuff={1160: {"m": [(35.0, 48.0)]}},
    )
    assert rep.n_covered == 1 and rep.n_partial == 1 and rep.n_uncovered == 0
    assert "Shield Wall" in rep.detail
    assert "Demoralizing Shout" in rep.detail  # partial credited separately


# ── bleed gate — armor/parry levers can't cover armor-bypassing bleeds ─────────


def test_physical_scope_lever_does_not_cover_a_bleed():
    """Incarnation (physical/armor) must NOT 'cover' a bleed tick (bleeds bypass
    armor). Only an all-scope %DR lever covers a bleed."""
    bleed = _ev(100, 10, school="physical", spell="Rip")
    rep = coaching.build_coverage_report(
        "guardian_druid",
        [bleed],
        levers=[INCARN],
        buff_windows={102558: [(5.0, 15.0)]},
        debuff_windows_by_source={},
        run_start=0.0,
        run_end=100.0,
        is_bleed_fn=lambda name, is_periodic=True: name == "Rip",
        top_n=8,
        min_events=1,
    )
    assert rep.top_hits[0].covered_by == ()  # bleed not covered by armor lever


def test_physical_scope_lever_covers_a_nonbleed_physical_hit():
    phys = _ev(100, 10, school="physical", spell="Cleave")
    rep = coaching.build_coverage_report(
        "guardian_druid",
        [phys],
        levers=[INCARN],
        buff_windows={102558: [(5.0, 15.0)]},
        debuff_windows_by_source={},
        run_start=0.0,
        run_end=100.0,
        is_bleed_fn=lambda name, is_periodic=True: name == "Rip",
        top_n=8,
        min_events=1,
    )
    assert rep.top_hits[0].covered_by == ("Incarnation",)


# ── interruptible-cast lever ─────────────────────────────────────────────────


def test_hit_flagged_interruptible_when_spell_id_in_evidence_set():
    ev = _ev(500_000, 10, spell="Icy Blast", spell_id=396640)
    rep = _build([ev], levers=[SHIELD_WALL], interrupted={396640})
    assert rep.top_hits[0].interruptible is True
    assert rep.n_interruptible == 1


def test_hit_not_flagged_without_evidence():
    ev = _ev(500_000, 10, spell="Icy Blast", spell_id=396640)
    rep = _build([ev], levers=[SHIELD_WALL], interrupted=frozenset())
    assert rep.top_hits[0].interruptible is False
    assert rep.n_interruptible == 0


def test_interruptible_set_only_matches_exact_spell_id():
    ev = _ev(500_000, 10, spell="Icy Blast", spell_id=396640)
    # A different spell_id was interrupted elsewhere — must not bleed onto this hit.
    rep = _build([ev], levers=[SHIELD_WALL], interrupted={111111})
    assert rep.top_hits[0].interruptible is False


def test_interruptible_is_independent_of_defensive_coverage():
    """A hit can be BOTH covered by a major defensive AND interruptible — the
    two signals are orthogonal, not mutually exclusive."""
    ev = _ev(500_000, 10, spell="Icy Blast", spell_id=396640)
    rep = _build(
        [ev],
        levers=[SHIELD_WALL],
        buff_windows={871: [(5.0, 15.0)]},
        interrupted={396640},
    )
    hit = rep.top_hits[0]
    assert hit.covered is True
    assert hit.interruptible is True


def test_default_interrupted_set_is_empty_and_backward_compatible():
    """Callers that don't pass `interrupted_spell_ids` (every pre-existing
    caller) must get bit-identical behavior — interruptible always False."""
    ev = _ev(500_000, 10, spell="Icy Blast", spell_id=396640)
    rep = coaching.build_coverage_report(
        "protection_warrior",
        [ev],
        levers=[SHIELD_WALL],
        buff_windows={},
        debuff_windows_by_source={},
        run_start=0.0,
        run_end=100.0,
        is_bleed_fn=_no_bleed,
    )
    assert rep.top_hits[0].interruptible is False


# ── kick-availability split (cooldown-aware, PR follow-up 2026-07-03) ───────


def test_interrupt_ability_for_spec_warrior():
    ability = coaching.interrupt_ability_for_spec("protection_warrior", load_constants())
    assert ability == InterruptAbility(6552, "Pummel", 15.0)


def test_interrupt_ability_for_spec_unknown_returns_none():
    assert coaching.interrupt_ability_for_spec("frost_mage", load_constants()) is None
    assert coaching.interrupt_ability_for_spec(None, load_constants()) is None


@pytest.mark.parametrize(
    "spec",
    [
        "protection_warrior",
        "protection_paladin",
        "blood_death_knight",
        "vengeance_demon_hunter",
        "brewmaster_monk",
        "guardian_druid",
    ],
)
def test_interrupt_ability_registered_for_every_modeled_tank_spec(spec):
    ability = coaching.interrupt_ability_for_spec(spec, load_constants())
    assert ability is not None
    assert ability.spell_id > 0
    assert ability.name
    assert ability.cooldown_s > 0


def test_kick_was_ready_true_when_never_cast():
    """Never having cast the interrupt reads as always-available — a true
    mechanical fact, independent of whether NOT using it was a good call."""
    ev = _ev(500_000, 100, spell="Icy Blast", spell_id=396640)
    rep = _build([ev], levers=[SHIELD_WALL], interrupted={396640}, interrupt_ability=PUMMEL)
    hit = rep.top_hits[0]
    assert hit.kick_was_ready is True
    assert hit.kick_missed is True
    assert rep.n_kick_ready == 1
    assert rep.n_kick_on_cooldown == 0


def test_kick_was_ready_true_when_cast_long_enough_ago():
    ev = _ev(500_000, 100, spell="Icy Blast", spell_id=396640)
    rep = _build(
        [ev],
        levers=[SHIELD_WALL],
        interrupted={396640},
        interrupt_ability=PUMMEL,
        own_interrupt_cast_times=(80.0,),  # 20s before the hit, cooldown is 15s
    )
    assert rep.top_hits[0].kick_was_ready is True


def test_kick_was_ready_false_when_cast_recently():
    ev = _ev(500_000, 100, spell="Icy Blast", spell_id=396640)
    rep = _build(
        [ev],
        levers=[SHIELD_WALL],
        interrupted={396640},
        interrupt_ability=PUMMEL,
        own_interrupt_cast_times=(90.0,),  # 10s before the hit, cooldown is 15s
    )
    hit = rep.top_hits[0]
    assert hit.kick_was_ready is False
    assert hit.kick_on_cooldown is True
    assert hit.kick_missed is False
    assert rep.n_kick_ready == 0
    assert rep.n_kick_on_cooldown == 1


def test_kick_was_ready_none_when_no_interrupt_ability_known():
    """Not-assessable stays None, never a guessed True/False."""
    ev = _ev(500_000, 100, spell="Icy Blast", spell_id=396640)
    rep = _build([ev], levers=[SHIELD_WALL], interrupted={396640}, interrupt_ability=None)
    assert rep.top_hits[0].kick_was_ready is None
    assert rep.n_kick_ready == 0
    assert rep.n_kick_on_cooldown == 0


def test_kick_was_ready_none_when_hit_not_interruptible():
    """The split only applies to hits already proven interruptible — a
    non-interruptible hit never gets a kick_was_ready verdict either way."""
    ev = _ev(500_000, 100, spell="Icy Blast", spell_id=396640)
    rep = _build(
        [ev],
        levers=[SHIELD_WALL],
        interrupted=frozenset(),  # not proven interruptible
        interrupt_ability=PUMMEL,
        own_interrupt_cast_times=(80.0,),
    )
    assert rep.top_hits[0].interruptible is False
    assert rep.top_hits[0].kick_was_ready is None


def test_interrupt_ability_name_on_report():
    ev = _ev(500_000, 100, spell="Icy Blast", spell_id=396640)
    rep = _build([ev], levers=[SHIELD_WALL], interrupted={396640}, interrupt_ability=PUMMEL)
    assert rep.interrupt_ability_name == "Pummel"

    rep_none = _build([ev], levers=[SHIELD_WALL], interrupted={396640})
    assert rep_none.interrupt_ability_name is None


def test_kick_availability_default_backward_compatible():
    """Callers that don't pass interrupt_ability/own_interrupt_cast_times
    (every pre-existing caller) get bit-identical behavior."""
    ev = _ev(500_000, 100, spell="Icy Blast", spell_id=396640)
    rep = coaching.build_coverage_report(
        "protection_warrior",
        [ev],
        levers=[SHIELD_WALL],
        buff_windows={},
        debuff_windows_by_source={},
        run_start=0.0,
        run_end=200.0,
        is_bleed_fn=_no_bleed,
        interrupted_spell_ids={396640},
    )
    assert rep.top_hits[0].kick_was_ready is None
    assert rep.interrupt_ability_name is None


# ── robustness ─────────────────────────────────────────────────────────────────


def test_run_end_none_does_not_crash():
    """A truncated log (run_end=None) must not crash — fall back to last event."""
    rep = coaching.build_coverage_report(
        "protection_warrior",
        [_ev(100, 10), _ev(90, 50)],
        levers=[SHIELD_WALL, SHIELD_BLOCK_CONT],
        buff_windows={871: [(5.0, 15.0)], 132404: [(0.0, 40.0)]},
        debuff_windows_by_source={},
        run_start=0.0,
        run_end=None,
        is_bleed_fn=_no_bleed,
        top_n=8,
        min_events=1,
    )
    assert rep.n_total == 2
    # uptime computed against the fallback window without dividing by None
    assert 0.0 <= rep.continuous[0].uptime_pct <= 100.0


# ── registry hygiene ───────────────────────────────────────────────────────────


def test_registry_rows_use_only_valid_enum_values():
    """A typo in role/detect/school_scope/tier must fail CI, not silently no-op."""
    C = load_constants()
    for spec, rows in C["coaching"]["defensives"].items():
        for r in rows:
            assert r["role"] in {"coverage", "continuous"}, (spec, r)
            assert r["detect"] in {"buff", "debuff_on_source"}, (spec, r)
            assert r["school_scope"] in {"all", "physical", "magic"}, (spec, r)
            assert r.get("tier", "major") in {"major", "minor"}, (spec, r)
            assert isinstance(r["spell_id"], int) and r["spell_id"] > 0, (spec, r)
            if r["detect"] == "debuff_on_source":
                assert r.get("duration_s"), (spec, r)  # needs a safety cap


def test_too_short_run():
    rep = _build(
        [_ev(100, 10)], levers=[SHIELD_WALL], buff_windows={871: [(5.0, 15.0)]}, min_events=20
    )
    assert rep.too_short
    assert not rep.has_data
    assert "Too few hits" in rep.headline


def test_no_coverage_levers_with_continuous_credits_not_blames():
    """A tank who soaked the run on continuous mitigation and pressed no
    emergency CD must be CREDITED, not told 'weren't pressed' — and the
    measured continuous uptime must not be discarded."""
    events = [_ev(100, 10), _ev(90, 20)]
    rep = _build(
        events, levers=[SHIELD_BLOCK_CONT], buff_windows={132404: [(0.0, 50.0)]}, min_events=1
    )
    assert not rep.has_data
    assert "ate" not in rep.headline.lower()
    assert "weren't pressed" not in rep.detail.lower()
    # continuous uptime is preserved (not thrown away) and credited
    assert rep.continuous and rep.continuous[0].name == "Shield Block"
    assert "shield block" in rep.detail.lower()


def test_no_levers_at_all_is_soft_not_blaming():
    """With NO levers detected and NO continuous data, stay soft (no 'N/N up')."""
    events = [_ev(100, 10), _ev(90, 20)]
    rep = _build(events, levers=[SHIELD_WALL], buff_windows={}, min_events=1)
    assert not rep.has_data
    assert "ate" not in rep.headline.lower()
    assert "no major defensive" in rep.headline.lower()


# ── availability split: "kit was spent" vs "you had one ready" ───────────────
#
# A hit you ate with your whole major kit on cooldown is a pacing/pull problem,
# NOT a reaction miss — it must not read as "you should have pressed something."
# Cooldown-bearing levers (cooldown_s set) opt a hit into the split; a lever
# without cooldown_s sits it out (cd_assessable stays False → original wording).

SW_CD = Lever(871, "Shield Wall", "all", "coverage", "buff", cooldown_s=240)
INCARN_CD = Lever(102558, "Incarnation", "physical", "coverage", "buff", cooldown_s=180)


def test_lever_available_at():
    assert coaching._lever_available_at([], 100.0, 240.0)  # never cast → ready
    assert coaching._lever_available_at([(0.0, 8.0)], 300.0, 240.0)  # 300s ago → ready
    assert not coaching._lever_available_at([(290.0, 298.0)], 300.0, 240.0)  # 10s ago → on CD
    assert coaching._lever_available_at([(0.0, 8.0)], 240.0, 240.0)  # exact boundary → ready


def test_split_had_cd_available():
    # Shield Wall cast once at t=0 (so it's DETECTED), big uncovered hit at t=300
    # — 300s later, well past its 240 CD → it was ready and the player ate it.
    events = [_ev(500, 300)]
    rep = _build(events, levers=[SW_CD], buff_windows={871: [(0.0, 8.0)]}, run_end=400.0)
    h = rep.top_hits[0]
    assert h.uncovered and h.cd_assessable
    assert h.had_cd_available and not h.kit_spent
    assert h.available_major_levers == ("Shield Wall",)
    assert rep.n_uncovered_had_cd == 1
    assert "had a major cooldown ready" in rep.headline
    assert "Shield Wall was off cooldown" in rep.detail


def test_split_kit_spent():
    # Shield Wall cast at t=290 (8s buff), uncovered hit at t=300 — 10s after the
    # cast, deep inside the 240 CD → nothing left to press.
    events = [_ev(500, 300)]
    rep = _build(events, levers=[SW_CD], buff_windows={871: [(290.0, 298.0)]}, run_end=400.0)
    h = rep.top_hits[0]
    assert h.uncovered and h.cd_assessable
    assert h.kit_spent and not h.had_cd_available
    assert rep.n_uncovered_kit_spent == 1
    assert "already spent" in rep.headline
    assert "pacing" in rep.detail.lower()
    # Must NOT tell the player to pre-press a cooldown that wasn't available.
    assert "pre-press" not in rep.detail.lower()


def test_split_physical_lever_not_credited_available_for_magic_hit():
    # Incarnation (physical-scope) is OFF cooldown but the hit is frost — it can
    # never cover magic, so it must NOT count as an available lever. With the only
    # all-scope major (Shield Wall) on cooldown, the hit reads as kit_spent.
    events = [_ev(500, 300, school="frost")]
    rep = _build(
        events,
        levers=[SW_CD, INCARN_CD],
        buff_windows={871: [(290.0, 298.0)], 102558: [(0.0, 8.0)]},
        run_end=400.0,
    )
    h = rep.top_hits[0]
    assert h.kit_spent
    assert h.available_major_levers == ()


def test_split_had_cd_available_pluralizes_two_lever_names():
    """Round-3 review (2026-07-05): the detail sentence hardcoded singular
    "was"/"it" regardless of how many lever names got joined — reproduced
    live on two independent real logs as "Barkskin and Survival Instincts
    WAS off cooldown." Both Shield Wall (all-scope) and Incarnation
    (physical-scope) are off cooldown for this physical hit, so
    `available_major_levers` carries two names and the sentence must
    agree: "were" / "them", not "was" / "it"."""
    events = [_ev(500, 300)]
    rep = _build(
        events,
        levers=[SW_CD, INCARN_CD],
        buff_windows={871: [(0.0, 8.0)], 102558: [(0.0, 8.0)]},
        run_end=400.0,
    )
    h = rep.top_hits[0]
    assert h.had_cd_available
    assert set(h.available_major_levers) == {"Shield Wall", "Incarnation"}
    assert "were off cooldown" in rep.detail
    assert "was off cooldown" not in rep.detail
    assert "pre-press them" in rep.detail
    assert "pre-press it " not in rep.detail


def test_split_mixed_had_cd_and_kit_spent():
    # Two uncovered hits, one cast of Shield Wall at t=5. The t=10 hit is 5s later
    # (on CD → kit_spent); the t=300 hit is 295s later (ready → had_cd).
    events = [_ev(500, 300), _ev(450, 10)]
    rep = _build(events, levers=[SW_CD], buff_windows={871: [(5.0, 9.0)]}, run_end=400.0)
    by_t = {h.rel_s: h for h in rep.top_hits}
    assert by_t[10.0].kit_spent
    assert by_t[300.0].had_cd_available
    assert rep.n_uncovered_had_cd == 1 and rep.n_uncovered_kit_spent == 1
    assert "had a major cooldown ready" in rep.headline
    assert "already on cooldown" in rep.detail  # the spent-note for the other hit


# ── pressed-late cross-check (validator finding, 2026-07-08/09) ─────────────
#
# `_lever_available_at` infers "still on cooldown" purely from the registry's
# `cooldown_s` — it never looks at what the player actually did right after
# the hit. A real log showed Shield Wall cast 0.81s and 2.45s AFTER two
# flagged hits (direct proof it was NOT stuck deep in a long cooldown), yet
# the old classification called both hits `kit_spent` and told the player to
# "hold a major for these spikes" — the exact opposite of the real lesson
# (late reaction / window timing). These tests pin the fix: a lever's real
# cast landing shortly after an uncovered hit must falsify `kit_spent` and
# must never produce an "already spent" headline.


def test_pressed_late_falsifies_kit_spent():
    # Shield Wall cast at t=300.81 — 0.81s AFTER the uncovered hit at t=300.
    # No earlier cast at all, so the base-CD model alone would have called
    # this "never cast before -> ready" (had_cd), NOT kit_spent — use a
    # prior cast far enough back that the base-CD model itself would (wrongly)
    # call the lever on cooldown, isolating the cast-evidence cross-check.
    events = [_ev(500, 300)]
    rep = _build(
        events,
        levers=[SW_CD],
        buff_windows={871: [(100.0, 108.0), (300.81, 308.81)]},
        run_end=400.0,
    )
    h = rep.top_hits[0]
    # Base-CD model alone (100 -> 300 = 200s < 240s CD) would say "on
    # cooldown" — but the 0.81s-later cast directly falsifies that.
    assert not coaching._lever_available_at([(100.0, 108.0)], 300.0, 240.0)
    assert h.uncovered and h.cd_assessable
    assert h.available_major_levers == ()  # the base-CD model still says "no"
    assert h.pressed_late_levers == ("Shield Wall",)
    assert h.pressed_late
    assert not h.kit_spent  # the cast evidence overrides the base-CD guess
    assert not h.had_cd_available
    assert rep.n_uncovered_kit_spent == 0
    assert rep.n_pressed_late == 1
    assert "already spent" not in rep.headline
    assert "already spent" not in rep.detail
    assert "hold a major" not in rep.detail.lower()
    assert "just after the hit landed" in rep.headline


def test_pressed_late_two_hits_matches_real_log_shape():
    # Mirrors the real-log finding: Shield Wall re-cast 0.81s after one hit
    # and 2.45s after another, with no other coverage in between.
    events = [_ev(500, 100), _ev(480, 300)]
    rep = _build(
        events,
        levers=[SW_CD],
        buff_windows={871: [(100.81, 108.81), (302.45, 310.45)]},
        run_end=400.0,
    )
    for h in rep.top_hits:
        assert h.pressed_late and not h.kit_spent
    assert rep.n_pressed_late == 2
    assert rep.n_uncovered_kit_spent == 0
    assert "already spent" not in rep.headline
    assert "already spent" not in rep.detail


def test_pressed_late_does_not_trigger_when_cast_is_well_after():
    # A cast 40s after the hit is not "pressed late" — it's an unrelated,
    # later press. Falls back to the original kit_spent wording.
    events = [_ev(500, 300)]
    rep = _build(
        events,
        levers=[SW_CD],
        buff_windows={871: [(100.0, 108.0), (340.0, 348.0)]},
        run_end=400.0,
    )
    h = rep.top_hits[0]
    assert h.pressed_late_levers == ()
    assert not h.pressed_late
    assert h.kit_spent
    assert "already spent" in rep.headline


def test_pressed_late_does_not_trigger_when_cast_is_before_the_hit():
    # A cast strictly before the hit (even if very close) is coverage/
    # availability territory, not "pressed late" — guards the boundary.
    events = [_ev(500, 300)]
    rep = _build(
        events,
        levers=[SW_CD],
        buff_windows={871: [(100.0, 108.0), (299.9, 300.0)]},
        run_end=400.0,
    )
    h = rep.top_hits[0]
    assert h.pressed_late_levers == ()


def test_cd_model_uncertain_softens_kit_spent_claim():
    # Shield Wall's registry cooldown_s is 240s, but THIS run recast it only
    # 20s apart (100 -> 120) — direct evidence the base-CD assumption is
    # wrong for this run. A later hit that the model calls "kit spent" must
    # now read as uncertain, not a flat "already spent" pacing verdict.
    events = [_ev(500, 150)]
    rep = _build(
        events,
        levers=[SW_CD],
        buff_windows={871: [(100.0, 105.0), (120.0, 125.0)]},
        run_end=400.0,
    )
    h = rep.top_hits[0]
    assert h.kit_spent  # no pressed-late evidence for THIS hit specifically
    assert rep.cd_model_uncertain_levers == ("Shield Wall",)
    assert "already spent" not in rep.headline
    assert "unreliable" in rep.headline.lower()
    assert "uncertain" in rep.detail.lower()


def test_cd_model_uncertain_stays_empty_with_a_single_cast():
    # Only one cast on record — no gap to measure, so the model can't be
    # judged unreliable from this run alone. Matches `test_split_kit_spent`.
    events = [_ev(500, 300)]
    rep = _build(events, levers=[SW_CD], buff_windows={871: [(290.0, 298.0)]}, run_end=400.0)
    assert rep.cd_model_uncertain_levers == ()
    assert "already spent" in rep.headline


def test_split_absent_without_cooldown_data():
    # A lever with no cooldown_s sits the split out — original wording, no
    # "kit spent"/"ready" claim (fail-safe for specs lacking cooldown data).
    events = [_ev(500, 300)]
    rep = _build(events, levers=[SHIELD_WALL], buff_windows={871: [(0.0, 8.0)]}, run_end=400.0)
    h = rep.top_hits[0]
    assert not h.cd_assessable
    assert not h.kit_spent and not h.had_cd_available
    assert "no defensive cooldown up" in rep.headline


# ── partial-with-major-ready — a benign "partial" must not mask a real miss ────


def test_partial_with_major_ready_is_flagged():
    """A partial hit (Demo Shout −20% only) where a MAJOR (Shield Wall) was off
    cooldown is actionable — not a benign "partial". Shield Wall cast at t=0
    (detected, past its 240 CD by t=300); Demo Shout up at t=300."""
    partial = _ev(500, 300, guid="m")
    rep = _build(
        [partial],
        levers=[SW_CD, DEMO],
        buff_windows={871: [(0.0, 8.0)]},
        debuff={1160: {"m": [(295.0, 310.0)]}},
        run_end=400.0,
    )
    h = rep.top_hits[0]
    assert h.partial and not h.covered and not h.uncovered
    assert h.partial_major_ready
    assert h.available_major_levers == ("Shield Wall",)
    assert rep.n_partial_major_ready == 1
    # The uncovered-only split stays honest: this is NOT a fully-uncovered hit.
    assert not h.had_cd_available and not h.kit_spent
    assert rep.n_uncovered_had_cd == 0
    # Verdict leads with the actionable miss and names the ready major.
    assert "ready" in rep.headline.lower() and "major" in rep.headline.lower()
    assert "Shield Wall" in rep.detail


def test_partial_with_major_on_cooldown_stays_benign():
    """Same partial hit, but Shield Wall was already spent (cast 5s prior) → NOT
    flagged; reads as a plain partial with no 'major ready' claim."""
    partial = _ev(500, 300, guid="m")
    rep = _build(
        [partial],
        levers=[SW_CD, DEMO],
        buff_windows={871: [(295.0, 298.0)]},
        debuff={1160: {"m": [(295.0, 310.0)]}},
        run_end=400.0,
    )
    h = rep.top_hits[0]
    assert h.partial and not h.partial_major_ready
    assert h.available_major_levers == ()
    assert rep.n_partial_major_ready == 0
    assert "ready" not in rep.detail.lower()


def test_registry_levers_carry_cooldown():
    levers = coaching.levers_for_spec("guardian_druid", load_constants())
    si = next(lv for lv in levers if lv.name == "Survival Instincts")
    assert si.cooldown_s == 180
    bark = next(lv for lv in levers if lv.name == "Barkskin")
    assert bark.cooldown_s == 60
    # Warrior too — the other verified spec.
    wlevers = coaching.levers_for_spec("protection_warrior", load_constants())
    sw = next(lv for lv in wlevers if lv.name == "Shield Wall")
    assert sw.cooldown_s == 240
