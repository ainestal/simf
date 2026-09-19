"""The spec trust caption: uncalibrated warning + the decoupled per-spec caveat.

`_uncalibrated_spec_warning` shows a tier-specific "not calibrated yet" line
for any spec below the `calibrated` tier (Top-5 #4, 2026-07-06 — replaces the
old `calibrated: true/false` boolean with `calibration_tier`:
placeholder/characterized/calibrated). The per-spec MODELING CAVEAT (e.g.
Guardian's single-build haste slope + magic residual) is DECOUPLED from the
tier (2026-06-29): it surfaces regardless of tier, so neither "calibrated"
nor "characterized" implies every lever is equally tight.
"""

from __future__ import annotations

# `_uncalibrated_spec_warning` and the `_ss` it resolves both live in
# `simf.ui.state` after the foundation split (PR 1). Alias as `app` so the
# monkeypatch targets the module where the function actually reads `_ss`.
from simf.core.constants import load_constants
from simf.ui import state as app


def _patch_spec(monkeypatch, spec: str) -> None:
    monkeypatch.setattr(app, "_ss", lambda: {"char_data": {"class_spec": spec}})


def test_characterized_guardian_shows_characterized_warning_with_its_own_caveat(monkeypatch):
    # Guardian was downgraded calibrated → characterized 2026-07-17 (human-
    # ratified): its own LOO-CV gate measurably fails (9/13 = 69% held-out,
    # need ≥75%), not attributable to known outlier runs — see
    # docs/validation/phase4_guardian_loo_cv_f_consistent_2026_07_08.md. It
    # still gets the "characterized" copy (not the more alarming "isn't
    # calibrated yet" placeholder wording) PLUS its own per-spec modeling
    # caveat, decoupled from the tier same as before.
    _patch_spec(monkeypatch, "guardian_druid")
    msg = app._uncalibrated_spec_warning()
    assert "characterized" in msg
    assert "isn't calibrated" not in msg
    assert "is calibrated" not in msg  # no longer affirms calibrated state
    # caveat levers still surfaced (decoupled from the tier):
    assert "haste→Ironfur" in msg
    assert "Elune's-Chosen" in msg
    assert "magic residual" in msg
    # Names the MECHANISM (magic-heavy damage), not a specific Season-1
    # dungeon — a dungeon example goes stale the moment the M+ pool rotates
    # (Season 2 swaps all 8 dungeons), a mechanism description doesn't.
    assert "Nexus-Point Xenas" not in msg
    assert "magic rather than physical" in msg


def test_characterized_spec_shows_characterized_warning_without_guardian_text(monkeypatch):
    # Brewmaster is `characterized` (Top-5 #4, 2026-07-06) — real logs
    # replayed and written up, but short of the calibrated parity bar. Gets
    # the less-alarming "characterized, not calibrated" copy, distinct from
    # the "isn't calibrated yet" wording a true placeholder spec would get,
    # and (having no caveat entry) none of Guardian's caveat text.
    _patch_spec(monkeypatch, "brewmaster_monk")
    msg = app._uncalibrated_spec_warning()
    assert "characterized" in msg
    assert "isn't calibrated" not in msg
    assert "Ironfur" not in msg
    assert "Elune's-Chosen" not in msg


def test_calibrated_warrior_shows_only_its_own_caveat(monkeypatch):
    # Prot Warrior's tier history: calibrated -> characterized 2026-07-18
    # (Demo Shout + Phalanx double-count fix, PR #387) -> widened further by
    # the 2026-07-21 Shield Block fixes (RMSE 0.162, bias +13.7%) -> closed
    # by Vanguard's strength->armor spec passive same day (RMSE 0.073, bias
    # +1.8%) -> RE-PROMOTED to `calibrated` 2026-07-22 once the LOO-CV gate
    # (the 4th promotion criterion, newly wired into `simf calibrate-k`,
    # PR #416) passed against this same corpus -> DOWNGRADED again
    # 2026-07-25 when that same bar failed against 15 independent players
    # (see docs/validation/protwarrior_calibrated_downgrade_2026_07_25.md) —
    # Prot Warrior is `characterized` again today (test_no_spec_is_
    # calibrated_today in test_constants.py pins this). This test's actual
    # subject is the FORMATTING behaviour at the `calibrated` tier (returns
    # ONLY the per-spec caveat, line 545's early return, no "isn't
    # calibrated" wrapper text) using Prot Warrior's real caveat text as a
    # realistic example — so the tier itself is faked here (same pattern as
    # `test_calibrated_spec_without_caveat_has_no_caption` below) rather
    # than trusted to be Prot Warrior's live value, which keeps this test
    # correct regardless of which way that value next flips. The caveat
    # text itself still legitimately says "characterized" — it names Prot
    # Warrior's REAL, live cross-player-gate status, which the tier fake
    # here doesn't change (the caveat is decoupled from the tier by design).
    _patch_spec(monkeypatch, "protection_warrior")
    real = load_constants()
    fake = dict(real)
    fake["specs"] = {
        **real["specs"],
        "protection_warrior": {
            **real["specs"]["protection_warrior"],
            "calibration_tier": "calibrated",
        },
    }
    monkeypatch.setattr(app, "load_constants", lambda: fake)
    msg = app._uncalibrated_spec_warning()
    rmse_pct = f"{real['calibration']['global_rmse'] * 100:.1f}"
    assert msg == app._SPEC_MODELING_CAVEAT["protection_warrior"].format(rmse_pct=rmse_pct)
    assert "isn't calibrated" not in msg
    assert f"±{rmse_pct}%" in msg
    assert "cross-player" in msg
    assert "cross-validation" in msg


def test_protection_warrior_caveat_interpolates_live_rmse_not_a_stale_literal(monkeypatch):
    """The caveat must never hardcode a specific RMSE figure or promotion
    date — that's exactly how the prior wording ("Re-promoted to
    *calibrated* 2026-07-22 ... +1.8% bias, RMSE 0.073, 16/16 within
    ±15%") went stale within 3 days of the 2026-07-25 downgrade + KYFOTG
    merge (constants.yaml moved to RMSE 0.080 the same day). It must
    interpolate the live `calibration.global_rmse` instead, so a future
    recalibration can't silently leave a wrong number on screen."""
    _patch_spec(monkeypatch, "protection_warrior")
    real = load_constants()
    fake = dict(real)
    fake["calibration"] = {**real["calibration"], "global_rmse": 0.1234}
    monkeypatch.setattr(app, "load_constants", lambda: fake)
    msg = app._uncalibrated_spec_warning()
    assert "calibrated 2026-07-22" not in msg
    assert "Re-promoted" not in msg
    assert "0.073" not in msg
    assert "16/16" not in msg
    assert "+1.8%" not in msg
    assert "12.3%" in msg


def test_calibrated_spec_without_caveat_has_no_caption(monkeypatch):
    """No real spec is `calibrated` today (both Warrior and Guardian were
    downgraded — see `test_no_spec_is_calibrated_today` in test_constants.py)
    — inject a fake one, same pattern as `test_placeholder_tier_keeps_
    original_wording` below, so the `calibrated`-with-no-caveat code path
    (an empty-string return) stays covered rather than silently rotting."""
    _patch_spec(monkeypatch, "totally_fake_calibrated_spec")
    real = load_constants()
    fake = dict(real)
    fake["specs"] = {
        **real["specs"],
        "totally_fake_calibrated_spec": {"calibration_tier": "calibrated"},
    }
    monkeypatch.setattr(app, "load_constants", lambda: fake)
    assert app._uncalibrated_spec_warning() == ""


def test_no_character_loaded_has_no_caption(monkeypatch):
    monkeypatch.setattr(app, "_ss", lambda: {})
    assert app._uncalibrated_spec_warning() == ""


def test_placeholder_tier_keeps_original_wording(monkeypatch):
    """No real spec is `placeholder` today (all 6 have real characterization
    work) — inject a fake one so this code path stays covered rather than
    silently rotting behind the `characterized` branch."""
    _patch_spec(monkeypatch, "totally_fake_spec")
    real = load_constants()
    fake = dict(real)
    fake["specs"] = {**real["specs"], "totally_fake_spec": {"calibration_tier": "placeholder"}}
    monkeypatch.setattr(app, "load_constants", lambda: fake)
    msg = app._uncalibrated_spec_warning()
    assert "isn't calibrated against real logs yet" in msg
    assert "characterized" not in msg
