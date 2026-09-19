"""Regression test: the Calibration-details popover's stat-weight noise line
must render on the FIRST script pass after a character load, not one rerun
late (Batch review 2026-07-08 — elite_tank + engine-math validator).

Root cause: `_build_run_config` (`ui/load.py`) calls `_marginals_ci_for`
near the top of every script pass, but the gear surface's `_marginals_for`
call — the thing that actually populates the SESSION cache on a fresh
compute — runs LATER in that same pass (`app.py::main`). Opening the popover
is a client-side toggle (no server rerun), so before the fix the line stayed
gated off until some unrelated widget interaction forced a second rerun.

This test simulates "a prior session already warmed the disk cache" — the
on-disk cache's whole reason to exist is surviving a browser refresh — by
writing a CI entry keyed to the demo character's real marginals signature,
then driving a completely FRESH `AppTest` session through the demo-load
click exactly once and asserting the popover already carries the line.
"""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from simf.core import marginals_cache
from simf.core.character import Character
from simf.core.marginals import ehp_marginals
from simf.ui import marginals as marginals_mod
from simf.ui.marginals import (
    MARGINALS_CI_RESAMPLES,
    MARGINALS_SIM_ITERATIONS,
    MARGINALS_SIM_SEED,
    _char_marginals_signature,
    _marginals_ci_for,
)

from .conftest import make_warrior

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_PATH = REPO_ROOT / "src" / "simf" / "ui" / "app.py"


def test_marginals_ci_for_falls_back_to_disk_on_session_miss(monkeypatch, tmp_path):
    """Fast, pure-function pin of the exact fix, independent of the fuller
    AppTest scenario below: a brand-new session (a literal empty dict, the
    same state a fresh browser session starts with) must still resolve the
    CI from disk rather than returning ``None`` until some later rerun
    populates the session cache."""
    session: dict = {}
    monkeypatch.setattr(marginals_mod, "_ss", lambda: session)
    monkeypatch.setenv("SIMF_MARGINALS_CACHE_DIR", str(tmp_path))

    char = make_warrior(versatility_rating=1500)
    sig = _char_marginals_signature(char)
    ci = {"versatility_rating": {"p": (400.0, 600.0), "m": None}}
    marginals_cache.store(sig, ehp_marginals(char), ci=ci, override_dir=tmp_path)

    # Pre-fix behavior: a bare `_ss().get(...)` on the fresh session dict
    # returns None here, even though the disk cache already has the answer.
    got = _marginals_ci_for(char)
    assert got == ci

    # A second call must come from the (now-populated) session cache, not
    # re-hit disk — same contract `_marginals_for` already relies on.
    def _fail_if_called(*_a, **_kw):
        raise AssertionError("load_ci must not be called once the session cache is warm")

    monkeypatch.setattr(marginals_cache, "load_ci", _fail_if_called)
    assert _marginals_ci_for(char) == ci


def test_marginals_ci_for_stays_none_on_a_genuine_cache_miss(monkeypatch, tmp_path):
    """No session entry AND no disk entry must still resolve to `None` —
    the fix adds a fallback, not a fabricated answer."""
    session: dict = {}
    monkeypatch.setattr(marginals_mod, "_ss", lambda: session)
    monkeypatch.setenv("SIMF_MARGINALS_CACHE_DIR", str(tmp_path))

    char = make_warrior()
    assert _marginals_ci_for(char) is None


def _load_demo(at: AppTest) -> AppTest:
    """Cold-load the app, then click the bundled demo button — the exact
    single-click, single-`.run()` path a real user takes to open the
    Calibration-details popover with a character already loaded."""
    at.run()
    demo_btn = next(b for b in at.button if "sample build" in (b.label or "").lower())
    return demo_btn.click().run()


def _prewarm_disk_ci(tmp_path: Path) -> None:
    """Learn the demo character's real marginals signature by loading it
    once, then write a CI entry under that exact key — standing in for "an
    earlier session already paid the 10-20s compute." Deterministic: the
    demo Character (and therefore its signature) is a pure function of the
    bundled yaml/SimC files, so a later, independent session reproduces the
    identical key without needing to share any process state."""
    setup = AppTest.from_file(str(APP_PATH), default_timeout=30)
    _load_demo(setup)
    assert not setup.exception, f"setup demo load failed: {setup.exception}"
    char = Character.from_dict(setup.session_state["char_data"])
    sig = _char_marginals_signature(char)
    marginals = ehp_marginals(char)
    point = marginals["versatility_rating"]["p"]
    assert point != 0.0, "expected a nonzero versatility eHP marginal for the demo character"
    ci = {"versatility_rating": {"p": (0.0, 2 * point), "m": None}}
    marginals_cache.store(sig, marginals, ci=ci, override_dir=tmp_path)


def test_noise_line_renders_on_first_pass_with_a_prewarmed_disk_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("SIMF_MARGINALS_CACHE_DIR", str(tmp_path))
    _prewarm_disk_ci(tmp_path)

    # A brand-new AppTest instance — fresh session state, same on-disk cache
    # dir — loads the demo. The noise line must already be present after
    # this ONE click().run(), with no second rerun.
    at = AppTest.from_file(str(APP_PATH), default_timeout=30)
    at = _load_demo(at)
    assert not at.exception, f"demo load failed: {at.exception}"

    body = "\n".join(str(m.value) for m in at.markdown)
    assert "Stat-weight noise" in body, (
        "the noise line did not render on the first pass after a demo load "
        "with a prewarmed disk cache — the exact render-order bug this test guards"
    )
    assert "versatility ±100%" in body


def test_noise_line_without_prewarm_stays_absent_not_stale(monkeypatch, tmp_path):
    """Sanity check on the other side of the fix: a genuinely cold disk
    cache (no prior session ever computed this character's CI) must still
    omit the line rather than fabricate one — the fix is a render-order
    correction, not a new source of numbers."""
    monkeypatch.setenv("SIMF_MARGINALS_CACHE_DIR", str(tmp_path))

    at = AppTest.from_file(str(APP_PATH), default_timeout=30)
    at = _load_demo(at)
    assert not at.exception, f"demo load failed: {at.exception}"

    body = "\n".join(str(m.value) for m in at.markdown)
    assert "Stat-weight noise" not in body


def test_noise_line_states_school_and_iteration_basis(monkeypatch, tmp_path):
    """The line must name what it's actually measured from — physical
    school, sim iteration count, bootstrap resample count, seed — not the
    old vague '(95% CI, this character)', which let a ±27% read next to an
    unrelated 'iterations: 1,000' elsewhere in the same popover as if it
    were the same sim (it isn't — this CI comes from a smaller, dedicated
    compute; see `ui.marginals.marginal_noise_basis_label`)."""
    monkeypatch.setenv("SIMF_MARGINALS_CACHE_DIR", str(tmp_path))
    _prewarm_disk_ci(tmp_path)

    at = AppTest.from_file(str(APP_PATH), default_timeout=30)
    at = _load_demo(at)
    assert not at.exception, f"demo load failed: {at.exception}"

    body = "\n".join(str(m.value) for m in at.markdown)
    assert "physical school" in body
    assert f"{MARGINALS_SIM_ITERATIONS} iters" in body
    assert f"{MARGINALS_CI_RESAMPLES:,} bootstrap resamples" in body
    assert f"seed {MARGINALS_SIM_SEED}" in body
