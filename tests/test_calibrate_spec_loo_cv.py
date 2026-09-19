"""Tests for _run_loo_cv — the leave-one-out cross-validation gate backing
Top-5 #4 (2026-07-06 retrospective).

The script lives outside ``src/`` so we import it via a path-based loader
(no install step), matching ``tests/test_calibrate_spec_from_wcl.py``.

The Warrior fixture below is the EXACT real sweep table from a live run
(``calibrate protection_warrior --logs-dir examples --iters 300``,
2026-07-06) — not synthetic. That live run first caught a real bug: the
original stability check (spread of bestK_-i's own extremes) failed this
corpus even though held-out prediction was a perfect 5/5, because the
default sweep steps by 250 and ANY two-point spread across that grid reads
as "unstable" under a naive spread check. The fix anchors stability to the
full-corpus best K instead. This test pins that fix against the real data
that exposed the bug — a regression here means the fix regressed.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "calibrate_spec_from_logs.py"


def _load():
    spec = importlib.util.spec_from_file_location("calibrate_spec_from_logs", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["calibrate_spec_from_logs"] = mod
    spec.loader.exec_module(mod)
    return mod


cal = _load()

# Real table from `calibrate protection_warrior --logs-dir examples --iters 300`
# (2026-07-06, 5 timed runs). rmse values and per-run deltas as printed.
_WARRIOR_KS = [2000, 2250, 2500, 2750, 3000, 3250, 3430, 3500, 3750, 4000, 4250, 4500, 4750, 5000]
_WARRIOR_TABLE = {
    2000: (0.288, [-36.8, -32.0, -9.0, -27.6, -30.6]),
    2250: (0.242, [-30.5, -28.9, -6.8, -21.2, -25.6]),
    2500: (0.191, [-24.1, -25.2, -4.3, -14.4, -19.7]),
    2750: (0.145, [-17.5, -22.0, -1.7, -8.0, -13.9]),
    3000: (0.102, [-11.1, -18.1, 0.5, -0.6, -8.5]),
    3250: (0.075, [-5.0, -14.5, 2.8, 5.5, -3.4]),
    3430: (0.072, [-0.7, -12.3, 4.7, 9.4, -0.3]),
    3500: (0.075, [0.9, -11.3, 5.4, 11.1, 0.7]),
    3750: (0.101, [6.6, -8.1, 8.1, 17.5, 4.9]),
    4000: (0.136, [11.9, -5.1, 10.4, 23.7, 9.3]),
    4250: (0.179, [18.0, -1.9, 12.8, 29.7, 15.1]),
    4500: (0.218, [23.0, 0.3, 15.3, 35.1, 19.2]),
    4750: (0.256, [27.5, 3.6, 17.6, 40.4, 23.8]),
    5000: (0.297, [33.2, 6.4, 19.6, 45.1, 28.9]),
}
_WARRIOR_LABELS = [
    "WoWCombatLog-050626_15[0] Windrunner Spi+12",
    "WoWCombatLog-050626_17[0] Algeth'ar Acad+12",
    "WoWCombatLog-051026_07[0] Nexus-Point Xe+12",
    "WoWCombatLog-051026_09[2] Windrunner Spi+14",
    "WoWCombatLog-051026_10[1] Pit of Saron+13",
]


def test_real_warrior_corpus_passes_after_stability_fix(capsys):
    """The exact real table that exposed the spread-of-extremes bug must
    now PASS: bestK_-i lands on {3250, 3430, 3500} (all within 180 of the
    full-corpus best K=3430, well under the 300 tolerance), and held-out
    prediction is a perfect 5/5 within +/-15%."""
    result = cal._run_loo_cv(_WARRIOR_TABLE, _WARRIOR_KS, _WARRIOR_LABELS)
    out = capsys.readouterr().out
    assert result is True
    assert "LOO-CV GATE: PASS" in out


def test_stability_anchors_to_full_corpus_not_extreme_spread():
    """A corpus where every excl-i fold agrees closely with the full-corpus
    best K, but the excl-i values themselves span a 250-wide grid, must
    read as STABLE — this is the exact shape the real bug produced."""
    ks = [3250, 3430, 3500]
    # 3 runs; excluding any one still lands close to 3430 either side.
    table = {
        3250: (0.10, [-5.0, -1.0, -3.0]),
        3430: (0.05, [0.0, 0.0, 0.0]),
        3500: (0.10, [5.0, 1.0, 3.0]),
    }
    labels = ["a", "b", "c"]
    result = cal._run_loo_cv(table, ks, labels)
    assert result is True


def test_genuinely_unstable_corpus_still_fails():
    """A corpus where removing different runs swings the best K wildly
    (one run anchors the fit near 2000, another near 5000) must still FAIL
    stability — the fix must not have gutted the check entirely."""
    ks = [2000, 3430, 5000]
    table = {
        # Run 0 only fits near 2000; runs 1/2 only fit near 5000. Excluding
        # run 0 pulls the best K to 5000; excluding run 1 or 2 pulls it to
        # 2000 (2 of 3 votes) — full-corpus best is ambiguous/contested.
        2000: (0.05, [0.0, 40.0, 40.0]),
        3430: (0.30, [20.0, 20.0, 20.0]),
        5000: (0.05, [40.0, 0.0, 0.0]),
    }
    labels = ["a", "b", "c"]
    result = cal._run_loo_cv(table, ks, labels)
    assert result is False


def test_fewer_than_three_runs_skips_with_false():
    result = cal._run_loo_cv({3430: (0.05, [1.0, 2.0])}, [3430], ["a", "b"])
    assert result is False


def test_held_out_failure_reported_independently_of_stability(capsys):
    """A corpus that's K-stable but has a genuinely bad held-out fold
    (mirrors the real Guardian finding) must fail on held-out prediction
    specifically, not get relabeled as a stability failure."""
    ks = [3250, 3430, 3500]
    table = {
        3250: (0.10, [-5.0, -3.0, -30.0]),
        3430: (0.05, [0.0, 0.0, -26.0]),
        3500: (0.10, [5.0, 3.0, -22.0]),
    }
    labels = ["a", "b", "c"]
    result = cal._run_loo_cv(table, ks, labels)
    out = capsys.readouterr().out
    assert result is False
    assert "HARD-FAIL" in out
