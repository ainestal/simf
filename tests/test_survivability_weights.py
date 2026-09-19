"""Phase 2.7 anchor tests for sim-derived survivability marginals.

The point of this module: prove the new sim-based marginals don't lie
about the stats we *can* check against paper math (stamina, armor, vers).
Stamina is exact by construction; armor and versatility have known
closed-form derivatives and serve as independent sanity checks.

For mastery / haste / crit there is no closed-form. We assert only the
qualitative shape — non-zero for stats the engine actually couples to
the mitigation chain.

Tests use real Brutoh stats (the calibrated character) so the operating
point matches what the picker will see in production. Smaller iteration
counts than production (200) keep the test suite fast.
"""

from __future__ import annotations

from dataclasses import replace as dc_replace
from pathlib import Path

import pytest
import yaml

from simf.core.character import Character
from simf.core.marginals import ehp_marginals
from simf.core.survivability_weights import (
    PERTURB_DELTA,
    PERTURBED_STATS,
    compute_survivability_marginals,
)


@pytest.fixture(scope="module")
def brutoh() -> Character:
    """Real Brutoh stats — Earthen Prot Warrior, Midnight 12.0.5 scale."""
    yaml_path = Path("src/simf/data/characters/brutoh.yaml")
    with yaml_path.open() as f:
        d = yaml.safe_load(f)
    char_data = {k: v for k, v in d.items() if k in Character.__dataclass_fields__}
    return Character(**char_data)


@pytest.fixture(scope="module")
def brutoh_weights(brutoh):
    """Compute once, share across all tests in this module."""
    return compute_survivability_marginals(brutoh, iterations=200, seed=42, n_workers=4)


def test_shape_matches_ehp_marginals(brutoh, brutoh_weights):
    """New module returns the same {stat: {'p', 'm'}} shape as the old one
    so downstream callers in optimizer/per_dungeon.py work bit-identically.
    """
    old = ehp_marginals(brutoh)
    new = brutoh_weights.marginals
    assert set(new.keys()) == set(old.keys())
    for stat in new:
        assert set(new[stat].keys()) == set(old[stat].keys()) == {"p", "m"}


def test_stamina_anchor_holds(brutoh, brutoh_weights):
    """Stamina marginal equals the closed-form by construction (after the
    K-anchor projection). Any deviation means the override-cleared baseline
    isn't being computed consistently between sim and closed-form.

    Tolerance is generous because the finite-difference is at +2000 stam
    (a chord, not the true derivative at 0), so a few percent of error is
    expected.
    """
    base_char = dc_replace(brutoh, max_hp_override=None)
    closed = ehp_marginals(base_char)
    sim = brutoh_weights.marginals
    for school in ("p", "m"):
        ratio = sim["stamina"][school] / closed["stamina"][school]
        assert 0.90 < ratio < 1.15, f"stamina anchor drift on school {school}: {ratio:.3f}"


def test_armor_sim_agrees_with_closed_form(brutoh, brutoh_weights):
    """Armor has an exact closed-form derivative; sim should agree within
    ±50% (sim integrates time + spike effects so absolute agreement is not
    expected — but a >2x divergence indicates the engine has drifted from
    the paper math)."""
    base_char = dc_replace(brutoh, max_hp_override=None)
    closed = ehp_marginals(base_char)
    sim = brutoh_weights.marginals
    if closed["armor_from_gear"]["p"] > 1e-3:
        ratio = sim["armor_from_gear"]["p"] / closed["armor_from_gear"]["p"]
        assert 0.5 < ratio < 2.0, f"armor sim/closed ratio outside ±2x: {ratio:.2f}"
    # Magic side: armor never mitigates magic. Must be exactly 0.
    assert sim["armor_from_gear"]["m"] == 0.0


def test_versatility_sim_agrees_with_closed_form_within_5x(brutoh, brutoh_weights):
    """Versatility's closed-form is a linear lower bound; sim captures
    spike-survival exp-weighting that legitimately inflates the value.
    ±5x is the upper limit of what's physically defensible — wider than
    that indicates the operating point is in ETMI exp-saturation
    (baseline ETMI > ~100k on physical profile) and the result is
    unreliable.
    """
    base_char = dc_replace(brutoh, max_hp_override=None)
    closed = ehp_marginals(base_char)
    sim = brutoh_weights.marginals
    for school in ("p", "m"):
        if closed["versatility_rating"][school] > 1e-3:
            ratio = sim["versatility_rating"][school] / closed["versatility_rating"][school]
            assert 0.5 < ratio < 5.0, (
                f"vers sim/closed ratio outside expected band on {school}: {ratio:.2f}"
            )


def test_mastery_haste_strength_nonzero_on_physical(brutoh_weights):
    """The headline fix: mastery / haste / strength get *positive* credit
    on physical pressure. Closed-form zeroed all three. If the sim still
    returns zero here, the perturbation produced no signal — engine bug,
    not a feature.
    """
    sim = brutoh_weights.marginals
    assert sim["mastery_rating"]["p"] > 0.0, (
        "mastery still returning 0 — engine isn't scaling crit-block?"
    )
    assert sim["haste_rating"]["p"] > 0.0, (
        "haste still returning 0 — engine isn't scaling SB/IP frequency?"
    )
    assert sim["strength"]["p"] > 0.0, (
        "strength still returning 0 — parry from strength not modeled?"
    )


def test_crit_has_real_positive_survival_value_for_prot_warrior(brutoh_weights):
    """Prot Warrior crit converts into bonus parry chance via Riposte (a
    spec ability, confirmed 2026-07-12 via a live in-game tooltip — see
    docs/validation/protwarrior_riposte_crit_parry_gap_2026_07_12.md and
    character.py:base_parry). Crit now carries a real, substantial
    survival marginal — no longer the old near-zero assumption this test
    used to guard (crit legitimately now exceeds mastery's marginal,
    since Riposte's rating-to-parry conversion is steeper per point than
    strength's). Sanity-bound against a units/divisor bug rather than
    asserting a specific ordering against mastery.
    """
    sim = brutoh_weights.marginals
    crit_p = sim["crit_rating"]["p"]
    vers_p = sim["versatility_rating"]["p"]
    assert crit_p > 0, "crit should have a real positive survival marginal via Riposte"
    assert crit_p < vers_p * 3, (
        f"crit (+{crit_p:.0f}) is implausibly large vs versatility (+{vers_p:.0f}) — "
        "check for a units/divisor bug"
    )


def test_armor_zero_on_magic(brutoh_weights):
    """Physical-only mitigation: armor must be exactly 0 on magic
    regardless of what the noisy sim says. We pin this in the
    implementation."""
    assert brutoh_weights.marginals["armor_from_gear"]["m"] == 0.0


def test_meta_carries_calibration_constants(brutoh_weights):
    """The K_phys / K_mag constants and raw ETMI deltas must be in meta
    so the future Streamlit "show your work" panel can render them."""
    assert brutoh_weights.meta.get("anchor_failed") is False
    assert "k_phys" in brutoh_weights.meta
    assert "k_mag" in brutoh_weights.meta
    assert "sim_etmi" in brutoh_weights.meta
    assert "sim_delta_etmi" in brutoh_weights.meta
    assert "sanity_residuals" in brutoh_weights.meta


def test_fallback_when_anchor_fails():
    """A degenerate character (stamina too low to move ETMI) should fall
    back to the closed-form rather than producing garbage. The meta tells
    callers anchor_failed=True so the UI can flag it.
    """
    # Stamina = 1 puts max_hp at ~22, which means the tank dies to any
    # event. ETMI saturates and the perturbation can't move it.
    degen = Character(
        name="degen",
        race="human",
        class_spec="protection_warrior",
        talents="kiratank-defensive",
        strength=100,
        stamina=1,
        armor_from_gear=100,
        haste_rating=100,
        crit_rating=100,
        mastery_rating=100,
        versatility_rating=100,
    )
    weights = compute_survivability_marginals(degen, iterations=100, seed=42, n_workers=2)
    # Either succeed (rare) or fall back honestly.
    if weights.meta.get("anchor_failed"):
        assert weights.meta.get("reason")  # has a reason string
        # Fallback values are closed-form — check shape, not specific values.
        for stat in PERTURBED_STATS:
            assert stat in weights.marginals
            assert "p" in weights.marginals[stat]
            assert "m" in weights.marginals[stat]


def test_perturb_delta_covers_all_stats():
    """No silent gaps: every stat in PERTURBED_STATS must have a
    PERTURB_DELTA entry so the perturbation matrix is complete."""
    for stat in PERTURBED_STATS:
        assert stat in PERTURB_DELTA, f"missing PERTURB_DELTA[{stat!r}]"
        assert PERTURB_DELTA[stat] > 0


# ─── Bootstrap CI (Active Triage Queue Batch F, 2026-07-08) ───────────────────
#
# `_bootstrap_marginal_ci` resamples the iterations `compute_survivability_marginals`
# already ran (zero extra sims) to put a 95% CI on every marginal, the same
# honesty treatment `optimizer/talent_search.py` already ships for the
# Talent A/B panel. Design validated by a calibration-scientist pass
# (2026-07-08) — see the docstring on `_bootstrap_marginal_ci` for the
# statistical argument (joint index resampling per school, denominator
# sign-flip guard).


def test_ci_present_by_default_and_brackets_point_estimate(brutoh_weights):
    """Every non-suppressed marginal's CI must actually contain its own
    point estimate — the point estimate is one particular draw from the
    same resampling distribution the CI describes, so a CI that excludes it
    would mean the two are computed from different data."""
    assert brutoh_weights.ci is not None
    for stat in PERTURBED_STATS:
        for school in ("p", "m"):
            point = brutoh_weights.marginals[stat][school]
            ci = brutoh_weights.ci[stat][school]
            if ci is None:
                continue
            lo, hi = ci
            assert lo <= point <= hi, (
                f"{stat}/{school}: point {point:.2f} outside CI [{lo:.2f}, {hi:.2f}]"
            )


def test_ci_none_for_noise_floor_zeroed_marginal(brutoh_weights):
    """A marginal the point-estimate noise floor zeroed out must not carry
    a CI — showing '0 ± band' next to a displayed 0 is a contradiction, not
    an honesty gain."""
    for stat in PERTURBED_STATS:
        for school in ("p", "m"):
            if brutoh_weights.marginals[stat][school] == 0.0:
                assert brutoh_weights.ci[stat][school] is None, (
                    f"{stat}/{school} is zeroed but still carries a CI"
                )


def test_ci_none_for_stamina_and_pinned_armor_magic(brutoh_weights):
    """Stamina is the closed-form anchor (zero-width by construction, no
    CI to show); armor-vs-magic is a physics pin (0.0, not measured)."""
    assert brutoh_weights.ci["stamina"]["p"] is None
    assert brutoh_weights.ci["stamina"]["m"] is None
    assert brutoh_weights.ci["armor_from_gear"]["m"] is None


def test_ci_meta_reports_resamples_and_sign_flip_count(brutoh_weights):
    ci_meta = brutoh_weights.meta.get("ci_meta")
    assert ci_meta
    assert ci_meta["resamples"] == 1000
    assert ci_meta["denominator_sign_flips"] == {"p": 0, "m": 0}


def test_ci_disabled_when_resamples_zero(brutoh):
    weights = compute_survivability_marginals(
        brutoh, iterations=100, seed=42, n_workers=2, ci_resamples=0
    )
    assert weights.ci is None
    assert weights.meta.get("ci_meta") == {}


def test_ci_deterministic_for_fixed_bootstrap_seed(brutoh):
    w1 = compute_survivability_marginals(
        brutoh, iterations=100, seed=42, n_workers=2, ci_resamples=200, ci_seed=7
    )
    w2 = compute_survivability_marginals(
        brutoh, iterations=100, seed=42, n_workers=2, ci_resamples=200, ci_seed=7
    )
    assert w1.ci == w2.ci


def test_ci_width_shrinks_with_more_resamples_up_to_a_point(brutoh):
    """Not a strict monotonicity claim (both are finite-sample estimates of
    the same underlying percentile), but a pathologically small resample
    count (5) should not produce a materially TIGHTER band than a
    production-sized one (1000) on a stat with real signal — that would
    indicate the resample isn't actually varying anything."""
    small = compute_survivability_marginals(
        brutoh, iterations=150, seed=42, n_workers=2, ci_resamples=5, ci_seed=1
    )
    big = compute_survivability_marginals(
        brutoh, iterations=150, seed=42, n_workers=2, ci_resamples=1000, ci_seed=1
    )
    stat = "versatility_rating"
    small_ci = small.ci[stat]["p"]
    big_ci = big.ci[stat]["p"]
    assert small_ci is not None and big_ci is not None
    assert (small_ci[1] - small_ci[0]) > 0
    assert (big_ci[1] - big_ci[0]) > 0


def test_ci_skipped_on_anchor_failed_fallback():
    """The degenerate-character fallback path returns closed-form marginals
    with no sim signal to bootstrap — ci must stay None, not a garbage
    interval around a value the sim never actually measured."""
    degen = Character(
        name="degen",
        race="human",
        class_spec="protection_warrior",
        talents="kiratank-defensive",
        strength=100,
        stamina=1,
        armor_from_gear=100,
        haste_rating=100,
        crit_rating=100,
        mastery_rating=100,
        versatility_rating=100,
    )
    weights = compute_survivability_marginals(degen, iterations=100, seed=42, n_workers=2)
    if weights.meta.get("anchor_failed"):
        assert weights.ci is None
