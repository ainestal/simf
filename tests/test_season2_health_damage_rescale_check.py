"""Unit tests for ``scripts/season2_health_damage_rescale_check.py``.

The Monte Carlo comparison itself (``main()``) is not exercised here — it
runs 6 real ``run_simulation`` calls at 5000 iterations each (~40s), the
same scope discipline ``tests/test_calibrate_spec_from_wcl.py`` and
``tests/test_recalibrate_from_community_corpus.py`` already apply to their
own expensive/network-bound siblings. This file covers
``_scale_healing_profile_externals`` — the one piece of real logic in the
script (deciding which ``HealingExternal`` fields need scaling and which
don't), loaded via the same path-based technique used for those sibling
scripts (``scripts/`` is not an importable package).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from simf.core.profiles import HealingExternal, HealingProfile

_SCRIPT = (
    Path(__file__).resolve().parent.parent / "scripts" / "season2_health_damage_rescale_check.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("season2_health_damage_rescale_check", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["season2_health_damage_rescale_check"] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load()


def _profile_with_externals(externals):
    return HealingProfile(
        profile="test",
        baseline_hps_pct_of_dtps=1.0,
        externals=externals,
    )


def test_scales_absolute_amount_field():
    hp = _profile_with_externals(
        [HealingExternal(time_s=60, type="absorb", amount=8_000_000, duration_s=10)]
    )
    scaled = mod._scale_healing_profile_externals(hp, 1.25)
    assert scaled.externals[0].amount == 10_000_000


def test_leaves_percentage_field_untouched():
    """`amount_pct` (e.g. a 40% DR cooldown) is already scale-invariant —
    the helper must not touch it."""
    hp = _profile_with_externals(
        [HealingExternal(time_s=30, type="dr_cooldown", amount_pct=0.40, duration_s=8)]
    )
    scaled = mod._scale_healing_profile_externals(hp, 1.25)
    assert scaled.externals[0].amount_pct == 0.40
    assert scaled.externals[0].amount is None


def test_leaves_none_amount_as_none_not_zero():
    """A percentage-only external's `amount` is `None`, not `0` — scaling
    must preserve that `None`, not silently turn it into `0.0` (which
    would read as "no absorb" rather than "not an absolute-amount type")."""
    hp = _profile_with_externals([HealingExternal(time_s=30, type="dr_cooldown", amount_pct=0.40)])
    scaled = mod._scale_healing_profile_externals(hp, 1.25)
    assert scaled.externals[0].amount is None


def test_scales_every_external_independently():
    hp = _profile_with_externals(
        [
            HealingExternal(time_s=30, type="dr_cooldown", amount_pct=0.40, duration_s=8),
            HealingExternal(time_s=60, type="absorb", amount=8_000_000, duration_s=10),
            HealingExternal(time_s=90, type="dr_cooldown", amount_pct=0.40, duration_s=8),
            HealingExternal(time_s=120, type="absorb", amount=4_000_000, duration_s=10),
        ]
    )
    scaled = mod._scale_healing_profile_externals(hp, 1.25)
    assert scaled.externals[0].amount_pct == 0.40
    assert scaled.externals[1].amount == 10_000_000
    assert scaled.externals[2].amount_pct == 0.40
    assert scaled.externals[3].amount == 5_000_000


def test_does_not_mutate_the_baseline_profile():
    """A real regression risk for a `replace()`-based helper: the ORIGINAL
    profile object must stay untouched, so a caller can still run the
    unscaled baseline sim afterward."""
    hp = _profile_with_externals(
        [HealingExternal(time_s=60, type="absorb", amount=8_000_000, duration_s=10)]
    )
    mod._scale_healing_profile_externals(hp, 1.25)
    assert hp.externals[0].amount == 8_000_000


def test_other_healing_profile_fields_pass_through_unchanged():
    """Only `externals` should change — `baseline_hps_pct_of_dtps` and the
    other percentage-of-max_hp/DTPS fields already self-scale and must not
    be touched by this helper."""
    hp = HealingProfile(
        profile="test",
        baseline_hps_pct_of_dtps=0.95,
        reactive_threshold_hp_pct=0.40,
        reactive_burst_pct_of_max_hp=0.45,
        healer_budget_capacity_pct_of_max_hp=1.2,
        healer_budget_refill_pct_of_max_hp_per_s=0.03,
        externals=[HealingExternal(time_s=60, type="absorb", amount=8_000_000, duration_s=10)],
    )
    scaled = mod._scale_healing_profile_externals(hp, 1.25)
    assert scaled.baseline_hps_pct_of_dtps == 0.95
    assert scaled.reactive_threshold_hp_pct == 0.40
    assert scaled.reactive_burst_pct_of_max_hp == 0.45
    assert scaled.healer_budget_capacity_pct_of_max_hp == 1.2
    assert scaled.healer_budget_refill_pct_of_max_hp_per_s == 0.03
