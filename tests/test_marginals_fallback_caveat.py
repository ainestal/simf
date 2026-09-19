"""The single-flight-busy branch of `_marginals_for` must surface the same
caveat the sibling exception/anchor-failed branches already do.

Found by a launch-readiness audit (2026-08-01): under concurrent public
load, a visitor who loses the `_sim_slot()` race gets the closed-form
`ehp_marginals` approximation for that render with NO on-screen indication
— exactly the silent-degradation regression Phase 2.7 fixed for the other
two fallback paths in this same function. This is the common case under
load, not a rare one, so the gap matters more than its severity looks.
"""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

_BRUTOH_YAML = Path(__file__).parent.parent / "src" / "simf" / "data" / "characters" / "brutoh.yaml"


def _script() -> str:
    return f"""
import streamlit as st
from simf.ui.marginals import _marginals_for, _surv_marginals_fallback_warning
from simf.core.character import Character
import yaml

with open({str(_BRUTOH_YAML)!r}) as f:
    d = yaml.safe_load(f)
char_data = {{k: v for k, v in d.items() if k in Character.__dataclass_fields__}}
char = Character(**char_data)

marginals = _marginals_for(char)
st.session_state["_test_fallback_reason"] = _surv_marginals_fallback_warning(char)
"""


def test_single_flight_busy_sets_the_fallback_caveat(monkeypatch, tmp_path):
    """With the single sim slot held externally (simulating a concurrent
    visitor's real compute in flight) and a fresh disk cache (so this can't
    short-circuit on a cache hit instead of actually reaching the lock
    check), `_marginals_for` must record a non-None fallback reason —
    the UI's caveat caption (app.py / gear_surface.py) reads exactly this
    flag to decide whether to warn the visitor."""
    monkeypatch.setenv("SIMF_MARGINALS_CACHE_DIR", str(tmp_path / "marginals_cache"))
    monkeypatch.setenv("SIMF_FAST_MARGINALS", "")  # this test IS the fast path — don't skip it

    from simf.ui import state as state_mod

    acquired = state_mod._SIM_LOCK.acquire(blocking=False)
    assert acquired, "test setup itself failed to acquire the lock"
    try:
        at = AppTest.from_string(_script())
        at.run()
    finally:
        state_mod._SIM_LOCK.release()

    assert at.exception == []
    reason = at.session_state["_test_fallback_reason"]
    assert reason is not None, (
        "Single-flight-busy branch returned the closed-form fallback but left "
        "the caveat flag at None — a visitor sees degraded numbers with no "
        "on-screen warning (the exact regression Phase 2.7 fixed elsewhere)."
    )
    assert "another visitor" in reason or "sim is running" in reason


def test_slot_free_leaves_no_stale_caveat(monkeypatch, tmp_path):
    """Negative control: with the slot free, a successful compute must NOT
    report a fallback.

    Originally ran the real ~300-iteration Monte Carlo compute (a genuine
    pattern this test file's first version copied from
    test_survivability_weights.py) — fine on this box (a few seconds), but
    CI's runner has fewer cores, and under `-n auto` + `--cov` it blew past
    even a 60s AppTest timeout (CI run 31128316493, 2026-08-06). This test
    only needs to prove _marginals_for's SUCCESS branch clears the flag, not
    that the real simulation works (that's test_survivability_weights.py's
    job) — so the compute itself is monkeypatched to return instantly."""
    monkeypatch.setenv("SIMF_MARGINALS_CACHE_DIR", str(tmp_path / "marginals_cache"))
    monkeypatch.setenv("SIMF_FAST_MARGINALS", "")

    import simf.ui.marginals as marginals_mod
    from simf.core.survivability_weights import SurvivabilityWeights

    monkeypatch.setattr(
        marginals_mod,
        "compute_survivability_marginals",
        lambda *args, **kwargs: SurvivabilityWeights(marginals={}, meta={}, ci={}),
    )

    at = AppTest.from_string(_script())
    at.run()

    assert at.exception == []
    reason = at.session_state["_test_fallback_reason"]
    assert reason is None, f"Unexpected fallback on a free slot: {reason!r}"
