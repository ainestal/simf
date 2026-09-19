"""Unit tests for Midnight stat diminishing returns (audit F5).

Verifies that apply_secondary_dr() implements the Maxroll DR table correctly
at each bracket boundary, and that the per-stat _pct() methods on Character
correctly route the rating-derived portion through DR while leaving flat
baselines (base crit 5%, base mastery) untouched.
"""

from __future__ import annotations

import pytest

from simf.core.character import Character, apply_secondary_dr

# ---- Direct function tests ---------------------------------------------------


def test_dr_no_op_below_first_breakpoint():
    """Linear < 30% → effective == linear (no DR applied)."""
    assert apply_secondary_dr(0.10) == pytest.approx(0.10)
    assert apply_secondary_dr(0.2318) == pytest.approx(0.2318)  # Brutoh's haste
    assert apply_secondary_dr(0.2999) == pytest.approx(0.2999)


def test_dr_at_first_breakpoint_exactly():
    """Linear 30% → effective 30% (boundary)."""
    assert apply_secondary_dr(0.30) == pytest.approx(0.30)


def test_dr_in_first_penalty_bracket():
    """Linear 40% → effective 39% (30% + (10% × 0.9))."""
    # 0-30 is linear (cost 30 linear / 0.30 effective)
    # 30-39 effective costs 9 / 0.9 = 10 linear → spans linear 30-40.
    assert apply_secondary_dr(0.40) == pytest.approx(0.39, abs=1e-6)
    # Halfway through the bracket: linear 35 → effective 30 + 5*0.9 = 34.5
    assert apply_secondary_dr(0.35) == pytest.approx(0.345, abs=1e-6)


def test_dr_at_second_breakpoint():
    """Linear 40 + 10 (passing through 40-50 at -20%) → effective 39 + 8 = 47."""
    # Linear 0.40 is at effective 0.39 (end of first penalty bracket).
    # Second bracket: 39-47 effective, efficiency 0.80, linear cost 8/0.8 = 10.
    # So linear 0.40 + 0.10 = 0.50 → effective 0.39 + 8 = 0.47.
    assert apply_secondary_dr(0.50) == pytest.approx(0.47, abs=1e-6)


def test_dr_high_value_caps():
    """Past 200% linear → hard cap ~125% (Midnight brackets)."""
    # Cumulative bracket costs (Midnight linear 30/40/50/60/70/200):
    #   0-30 linear (0.30 eff)
    #   30-40 linear (0.39 eff, +0.09)
    #   40-50 linear (0.47 eff, +0.08)
    #   50-60 linear (0.54 eff, +0.07)
    #   60-70 linear (0.60 eff, +0.06)  [-40% bracket; 6/0.6 = 10 linear]
    #   70-200 linear (1.25 eff, +0.65) [-50% bracket; 65/0.5 = 130 linear]
    # So linear 1.0 (100%) → 0.60 + 0.30*0.5 = 0.75 effective.
    assert apply_secondary_dr(1.00) == pytest.approx(0.75, abs=1e-6)
    # Push very high — should hit the ~125% hard cap.
    assert apply_secondary_dr(10.0) <= 1.25


def test_dr_zero_or_negative_is_zero():
    assert apply_secondary_dr(0.0) == 0.0
    assert apply_secondary_dr(-0.05) == 0.0


# ---- Character integration tests ---------------------------------------------


def _mk_char(haste=0, crit=0, mastery=0, vers=0):
    return Character(
        name="dr-test",
        race="human",
        class_spec="protection_warrior",
        talents="archon-meta",
        strength=2000,
        stamina=30000,
        armor_from_gear=5000,
        haste_rating=haste,
        crit_rating=crit,
        mastery_rating=mastery,
        versatility_rating=vers,
    )


def test_brutoh_no_dr_applied():
    """Brutoh's actual stats sit below all DR thresholds, so DR is a no-op.

    Ratings in REAL lvl-90 units (haste/crit/vers at 44/46/54; mastery stays /100)
    so the percentages match his in-game sheet.
    """
    c = _mk_char(haste=1020, crit=640, mastery=1608, vers=160)
    # Haste 23.18% rating → 23.18% effective (no DR). (abs=1e-3: integer-rating rounding)
    assert c.haste_pct() == pytest.approx(0.2318, abs=1e-3)
    # Crit: 5% base + 13.91% rating → 5% + 13.91% (rating part below DR).
    assert c.crit_pct() == pytest.approx(0.05 + 0.1391, abs=1e-3)
    # Mastery: 12% base + 16.08% rating → no DR applied to rating portion (16% < 30%).
    assert c.mastery_pct() == pytest.approx(0.12 + 0.1608, abs=1e-3)
    # Versatility: 2.96% rating → 2.96% (DR a no-op).
    assert c.versatility_pct() == pytest.approx(0.0296, abs=1e-3)


def test_high_haste_dr_applies():
    """A player with 40% linear haste (rating 1760 at 44/1%) → 39% effective."""
    c = _mk_char(haste=1760)  # 1760/44 = 40% linear
    assert c.haste_pct() == pytest.approx(0.39, abs=1e-6)


def test_crit_base_5pct_outside_dr():
    """5% base crit is a class baseline — sits outside DR. Only rating portion sees DR."""
    # 35% rating-derived crit → linear 35% → effective 34.5% (in -10% bracket).
    # Plus 5% base → 39.5%.  (rating 1610 / 46 = 35% linear)
    c = _mk_char(crit=1610)
    assert c.crit_pct() == pytest.approx(0.05 + 0.345, abs=1e-6)


def test_mastery_base_outside_dr():
    """12% base mastery (Prot Warrior) sits outside DR."""
    # 50% rating mastery → linear 50% past two breakpoints:
    # 30 linear (0.30 eff) + 10 linear (0.39 eff) + 10 linear (0.47 eff) = 50 linear → 47 eff.
    # Plus 12% base → 59%.
    c = _mk_char(mastery=5000)
    assert c.mastery_pct() == pytest.approx(0.12 + 0.47, abs=1e-6)


def test_vers_dr_applies_to_dr_method_too():
    """versatility_dr() halves versatility_pct(); DR-adjusted vers flows through."""
    c = _mk_char(vers=2160)  # 2160/54 = 40% linear
    # Linear 40% vers → effective 39% (first penalty bracket) → DR halved to 19.5%
    assert c.versatility_dr() == pytest.approx(0.39 * 0.5, abs=1e-6)
