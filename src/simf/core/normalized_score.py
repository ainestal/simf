"""Normalized Tank Score + HRPS — Phase 6.2 / 6.3.

These metrics live on top of `SimResult` and aren't computed by the
engine directly. They're derived during aggregation so the `SimResult`
returned by `runner.run_simulation` carries them ready for the UI.

**HRPS** (Healing Required Per Second) — the amount of healing per
second the healer must provide to keep the tank alive *after* the
tank's own self-sustain. Formula:

    hrps = (total_damage_dealt - total_self_heal) / duration_s

Where `total_self_heal` comes from the iteration's heal_timeline.

**Normalized Tank Score** — composite of three components, each
normalized against a level-appropriate baseline:

    score = (1 - death_rate) * w_death
          + (1 - hrps_norm)  * w_hrps
          + (1 - dtps_norm)  * w_dtps

Weights default to 0.40 / 0.30 / 0.30 (death-rate dominates because
dying is the worst outcome). The "norm" terms divide the spec's value
by a baseline calibrated against Brutoh-class Warrior on the same
profile — the baseline ships in `data/normalized_score_baselines.yaml`
and tunes per-patch.

Range [0.0, 1.0]. 1.0 is a perfect tank (never dies, never asks for
healing, takes minimal DTPS). 0.0 is a tank that dies every iteration.
"""

from __future__ import annotations

# Composite weights — sum to 1.0. Death dominates because dying is the
# worst outcome a tank can have; HRPS and DTPS are tied 0.30 because
# both contribute to "easy-to-heal" without one being a strict subset
# of the other.
WEIGHT_DEATH = 0.40
WEIGHT_HRPS = 0.30
WEIGHT_DTPS = 0.30

# Baseline values used when no per-key-level normalization is provided.
# Tuned roughly against Brutoh +14 Fortified: ~80k DTPS sustained,
# ~60k HRPS after self-sustain. Per-key-level baselines override these
# (data/normalized_score_baselines.yaml, referenced in the module
# docstring, was never actually shipped — this module-level default is
# still the only baseline in effect as of 2026-07-07).
#
# STALE (found 2026-07-07, Batch C healer-surfacing review, independently
# confirmed by calibration-scientist; not fixed here — future ticket):
# real `mean_hrps` at +14 Fortified today is ~2.9k, not ~60k — a ~20x
# gap from this constant. `hrps_norm = mean_hrps / BASELINE_HRPS` in
# `compute_normalized_tank_score` below is therefore saturated near
# 0.0 (i.e. `hrps_component` near 1.0) across the whole real key range,
# which means Tank Score's 30%-weighted HRPS component contributes a
# near-constant ~0.95-ish rather than actually discriminating between
# builds — Tank Score today is effectively death-rate + DTPS + a
# near-constant, not the death/HRPS/DTPS composite the docstring
# describes. Likely compounds with `compute_hrps`'s own
# self-heal-vs-modeled-healer scope gap (see its docstring) rather than
# being an independent bug — re-deriving this baseline should happen
# alongside fixing that, not before it.
BASELINE_DTPS = 80_000.0
BASELINE_HRPS = 60_000.0


def compute_hrps(
    total_damage_dealt: float,
    total_self_heal: float,
    duration_s: float,
) -> float:
    """Healing Required Per Second after the tank's own self-sustain.

    KNOWN COPY-ACCURACY GAP (found 2026-07-07, Batch C healer-surfacing
    review, independently confirmed by calibration-scientist; not fixed
    here — future ticket): despite this function's name and every
    caller's description of it ("the healing your healer needs after
    your self-sustain"), `total_self_heal` as actually passed in from
    `runner.py` (`IterationResult.healing_total`) is NOT self-heal-only.
    It sums the entire `heal_timeline`, which ALSO includes the sim's
    own modeled healer output (the `baseline_hps` per-tick heal and the
    reactive-burst heal from the `HealingProfile` token-bucket system) —
    true since the engine's very first commit, predating this function.
    So `mean_hrps` actually measures the shortfall beyond simf's own
    already-generous modeled baseline healer, not "what a real healer
    must supply." Confirmed empirically: `mean_hrps` runs roughly 12x
    smaller than the real external-heal-received rate, and is INVERSELY
    correlated with key level in the real sweep range (the modeled
    baseline healer scales with DTPS, so this residual shrinks as keys
    get harder — the opposite of what "healing required" should do).
    Every existing caller's copy that describes HRPS this way (this
    module's own docstring above, `ui/verdict.py`'s Tank Score caption,
    `ui/log_cd_plan.py`'s HRPS column tooltip) is describing the
    INTENDED semantic, not the actual one — a description bug, not a
    numeric/calibration one (no downstream number needs to change to
    fix it; the description does). See `ui/verdict.py`'s
    `_render_healer_ask_caption` docstring for the full investigation
    and why that surface uses `mean_dtps` instead.

    Args:
        total_damage_dealt: Sum of post-mitigation damage taken across the run.
        total_self_heal: Sum of self-heals (Brutal Vitality / DS / SC / T&C).
        duration_s: Encounter duration.

    Returns:
        Net HRPS, never negative (self-sustain overshoots clamp to 0).
    """
    if duration_s <= 0:
        return 0.0
    net = max(0.0, total_damage_dealt - total_self_heal)
    return net / duration_s


def compute_normalized_tank_score(
    death_rate: float,
    mean_hrps: float,
    mean_dtps: float,
    *,
    baseline_hrps: float = BASELINE_HRPS,
    baseline_dtps: float = BASELINE_DTPS,
) -> float:
    """Composite tank score in [0.0, 1.0]. Higher is better.

    Each component is independently normalized to [0, 1] (with 1 being
    "perfect, no damage taken"), then weighted by the module-level
    constants. Components beyond their baseline (e.g. dying more than
    the baseline death rate of 0.0) clamp to 0.

    Args:
        death_rate: Fraction of iterations that died (0.0-1.0).
        mean_hrps: Mean HRPS across iterations.
        mean_dtps: Mean DTPS across iterations.
        baseline_hrps: Baseline HRPS for normalization (default ~60k).
        baseline_dtps: Baseline DTPS for normalization (default ~80k).

    Returns:
        Score in [0.0, 1.0]. 1.0 is perfect; 0.0 is hopeless.
    """
    death_component = max(0.0, 1.0 - death_rate)
    hrps_norm = mean_hrps / max(baseline_hrps, 1.0)
    hrps_component = max(0.0, 1.0 - hrps_norm)
    dtps_norm = mean_dtps / max(baseline_dtps, 1.0)
    dtps_component = max(0.0, 1.0 - dtps_norm)

    score = (
        death_component * WEIGHT_DEATH + hrps_component * WEIGHT_HRPS + dtps_component * WEIGHT_DTPS
    )
    return max(0.0, min(1.0, score))
