"""Tests for core.constants — the tiered calibration helper (Top-5 #4,
2026-07-06 retrospective).
"""

from __future__ import annotations

from simf.core.constants import CALIBRATION_TIERS, load_constants, spec_is_calibrated


def test_calibration_tiers_ordered_worst_to_best():
    assert CALIBRATION_TIERS == ("placeholder", "characterized", "calibrated")


def test_spec_is_calibrated_true_only_at_top_tier():
    assert spec_is_calibrated({"calibration_tier": "calibrated"}) is True
    assert spec_is_calibrated({"calibration_tier": "characterized"}) is False
    assert spec_is_calibrated({"calibration_tier": "placeholder"}) is False


def test_spec_is_calibrated_defaults_to_placeholder_when_unset():
    """A spec block with no `calibration_tier` key at all must read as
    NOT calibrated — never silently trust an unmarked spec."""
    assert spec_is_calibrated({}) is False


def test_every_real_spec_has_a_valid_tier():
    """Every spec in constants.yaml must declare a real tier — catches a
    typo'd or missing `calibration_tier` before it silently defaults to
    placeholder (which would UNDER-claim) or, worse, a typo like
    "calibrated " (trailing space) that would silently under-claim too."""
    specs = load_constants()["specs"]
    for name, cfg in specs.items():
        tier = cfg.get("calibration_tier")
        assert tier in CALIBRATION_TIERS, f"{name} has invalid calibration_tier={tier!r}"


def test_no_spec_is_calibrated_today():
    """Pins the current real state so a future edit that silently flips a
    spec's tier gets caught here, not just in the UI.

    Guardian Druid was downgraded calibrated → characterized 2026-07-17
    (human-ratified): its own LOO-CV cross-validation gate measurably fails
    (9/13 = 69% held-out, need ≥75%), not attributable to known outlier runs
    — see docs/validation/phase4_guardian_loo_cv_f_consistent_2026_07_08.md.

    Prot Warrior's tier history: downgraded calibrated → characterized
    2026-07-18 (a Demo Shout + Phalanx log-replay double-count, fixed PR
    #387, had been silently canceling a separate, already-known mob-side
    bias, so the old RMSE 0.068 was unknowingly two errors netting to
    ~zero) → widened further 2026-07-21 by two real, now-fixed Shield Block
    bugs (RMSE 0.162, +13.7% bias) → closed by Vanguard's strength→armor
    spec passive the same day (RMSE 0.073, +1.8% bias, 16/16 within ±15%)
    → RE-PROMOTED to `calibrated` 2026-07-22 (human-ratified) once the
    LOO-CV gate — the 4th and last promotion criterion, newly wired into
    `simf calibrate-k` itself (PR #416) — passed against this same corpus
    (K stability + 16/16 held-out folds within ±15%, the tightest result
    this project has measured for any spec) — → **DOWNGRADED again
    2026-07-25** when the SAME promotion bar, applied for the first time
    ever to 15 independent Protection Warrior players (not Brutoh), failed
    it: mean signed bias +11.0% (bar ≤5%), 67% within ±15% (bar ≥75%). Two
    rival explanations (WCL-hydrate data-quality artifact; a key-level-
    driven gap) were checked and ruled out — see
    docs/validation/protwarrior_calibrated_downgrade_2026_07_25.md. See also
    docs/validation/protwarrior_shield_block_fix_2026_07_21.md,
    protwarrior_vanguard_strength_armor_2026_07_21.md, and
    protwarrior_loo_cv_vanguard_2026_07_22.md for the earlier history.

    Both specs' downgrades reflect this project's own cross-validation/
    generalization standards not being cleared, not a model regression —
    `global_rmse` and every mitigation constant are unchanged by either
    downgrade."""
    specs = load_constants()["specs"]
    calibrated = {name for name, cfg in specs.items() if spec_is_calibrated(cfg)}
    assert calibrated == set()
