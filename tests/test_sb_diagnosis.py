"""Tests for Phase 3.4: Shield Block gap diagnosis (rage-starved vs charge-limited)."""

from simf.core.character import Character
from simf.core.constants import load_constants
from simf.core.mitigation import MitigationState
from simf.core.policy import ActiveMitigationPolicy
from simf.core.profiles import HealingProfile, load_damage_profile
from simf.core.runner import run_simulation


def _prot_warrior(**overrides) -> Character:
    defaults = dict(
        name="Test",
        race="human",
        class_spec="protection_warrior",
        talents="kiratank-defensive",
        stamina=80000,
        strength=5000,
        armor_from_gear=30000,
        haste_rating=1000,
        crit_rating=500,
        mastery_rating=1000,
        versatility_rating=800,
    )
    defaults.update(overrides)
    return Character(**defaults)


def _profile():
    return load_damage_profile("m+_pull_melee")


def test_sb_diagnosis_fields_present():
    char = _prot_warrior()
    heal = HealingProfile(profile="test", baseline_hps_pct_of_dtps=1.5)
    result = run_simulation(char, _profile(), heal, iterations=10, seed=1)
    assert hasattr(result, "mean_sb_rage_starved_pct")
    assert hasattr(result, "mean_sb_charge_limited_pct")
    assert result.mean_sb_rage_starved_pct >= 0.0
    assert result.mean_sb_charge_limited_pct >= 0.0


def test_sb_diagnosis_fractions_bounded():
    char = _prot_warrior()
    heal = HealingProfile(profile="test", baseline_hps_pct_of_dtps=1.5)
    result = run_simulation(char, _profile(), heal, iterations=20, seed=1)
    assert 0.0 <= result.mean_sb_rage_starved_pct <= 1.0
    assert 0.0 <= result.mean_sb_charge_limited_pct <= 1.0
    total_gap = 1.0 - result.mean_sb_uptime
    assert result.mean_sb_rage_starved_pct + result.mean_sb_charge_limited_pct <= total_gap + 0.01


def test_sb_diagnosis_zero_for_non_warrior():
    """Non-warrior specs should have 0 gap diagnosis (no SB)."""
    char = _prot_warrior(class_spec="brewmaster_monk")
    heal = HealingProfile(profile="test", baseline_hps_pct_of_dtps=1.5)
    result = run_simulation(char, _profile(), heal, iterations=10, seed=1)
    assert result.mean_sb_rage_starved_pct == 0.0
    assert result.mean_sb_charge_limited_pct == 0.0


def test_sb_uptime_plus_gaps_approximately_cover_duration():
    """uptime + rage_starved + charge_limited should cover most of the fight duration."""
    char = _prot_warrior()
    heal = HealingProfile(profile="test", baseline_hps_pct_of_dtps=1.5)
    result = run_simulation(char, _profile(), heal, iterations=30, seed=2)
    total_accounted = (
        result.mean_sb_uptime + result.mean_sb_rage_starved_pct + result.mean_sb_charge_limited_pct
    )
    # May not be exactly 1.0 — time before/after the last event is untracked
    assert 0.7 <= total_accounted <= 1.05


# ─── Hasted Shield Block recharge — 2026-07-21 fix ─────────────────────────
#
# docs/validation/protwarrior_post_shield_block_bias_decomposition_2026_07_21.md:
# `core/policy.py` was recharging Shield Block charges at a flat, un-hasted
# 16s regardless of the character's haste. SimC source confirms the real
# ability's cooldown is hasted (`shield_block_t`'s constructor sets
# `cooldown->hasted = true;`). These tests pin the exact scheduled-recharge
# formula directly at the policy layer — the smallest possible regression
# test for the changed line, isolated from the runner's rage-income channel
# (which is ALSO haste-scaled and would otherwise confound an end-to-end
# uptime comparison).


def test_shield_block_recharge_is_hasted_at_policy_level():
    """A press schedules the just-used charge's next-ready time at
    `now + recharge_s / (1 + haste_pct)`, not a flat `recharge_s`. Ample
    rage is provided so the rage-income channel (also haste-scaled) can't
    confound this — the assertion is purely about the recharge timer."""
    c = load_constants()
    recharge_s = c["active_mitigation"]["shield_block"]["recharge_s"]
    hasted_char = _prot_warrior(haste_rating=1020)  # Brutoh calibration value, ~23.18% haste
    state = MitigationState(hasted_char)
    state.rage = 100.0
    policy = ActiveMitigationPolicy(hasted_char, skill_modifier=1.0, skill_rng=None)
    now = 5.0
    policy.decide(state, now, recent_dtps=0.0, incoming_event=None)
    haste_pct = hasted_char.haste_pct()
    assert haste_pct > 0.0  # sanity: this fixture really does carry haste
    expected = now + recharge_s / (1 + haste_pct)
    # Both charges start at 0.0 (ready); exactly one gets consumed by this
    # press and rescheduled — find it.
    scheduled = [t for t in state.sb_charges_ready_at if t > now]
    assert len(scheduled) == 1
    assert scheduled[0] == expected


def test_shield_block_recharge_faster_with_more_haste():
    """End-to-end confirmation (not just the unit formula above): a
    higher-haste character reaches a HIGHER steady-state Shield Block
    uptime than a zero-haste one, holding the damage profile, skill
    modifier, and RNG seed fixed. This is the exact mechanism the
    validation doc's SimResult.mean_sb_uptime comparison (59.1% modeled
    vs 90.1% real, pre-fix) measured on the real ratified corpus."""
    zero_haste = _prot_warrior(haste_rating=0)
    high_haste = _prot_warrior(haste_rating=1020)
    heal = HealingProfile(profile="test", baseline_hps_pct_of_dtps=1.5)
    low = run_simulation(zero_haste, _profile(), heal, iterations=50, seed=7)
    high = run_simulation(high_haste, _profile(), heal, iterations=50, seed=7)
    assert high.mean_sb_uptime > low.mean_sb_uptime


# ─── Shield Block press CADENCE investigation (kept as-is) — 2026-07-21 ────
#
# docs/validation/protwarrior_sb_press_cadence_2026_07_21.md: investigated
# whether moving `core/policy.py`'s `-1.5s` reactive gate toward SimC's real
# APL condition (`engine/class_modules/apl/apl_warrior.cpp:372`,
# `shield_block,if=buff.shield_block.remains<=10` — a near-no-op for a 6s
# buff, i.e. "press whenever a charge is ready") would close more of the
# remaining calibration bias. It does NOT: a buffer sweep (dropping the gate
# entirely, and widening it to 3.0s) both ROBUSTLY REGRESSED
# `SimResult.mean_sb_uptime` and the ratified-corpus RMSE, because SimC's
# real mechanic banks remaining duration ADDITIVELY
# (`buff_t::extend_duration_or_trigger`) while this engine's
# `shield_block_until = now + duration_s` is an ABSOLUTE RESET — pressing
# "early" under reset semantics wastes a charge for near-zero benefit and
# pulls the charge-recharge clock forward, producing a BIGGER gap later.
# These tests pin that finding as a regression guard: the *un-widened*
# `-1.5s` gate must keep outperforming a wider/absent one on this engine, so
# a future "fix" can't silently reintroduce the naive SimC-literal mirror
# this investigation already falsified.


def test_shield_block_reactive_gate_withholds_press_far_from_expiry():
    """The CURRENT `-1.5s` gate withholds a press when the buff still has
    plenty of remaining time, even with a second charge sitting idle and
    ready. This is intentional (see the policy.py comment right above
    `sb_preconditions`) — do not remove this gate to chase SimC's literal
    `remains<=10` condition; that was tried and it regresses calibration
    (see the module docstring above)."""
    char = _prot_warrior(haste_rating=0)
    state = MitigationState(char)
    state.rage = 100.0
    state.shield_block_until = 6.0  # buff already active, 6s remaining at t=0
    state.sb_charges_ready_at = [0.0, 0.0]  # both charges ready the whole time
    policy = ActiveMitigationPolicy(char, skill_modifier=1.0, skill_rng=None)

    now = 4.0  # remains=2.0s, outside the -1.5s window
    policy.decide(state, now, recent_dtps=0.0, incoming_event=None)

    assert state.shield_block_until == 6.0  # unchanged — no press
    assert state.rage == 100.0  # unchanged — no press


def test_shield_block_reactive_gate_presses_once_within_window():
    """Sanity check on the same fixture: once `now` enters the `-1.5s`
    window, the press does fire, using the idle second charge."""
    char = _prot_warrior(haste_rating=0)
    state = MitigationState(char)
    state.rage = 100.0
    state.shield_block_until = 6.0
    state.sb_charges_ready_at = [0.0, 0.0]
    policy = ActiveMitigationPolicy(char, skill_modifier=1.0, skill_rng=None)

    now = 4.6  # remains=1.4s, inside the -1.5s window
    policy.decide(state, now, recent_dtps=0.0, incoming_event=None)

    assert state.shield_block_until == now + 6  # duration_s, freshly reset
    assert state.rage == 70.0  # rage_cost=30 spent


def test_shield_block_reactive_gate_beats_a_wider_one_on_uptime():
    """Regression guard for the sweep itself: at a fixed haste/profile/seed,
    the shipped `-1.5s` gate must reach a HIGHER `mean_sb_uptime` than a
    policy that presses on every opportunity (the literal SimC-mirror this
    investigation rejected). Constructs the "always press ASAP" variant
    inline via a subclass so this test doesn't depend on any removed code
    still existing to import."""

    class _PressASAPPolicy(ActiveMitigationPolicy):
        """Mirrors SimC's real remains<=10 condition literally: press
        whenever a charge + rage are available, no expiry-proximity gate."""

        def decide(self, state, now, recent_dtps, incoming_event=None, upcoming_events=None):
            c = load_constants()
            sb = c["active_mitigation"]["shield_block"]
            hasted_recharge_s = sb["recharge_s"] / (1 + state.cached_haste_pct)
            if state.shield_block_charges_available(now) > 0 and state.rage >= sb["rage_cost"]:
                for i, t in enumerate(state.sb_charges_ready_at):
                    if t <= now:
                        state.sb_charges_ready_at[i] = now + hasted_recharge_s
                        break
                state.shield_block_until = now + sb["duration_s"]
                state.rage -= sb["rage_cost"]

    char = _prot_warrior(haste_rating=1020)  # Brutoh calibration value, ~23.18% haste
    heal = HealingProfile(profile="test", baseline_hps_pct_of_dtps=1.5)

    shipped = run_simulation(char, _profile(), heal, iterations=100, seed=11)

    import simf.core.runner as runner_mod

    orig_make_policy = runner_mod.make_policy
    try:
        runner_mod.make_policy = lambda character, **kw: _PressASAPPolicy(character, **kw)
        press_asap = run_simulation(char, _profile(), heal, iterations=100, seed=11)
    finally:
        runner_mod.make_policy = orig_make_policy

    assert shipped.mean_sb_uptime > press_asap.mean_sb_uptime
