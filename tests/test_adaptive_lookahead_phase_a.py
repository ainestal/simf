"""Adaptive-lookahead Phase A — plumbing tests.

Phase A wires an optional ``upcoming_events`` slice from the runner
into ``policy.decide()`` so a future Phase B can react to imminent
damage spikes rather than only the current event + recent_dtps. This
file pins the plumbing contract: helper math, signature acceptance,
runner threading. No policy consumes the slice today — bit-identity
at every pinned seed is the load-bearing invariant and is exercised
by the existing test suite (which still passes against this branch).
"""

from __future__ import annotations

from unittest.mock import patch

from simf.classes.blood_death_knight import BloodDKPolicy
from simf.classes.brewmaster_monk import BrewmasterPolicy
from simf.classes.guardian_druid import GuardianPolicy
from simf.classes.protection_paladin import ProtPalPolicy
from simf.classes.vengeance_dh import VengeanceDHPolicy
from simf.core.character import Character
from simf.core.events import DamageEvent
from simf.core.mitigation import MitigationState
from simf.core.policy import ActiveMitigationPolicy
from simf.core.profiles import HealingProfile
from simf.core.runner import run_simulation
from simf.core.timeline import events_in_window


def _ev(t: float, raw: float = 100.0, tb: bool = False) -> DamageEvent:
    return DamageEvent(
        time_s=t,
        source_id="t",
        school="physical",
        raw_amount=raw,
        attack_type="melee",
        is_avoidable=False,
        is_blockable=False,
        is_tank_buster=tb,
    )


# ─── events_in_window helper ──────────────────────────────────────────────────


def test_events_in_window_returns_events_after_current_within_horizon() -> None:
    """The slice starts AFTER ``current_idx`` (current event isn't 'upcoming')
    and ends at ``time + window_s``, inclusive."""
    events = [_ev(0.0), _ev(1.0), _ev(2.0), _ev(3.0), _ev(5.0)]
    out = events_in_window(events, current_idx=1, window_s=2.0)
    assert [e.time_s for e in out] == [2.0, 3.0]


def test_events_in_window_horizon_is_inclusive_at_the_boundary() -> None:
    """An event at exactly ``time + window_s`` IS in the slice — matches
    SimC's ``<=`` semantics for tank-buster reaction windows."""
    events = [_ev(0.0), _ev(3.0)]
    out = events_in_window(events, current_idx=0, window_s=3.0)
    assert [e.time_s for e in out] == [3.0]


def test_events_in_window_zero_window_returns_empty() -> None:
    """The runner uses ``window_s=0`` as the OFF switch — the helper must
    short-circuit allocation rather than scanning the event list."""
    events = [_ev(0.0), _ev(1.0), _ev(2.0)]
    assert events_in_window(events, current_idx=0, window_s=0.0) == []
    assert events_in_window(events, current_idx=0, window_s=-1.0) == []


def test_events_in_window_handles_end_of_pull() -> None:
    """At the tail of the event list, the slice is empty — the policy gets
    no false signal that something's coming when nothing is."""
    events = [_ev(0.0), _ev(1.0), _ev(2.0)]
    assert events_in_window(events, current_idx=2, window_s=3.0) == []


def test_events_in_window_handles_out_of_range_idx() -> None:
    """Negative or past-end indices must not raise — defensive against
    a caller that passes ``current_idx - 1`` during init."""
    events = [_ev(0.0), _ev(1.0)]
    assert events_in_window(events, current_idx=-1, window_s=3.0) == []
    assert events_in_window(events, current_idx=99, window_s=3.0) == []


def test_events_in_window_preserves_tank_buster_flag() -> None:
    """Phase B will key off ``is_tank_buster`` — the slice must carry
    the field through verbatim rather than constructing wrapper objects
    that drop it."""
    events = [_ev(0.0), _ev(1.5, tb=True), _ev(2.5), _ev(3.0)]
    out = events_in_window(events, current_idx=0, window_s=3.0)
    assert [e.is_tank_buster for e in out] == [True, False, False]


# ─── decide() signatures accept upcoming_events kw across every spec ─────────


def _phys_event() -> DamageEvent:
    return DamageEvent(
        time_s=0.0,
        source_id="t",
        school="physical",
        raw_amount=1000.0,
        attack_type="melee",
        is_avoidable=False,
        is_blockable=False,
    )


def _char(spec: str) -> Character:
    return Character(
        name="t",
        race="orc",
        class_spec=spec,
        talents="brutoh-actual" if spec == "protection_warrior" else "default-paladin",
        strength=2000,
        stamina=50000,
        armor_from_gear=4000,
    )


def test_active_mitigation_policy_decide_accepts_upcoming_events_kw() -> None:
    """Phase A signature contract — the new kw is positional-or-keyword
    so the runner's keyword call site doesn't break."""
    char = _char("protection_warrior")
    policy = ActiveMitigationPolicy(char)
    state = MitigationState(char)
    state.talents = set()
    # Smoke: no exception when called with the new kw + non-empty list.
    policy.decide(
        state,
        now=1.0,
        recent_dtps=0.0,
        incoming_event=_phys_event(),
        upcoming_events=[_ev(2.0), _ev(3.0)],
    )


def test_every_spec_policy_decide_accepts_upcoming_events_kw() -> None:
    """Every tank spec's decide() must accept the new keyword.

    A spec policy that DIDN'T add the parameter would silently fall
    through to a TypeError in production for that spec. This loop
    catches the drift across all 5 spec modules in one place.
    """
    for spec, cls in [
        ("protection_paladin", ProtPalPolicy),
        ("blood_death_knight", BloodDKPolicy),
        ("vengeance_demon_hunter", VengeanceDHPolicy),
        ("brewmaster_monk", BrewmasterPolicy),
        ("guardian_druid", GuardianPolicy),
    ]:
        char = _char(spec)
        # Spec modules have varying constructor signatures; instantiate
        # consistently as the make_policy dispatch already does.
        policy = cls(char)
        state = MitigationState(char)
        state.talents = set()
        policy.decide(
            state,
            now=1.0,
            recent_dtps=0.0,
            incoming_event=_phys_event(),
            upcoming_events=[_ev(2.0)],
        )


# ─── runner threads the slice (default-ON via YAML constant) ─────────────────


def test_runner_threads_non_empty_upcoming_events_when_window_positive() -> None:
    """With ``policy.lookahead_window_s > 0`` (shipped default), the runner
    must pass a non-empty list to ``decide`` for events that have any
    follow-up event inside the window."""
    char = _char("protection_warrior")
    events = [_ev(0.0), _ev(1.0), _ev(2.0)]
    healing = HealingProfile(profile="t", baseline_hps_pct_of_dtps=0.0, baseline_hps_abs=0.0)
    received_upcoming: list[list[DamageEvent] | None] = []
    original_decide = ActiveMitigationPolicy.decide

    def spy(self, state, now, recent_dtps, incoming_event=None, upcoming_events=None):
        received_upcoming.append(upcoming_events)
        return original_decide(
            self,
            state,
            now,
            recent_dtps,
            incoming_event=incoming_event,
            upcoming_events=upcoming_events,
        )

    with patch.object(ActiveMitigationPolicy, "decide", spy):
        run_simulation(
            char,
            damage_profile=None,
            healing_profile=healing,
            events_override=events,
            iterations=1,
            seed=42,
            compute_metrics=False,
        )

    # First event sees the next two (both within 3s); second sees the third;
    # last has no follow-up. The slice ALWAYS excludes the current event.
    assert received_upcoming[0] is not None
    assert [e.time_s for e in received_upcoming[0]] == [1.0, 2.0]
    assert received_upcoming[1] is not None
    assert [e.time_s for e in received_upcoming[1]] == [2.0]
    assert received_upcoming[2] is not None
    assert received_upcoming[2] == []


def test_runner_passes_none_when_lookahead_window_is_zero() -> None:
    """When the YAML constant short-circuits the feature, the runner
    must NOT pass an empty-list allocation — passing ``None`` is the
    intentional 'skip allocation' signal documented on
    ``events_in_window``. (We re-load constants here to assert the
    runner respects the configured value.)"""
    char = _char("protection_warrior")
    events = [_ev(0.0), _ev(1.0)]
    healing = HealingProfile(profile="t", baseline_hps_pct_of_dtps=0.0, baseline_hps_abs=0.0)
    received_upcoming: list[list[DamageEvent] | None] = []
    original_decide = ActiveMitigationPolicy.decide

    def spy(self, state, now, recent_dtps, incoming_event=None, upcoming_events=None):
        received_upcoming.append(upcoming_events)
        return original_decide(
            self,
            state,
            now,
            recent_dtps,
            incoming_event=incoming_event,
            upcoming_events=upcoming_events,
        )

    with (
        patch("simf.core.runner.load_constants") as mock_load,
        patch.object(ActiveMitigationPolicy, "decide", spy),
    ):
        from simf.core.constants import load_constants as real_load

        c = real_load()
        c = {**c, "policy": {"lookahead_window_s": 0.0}}
        mock_load.return_value = c
        run_simulation(
            char,
            damage_profile=None,
            healing_profile=healing,
            events_override=events,
            iterations=1,
            seed=42,
            compute_metrics=False,
        )

    assert all(u is None for u in received_upcoming)


def test_constants_version_bumped_for_phase_a() -> None:
    """Adding a new top-level YAML block (``policy.lookahead_window_s``)
    bumps ``constants_version`` so any downstream cache keyed on the
    version invalidates. Pre-Phase A was 18; this PR ships 19."""
    from simf.core.constants import load_constants

    c = load_constants()
    assert c["constants_version"] >= 19
    assert c["policy"]["lookahead_window_s"] >= 0.0
