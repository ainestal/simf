"""`scripts/screenshot.py` settle-wait (2026-06-13).

The 'Gear' tab label appears the instant a character lands in session state,
but the gear surface then spends ~10-20s computing survivability marginals
behind a spinner — a fixed `--wait-ms` captured that mid-rerun frame.
`_settle()` waits for the spinner to appear-then-clear first.

Driven by a fake page (duck-typed Playwright `page`) so the logic is tested
without a browser. (Playwright itself is a test-env dependency — the sibling
test_screenshot_script.py monkeypatches the module-level `sync_playwright`.)
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "screenshot.py"


@pytest.fixture(scope="module")
def screenshot_mod():
    spec = importlib.util.spec_from_file_location("simf_screenshot", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeLocator:
    def __init__(self, calls, name, raise_states):
        self._calls, self._name, self._raise = calls, name, raise_states

    @property
    def first(self):
        return self

    def wait_for(self, state=None, timeout=None):
        self._calls.append(("wait_for", self._name, state, timeout))
        if state in self._raise:
            raise RuntimeError("simulated playwright timeout")


class _FakePage:
    def __init__(self, raise_states=()):
        self.calls: list = []
        self._raise = set(raise_states)

    def locator(self, selector):
        self.calls.append(("locator", selector))
        return _FakeLocator(self.calls, selector, self._raise)

    def wait_for_load_state(self, state):
        self.calls.append(("wait_for_load_state", state))

    def wait_for_timeout(self, ms):
        self.calls.append(("wait_for_timeout", ms))


def test_settle_waits_for_spinner_attach_then_detach_then_paints(screenshot_mod):
    page = _FakePage()
    screenshot_mod._settle(page, settle_ms=9000, final_ms=250)
    # Targets the marginals spinner by its stable Streamlit testid.
    assert ("locator", screenshot_mod._SPINNER_TESTID) in page.calls
    waits = [c for c in page.calls if c[0] == "wait_for"]
    states = [w[2] for w in waits]
    # attached (spinner appears, even though absent at call time → no race) then
    # detached (compute done).
    assert states == ["attached", "detached"], states
    attached_wait = next(w for w in waits if w[2] == "attached")
    detached_wait = next(w for w in waits if w[2] == "detached")
    assert detached_wait[3] == 9000  # generous settle cap
    assert attached_wait[3] <= 10000  # short appearance grace
    # networkidle + a final paint wait close it out.
    assert ("wait_for_load_state", "networkidle") in page.calls
    assert ("wait_for_timeout", 250) in page.calls


def test_settle_is_best_effort_when_waits_time_out(screenshot_mod):
    """A spinner that never appears (cached marginals) or a timeout must not
    raise — the capture proceeds with the final paint wait regardless."""
    page = _FakePage(raise_states={"visible", "hidden"})
    screenshot_mod._settle(page, settle_ms=1000, final_ms=300)  # must not raise
    # Even though both spinner waits threw, the final paint wait still ran.
    assert ("wait_for_timeout", 300) in page.calls


def test_settle_ms_arg_and_settle_wired_into_demo_and_tab():
    """`--settle-ms` exists with a generous default, and both compute-heavy
    transitions (demo load, tab click) call `_settle` rather than a bare
    fixed wait. Source-level guard — main() needs a browser to run."""
    src = _SCRIPT.read_text()
    assert '"--settle-ms"' in src
    assert "default=120_000" in src
    # Both transitions settle.
    demo_block = src.split("if args.demo:")[1].split("if args.tab:")[0]
    tab_block = src.split("if args.tab:")[1].split("# Expanders run before")[0]
    assert "_settle(page, args.settle_ms, args.wait_ms)" in demo_block
    assert "_settle(page, args.settle_ms, args.wait_ms)" in tab_block
