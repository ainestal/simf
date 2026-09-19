"""Phase B — adaptive-lookahead consumer for Demo Shout pre-cast.

Phase A (2026-05-23) plumbed ``upcoming_events`` into every tank
policy's ``decide()`` without consuming the slice anywhere. Phase B
adds the first consumer: when the sum of physical damage scheduled
inside ``upcoming_window_s`` exceeds ``upcoming_physical_threshold``,
``ActiveMitigationPolicy`` pre-casts Demoralizing Shout instead of
waiting for the existing past-burst gate (``recent_dtps > 250_000``)
to trip. This encodes the canonical Prot Warrior coaching tip —
"pre-cast Demo Shout one GCD before a tank-buster" — that already
shows up as a Focus-tier callout in ``skill_tiers``.

The consumer is toggle-gated, default-OFF. With the toggle OFF,
every pinned seed stays bit-identical. With it ON, synthetic-sim
behaviour shifts but replay mode is unaffected (replay mode fires
DS unconditionally via the existing ``is_replay_event`` clause).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from simf.core.character import Character
from simf.core.constants import load_constants
from simf.core.events import DamageEvent
from simf.core.mitigation import MitigationState
from simf.core.policy import ActiveMitigationPolicy


def _prot_warrior() -> Character:
    return Character(
        name="t",
        race="orc",
        class_spec="protection_warrior",
        talents="brutoh-actual",
        strength=2000,
        stamina=50000,
        armor_from_gear=5000,
    )


def _phys(time_s: float, raw: float = 100_000.0) -> DamageEvent:
    return DamageEvent(
        time_s=time_s,
        source_id="t",
        school="physical",
        raw_amount=raw,
        attack_type="melee",
        is_avoidable=False,
        is_blockable=False,
    )


def _magic(time_s: float, raw: float = 100_000.0) -> DamageEvent:
    return DamageEvent(
        time_s=time_s,
        source_id="t",
        school="magic",
        raw_amount=raw,
        attack_type="spell",
        is_avoidable=False,
        is_blockable=False,
    )


def _fresh_policy() -> tuple[ActiveMitigationPolicy, MitigationState]:
    char = _prot_warrior()
    policy = ActiveMitigationPolicy(char)
    state = MitigationState(char)
    state.talents = set()
    # Give the policy enough rage so the SB branch can't accidentally
    # consume the DS branch's bookkeeping.
    state.rage = 100.0
    return policy, state


# ─── Default-OFF preserves bit-identity for the lookahead trigger ────────────


def test_demo_shout_lookahead_off_short_circuits_without_upcoming_events() -> None:
    """Shipped default ``enabled: false`` — the helper must return False
    regardless of how juicy the lookahead slice looks."""
    policy, _ = _fresh_policy()
    c = load_constants()
    assert c["policy"]["lookahead_consumers"]["demo_shout_precast"]["enabled"] is False
    upcoming = [_phys(2.0, raw=10_000_000)] * 5  # huge spike — would trip if enabled
    assert policy._lookahead_demo_shout_precast(c, now=1.0, upcoming_events=upcoming) is False


def test_demo_shout_lookahead_helper_short_circuits_on_empty_slice() -> None:
    """``None`` or empty list → False, regardless of toggle state."""
    policy, _ = _fresh_policy()
    c = load_constants()
    on_cfg = {
        **c,
        "policy": {
            **c["policy"],
            "lookahead_consumers": {
                "demo_shout_precast": {
                    "enabled": True,
                    "upcoming_window_s": 1.5,
                    "upcoming_physical_threshold": 1.0,
                }
            },
        },
    }
    assert policy._lookahead_demo_shout_precast(on_cfg, now=1.0, upcoming_events=None) is False
    assert policy._lookahead_demo_shout_precast(on_cfg, now=1.0, upcoming_events=[]) is False


# ─── Toggle-ON fires on imminent physical spike ──────────────────────────────


def _toggled_on_constants(threshold: float = 375_000.0, window_s: float = 1.5) -> dict:
    c = load_constants()
    return {
        **c,
        "policy": {
            **c["policy"],
            "lookahead_consumers": {
                "demo_shout_precast": {
                    "enabled": True,
                    "upcoming_window_s": window_s,
                    "upcoming_physical_threshold": threshold,
                }
            },
        },
    }


def test_demo_shout_lookahead_fires_when_upcoming_phys_exceeds_threshold() -> None:
    """Toggle ON + 500_000 physical damage in the next 1.5s window
    (above the 375_000 default threshold) → helper returns True."""
    policy, _ = _fresh_policy()
    on_cfg = _toggled_on_constants(threshold=375_000.0, window_s=1.5)
    upcoming = [_phys(1.2, raw=500_000)]
    assert policy._lookahead_demo_shout_precast(on_cfg, now=1.0, upcoming_events=upcoming) is True


def test_demo_shout_lookahead_respects_threshold() -> None:
    """Below-threshold physical damage doesn't trip the consumer.

    Without this guard the toggle would fire on every physical event,
    not just incoming spikes — defeating the whole point of the
    "pre-cast on TANK-BUSTER" framing."""
    policy, _ = _fresh_policy()
    on_cfg = _toggled_on_constants(threshold=375_000.0)
    upcoming = [_phys(1.2, raw=100_000)]  # well below 375k threshold
    assert policy._lookahead_demo_shout_precast(on_cfg, now=1.0, upcoming_events=upcoming) is False


def test_demo_shout_lookahead_ignores_magic_damage() -> None:
    """Demo Shout is a physical-mitigation DR (the buff reduces damage
    the target deals; only the physical-event ledger is relevant for
    pre-casting). A spell tank-buster doesn't trigger Demo Shout."""
    policy, _ = _fresh_policy()
    on_cfg = _toggled_on_constants(threshold=375_000.0)
    upcoming = [_magic(1.2, raw=10_000_000)]  # huge magic — still ignored
    assert policy._lookahead_demo_shout_precast(on_cfg, now=1.0, upcoming_events=upcoming) is False


def test_demo_shout_lookahead_window_cuts_off_distant_events() -> None:
    """Events past ``now + upcoming_window_s`` must NOT count toward
    the threshold. The whole point of "one GCD ahead" is a narrow
    horizon; events 5s away aren't 'imminent'."""
    policy, _ = _fresh_policy()
    on_cfg = _toggled_on_constants(threshold=375_000.0, window_s=1.5)
    upcoming = [_phys(3.0, raw=10_000_000)]  # 2s out from now=1.0 — past 1.5s horizon
    assert policy._lookahead_demo_shout_precast(on_cfg, now=1.0, upcoming_events=upcoming) is False


def test_demo_shout_lookahead_sums_multiple_in_window() -> None:
    """Multiple smaller physical events that individually undershoot
    the threshold but together exceed it should still trip the
    consumer — a sustained burst is just as legitimate a pre-cast
    trigger as a single big TB."""
    policy, _ = _fresh_policy()
    on_cfg = _toggled_on_constants(threshold=375_000.0, window_s=1.5)
    upcoming = [
        _phys(1.2, raw=150_000),
        _phys(1.4, raw=150_000),
        _phys(1.5, raw=150_000),  # 450_000 total — above threshold
    ]
    assert policy._lookahead_demo_shout_precast(on_cfg, now=1.0, upcoming_events=upcoming) is True


# ─── End-to-end: decide() OR-s the lookahead trigger into ds_preconditions ───


def test_decide_fires_demo_shout_via_lookahead_when_recent_dtps_is_zero() -> None:
    """The killer integration: pre-this-feature, ``recent_dtps == 0``
    + DS off CD would NOT fire DS in synthetic mode. With the toggle
    on and a juicy lookahead slice, ``decide()`` now fires DS.
    """
    policy, state = _fresh_policy()
    on_cfg = _toggled_on_constants(threshold=375_000.0)
    upcoming = [_phys(1.2, raw=500_000)]

    # Pre-cast trigger needs DS to be off cooldown.
    state.demo_shout_cd_until = 0.0
    state.demo_shout_until = 0.0

    with patch("simf.core.policy.load_constants", return_value=on_cfg):
        policy.decide(
            state,
            now=1.0,
            recent_dtps=0.0,  # CRITICAL: no past burst — must fire from lookahead alone
            incoming_event=_phys(1.0, raw=1.0),
            upcoming_events=upcoming,
        )

    assert state.demo_shout_until > 1.0, (
        "Demo Shout did not fire even though upcoming physical damage > threshold"
    )


def test_decide_does_not_fire_demo_shout_when_toggle_is_off_and_only_lookahead_signal_exists() -> (
    None
):
    """Default toggle OFF + ``recent_dtps == 0`` + non-replay event +
    juicy lookahead slice → DS does NOT fire. This is the bit-identity
    invariant that holds for every pinned seed in the existing suite."""
    policy, state = _fresh_policy()
    state.demo_shout_cd_until = 0.0
    state.demo_shout_until = 0.0
    upcoming = [_phys(1.2, raw=10_000_000)]

    # Default constants (toggle OFF).
    policy.decide(
        state,
        now=1.0,
        recent_dtps=0.0,
        incoming_event=_phys(1.0, raw=1.0),
        upcoming_events=upcoming,
    )
    assert state.demo_shout_until == 0.0, (
        "Demo Shout fired with toggle OFF — bit-identity invariant broken"
    )


def test_decide_still_fires_on_recent_dtps_when_lookahead_is_quiet() -> None:
    """The new consumer ADDS firing occasions; it does NOT replace the
    existing ``recent_dtps > 250_000`` gate. Confirm the old trigger
    still works when no lookahead spike is visible."""
    policy, state = _fresh_policy()
    state.demo_shout_cd_until = 0.0
    state.demo_shout_until = 0.0

    policy.decide(
        state,
        now=1.0,
        recent_dtps=500_000.0,  # past burst — well above 250_000 gate
        incoming_event=_phys(1.0, raw=1.0),
        upcoming_events=[],  # no lookahead signal
    )
    assert state.demo_shout_until > 1.0, (
        "Demo Shout failed to fire on past-burst trigger after Phase B refactor"
    )


# ─── YAML schema lock-down ───────────────────────────────────────────────────


def test_constants_yaml_ships_demo_shout_precast_block_default_off() -> None:
    """The YAML default must be ``enabled: false`` so every existing
    install upgrades to Phase B without behaviour change. A YAML edit
    that flips ``enabled: true`` will fail this assertion loudly and
    force a calibration sweep before merging."""
    c = load_constants()
    cfg = c["policy"]["lookahead_consumers"]["demo_shout_precast"]
    assert cfg["enabled"] is False
    assert cfg["upcoming_window_s"] == pytest.approx(1.5)
    assert cfg["upcoming_physical_threshold"] == pytest.approx(375_000.0)


def test_constants_version_bumped_to_20() -> None:
    """Adding a new YAML block bumps constants_version so any cache
    keyed on it invalidates. Phase A was 19; Phase B is 20."""
    c = load_constants()
    assert c["constants_version"] >= 20
