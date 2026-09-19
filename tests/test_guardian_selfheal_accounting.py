"""Guardian self-heal heal_timeline accounting (2026-06-28, Guardian finish).

Guardian Tooth & Claw + Frenzied Regeneration heal the tank from its own kit
inside ``GuardianPolicy.tick()/decide()``. Before this change they mutated
``state.hp`` directly but were never recorded to the runner's ``heal_timeline``,
so HRPS / ETMI / Normalized Tank Score under-credited Guardian self-sustain.
``MitigationState.apply_self_heal`` now records the effective (post-clamp)
amount, and the runner drains it into ``heal_timeline``.

The change is Guardian-only (both self-heals live in ``GuardianPolicy``) and
purely additive accounting — it does NOT touch the death calc (the hp mutation
is unchanged) and the warrior path stays bit-identical. The source-grep
tripwire below pins that ``apply_self_heal`` is only wired on the Guardian path.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from simf.core.mitigation import MitigationState
from simf.core.profiles import HealingProfile
from simf.core.runner import run_simulation
from tests.test_guardian_natures_guardian_heal import _guardian, _magic_events

# ─── unit: the clamp + record contract ───────────────────────────────────────


def test_apply_self_heal_clamps_records_and_noops():
    ch = _guardian(stamina=55000)
    st = MitigationState(ch)
    st.hp = st.max_hp * 0.5
    missing = st.max_hp - st.hp

    # heal smaller than missing HP → fully credited
    eff = st.apply_self_heal(1.0, missing * 0.5)
    assert eff == pytest.approx(missing * 0.5)
    assert st.self_heal_events == [(1.0, pytest.approx(missing * 0.5))]
    assert st.hp == pytest.approx(st.max_hp * 0.75)

    # heal larger than missing HP → clamped to the remaining missing HP
    remaining = st.max_hp - st.hp
    eff2 = st.apply_self_heal(2.0, st.max_hp * 10)
    assert eff2 == pytest.approx(remaining)
    assert st.hp == pytest.approx(st.max_hp)
    assert st.self_heal_events[-1] == (2.0, pytest.approx(remaining))

    # at full HP → no-op, nothing recorded
    n = len(st.self_heal_events)
    assert st.apply_self_heal(3.0, 1000.0) == 0.0
    assert len(st.self_heal_events) == n

    # non-positive heal → no-op
    st.hp = st.max_hp * 0.5
    assert st.apply_self_heal(4.0, 0.0) == 0.0
    assert st.apply_self_heal(4.0, -50.0) == 0.0
    assert len(st.self_heal_events) == n


# ─── sim: self-heals reach heal_timeline ──────────────────────────────────────


def test_guardian_self_heals_credited_without_external_healing():
    """No healer at all (baseline 0, no externals, reactive off) → the ONLY
    healing source is the Guardian's own kit. healing_total > 0 proves Tooth &
    Claw + Frenzied Regen are recorded to heal_timeline. Before this change it
    was 0 in this scenario."""
    events = _magic_events(40, 50000.0)  # heavy sustained magic → tank drops low → FrR fires
    res = run_simulation(
        _guardian(stamina=55000),
        damage_profile=None,
        healing_profile=HealingProfile(profile="t", baseline_hps_pct_of_dtps=0.0),
        events_override=events,
        duration_override=41.0,
        iterations=2,
        min_iterations=1,
        seed=42,
        keep_iteration_results=True,
    )
    it = res.iteration_results[0]
    assert it.healing_total > 0.0
    # heal_timeline carries effective (post-clamp) amounts → all non-negative
    assert all(h >= 0 for _, h in it.heal_timeline)
    # healing_total is exactly the sum of the timeline it derives from
    assert it.healing_total == pytest.approx(sum(h for _, h in it.heal_timeline))


def test_self_heal_accounting_lowers_guardian_hrps():
    """Crediting self-sustain must REDUCE the healing the external healer has to
    provide. HRPS = (dealt - total_credited_healing) / duration (the credited
    healing now includes Guardian self-heals), so HRPS sits below raw DTPS for a
    self-sustaining tank. (Note: `total_credited_healing` is all modeled healing,
    not self-heal alone — see the compute_hrps docstring follow-up.)"""
    events = _magic_events(40, 50000.0)
    res = run_simulation(
        _guardian(stamina=55000),
        damage_profile=None,
        healing_profile=HealingProfile(profile="t", baseline_hps_pct_of_dtps=0.0),
        events_override=events,
        duration_override=41.0,
        iterations=20,
        min_iterations=5,
        seed=7,
    )
    # self-sustain is real → HRPS strictly below raw DTPS (dealt/duration).
    assert res.mean_hrps < res.mean_dtps


# ─── tripwire: Guardian-only, warrior path untouched ──────────────────────────


def test_apply_self_heal_only_wired_on_guardian_path():
    """``apply_self_heal`` must only be CALLED from the Guardian policy. If a
    future change wires it into another spec's policy (e.g. warrior Tooth &
    Claw), that spec's HRPS / Normalized Score shifts — a calibration-affecting
    change that must be a deliberate, separately-reviewed decision, not an
    accidental import. The definition lives in mitigation.py; calls must not."""
    src = Path(__file__).resolve().parent.parent / "src" / "simf"
    callers = []
    for py in src.rglob("*.py"):
        for i, line in enumerate(py.read_text().splitlines(), 1):
            # a call site, not the `def apply_self_heal` definition
            if re.search(r"\.apply_self_heal\s*\(", line):
                callers.append(f"{py.relative_to(src)}:{i}")
    assert callers, "expected at least the Guardian call sites"
    assert all(c.startswith("classes/guardian_druid.py") for c in callers), (
        f"apply_self_heal called outside the Guardian path: {callers}"
    )
