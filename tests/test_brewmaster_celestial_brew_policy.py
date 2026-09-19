"""BrewmasterPolicy.decide()'s Celestial Brew press schedule.

The calibration corpus can't see this: apply_brewmaster_mitigation's replay
branch (classes/brewmaster_monk.py:123-128) substitutes the log's own real
absorbed amount and never reads state.healer_absorb at all — that only
happens in the synthetic branch (:129-156, read at :150). Measured effect on
a geared tank's forward-sim verdict sweep is ALSO exactly zero (the
`hp_pct < 0.70` press gate rarely fires for a geared character regardless of
cooldown) — see
docs/validation/phase4_brewmaster_celestial_brew_cooldown_fix_2026_08_07.md
for the full before/after. This fix is a game-accuracy correction, not a
mitigation improvement; unit-tested directly here since the press schedule
itself is otherwise invisible to every log-based or verdict-sweep check.
"""

from __future__ import annotations

from itertools import pairwise

from simf.classes.brewmaster_monk import BrewmasterPolicy
from simf.core.character import Character
from simf.core.constants import load_constants
from simf.core.mitigation import MitigationState


def _brewmaster_state() -> MitigationState:
    char = Character(
        name="BM",
        race="pandaren",
        class_spec="brewmaster_monk",
        talents="",
        stamina=30_000,
        armor_from_gear=2_000,
        agility=5_000,
        max_hp_override=1_000_000,
    )
    state = MitigationState(char)
    state.hp = 500_000  # 50% — below the 70% press threshold throughout
    return state


def test_celestial_brew_presses_on_the_configured_cooldown():
    """Regression test for the 2026-08-07 fix (60.0 -> 45.0) and the 12.1.0
    live patch-note correction (2026-08-13): presses land exactly
    celestial_brew_cooldown_s apart, not the old (wrong) value. 45.0 was
    itself later superseded by 12.1.0's "cooldown increased by 100%" (45.0 x2
    = 90.0, confirmed verbatim on Blizzard's own patch notes page), so the
    pin below now targets the current live value, not the 2026-08-07 one."""
    c_const = load_constants()
    cooldown = c_const["specs"]["brewmaster_monk"]["celestial_brew_cooldown_s"]
    assert cooldown == 90.0  # pins the fix itself, not just the schedule shape

    state = _brewmaster_state()
    policy = BrewmasterPolicy(state.character)
    presses = []
    t = 0.0
    while t < 200.0:
        before = policy.last_celestial_t
        policy.decide(state, t, recent_dtps=0.0)
        if policy.last_celestial_t != before:
            presses.append(t)
        t += 0.5

    assert len(presses) >= 3
    gaps = [b - a for a, b in pairwise(presses)]
    assert all(abs(g - cooldown) < 0.5 for g in gaps)


def test_celestial_brew_absorb_matches_configured_pct_of_max_hp():
    c_const = load_constants()
    absorb_pct = c_const["specs"]["brewmaster_monk"]["celestial_brew_absorb_pct_of_max_hp"]

    state = _brewmaster_state()
    policy = BrewmasterPolicy(state.character)
    policy.decide(state, 0.0, recent_dtps=0.0)

    assert state.healer_absorb == state.max_hp * absorb_pct
