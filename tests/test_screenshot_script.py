"""Behaviour pin for `scripts/screenshot.py` failure-loudness.

Three loop iterations were misled by silent-success PNGs: the demo click
target wasn't reachable, the script printed a one-line warning to stderr,
and then still wrote a PNG of the empty landing surface and returned 0.

These tests mock Playwright so we can exercise the click-routing logic
without a real browser, and assert the script raises `SystemExit(2)` with
a clear message instead of falling through to the screenshot step.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "screenshot.py"


def _load_module():
    """Load scripts/screenshot.py as a regular module (not on sys.path)."""
    spec = importlib.util.spec_from_file_location("screenshot_script", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakePage:
    """Page double whose every click can be configured to throw."""

    def __init__(
        self,
        *,
        demo_click_raises: bool = False,
        demo_sentinel_visible: bool = True,
        tab_click_raises: bool = False,
        content_height: int | list[int] = 0,
    ):
        self._demo_click_raises = demo_click_raises
        self._demo_sentinel_visible = demo_sentinel_visible
        self._tab_click_raises = tab_click_raises
        # A list simulates the real "content only reveals itself gradually
        # as the viewport grows" behaviour — each `evaluate` call consumes
        # the next value, repeating the last once exhausted. A bare int is
        # a constant reading (the common case: converges in one resize).
        self._content_heights = (
            list(content_height) if isinstance(content_height, list) else [content_height]
        )
        self.screenshot = MagicMock()

    # Playwright surface — only the bits screenshot.py touches.
    def goto(self, *_a, **_kw):
        return None

    def wait_for_timeout(self, *_a, **_kw):
        return None

    def wait_for_load_state(self, *_a, **_kw):
        return None

    def get_by_role(self, role, name=None):
        # Routing based on (role, name) — return a locator whose .click()
        # either succeeds or raises per the test's configuration. The
        # Vault/Gear sub-nav is a button pair, not `st.tabs` (2026-07-11 —
        # see app.py::_set_gear_subtab): the post-demo sentinel probes for a
        # "Vault"-labeled button (unambiguous — no top-level nav button says
        # "Vault"). `--tab gear`/`--tab vault` click by widget `key=`
        # instead (see `.locator()` below), not by label.
        if role == "button" and name == "Load sample build":
            return _FakeLocator(raises=self._demo_click_raises)
        if role == "button" and name == "Vault":
            # `count()` is used as the post-demo sentinel probe.
            visible = self._demo_sentinel_visible
            return _FakeLocator(raises=False, count_value=1 if visible else 0)
        # Default: return a locator that succeeds and reports count=1.
        return _FakeLocator(raises=False, count_value=1)

    def get_by_text(self, *_a, **_kw):
        return _FakeLocator(raises=False, count_value=1)

    def locator(self, selector, *_a, **_kw):
        # `--tab gear|vault` clicks the sub-nav button via its widget
        # `key=` (`.st-key-<key> button` — see screenshot.py's
        # `_SUBTAB_KEYS`), not by label, so a copy-only rename of either
        # button's text can't silently break this click.
        if "st-key-gear_subtab_" in selector:
            return _FakeLocator(raises=self._tab_click_raises, count_value=1)
        return _FakeLocator(raises=False, count_value=1)

    def evaluate(self, *_a, **_kw):
        # Real Playwright: measures how far real content actually extends
        # (`_max_content_bottom`) so the final screenshot can grow the
        # viewport to fit it (see screenshot.py's full_page fix,
        # 2026-07-13). Defaults to 0 so click-routing/failure-loudness
        # tests never trigger the resize path; override via
        # `content_height=` to test that path specifically.
        if len(self._content_heights) > 1:
            return self._content_heights.pop(0)
        return self._content_heights[0]

    def close(self, *_a, **_kw):
        # Real Playwright: a too-short viewport closes this page and
        # replays every interaction against a freshly-sized one instead
        # (see screenshot.py's full_page fix). No-op here.
        return None


class _FakeLocator:
    def __init__(self, *, raises: bool = False, count_value: int = 1):
        self._raises = raises
        self._count = count_value
        self.first = self  # `.first`/`.last` chains return the same locator
        self.last = self
        self._presentation = self

    def click(self, *_a, **_kw):
        if self._raises:
            raise RuntimeError("locator not found")

    def hover(self, *_a, **_kw):
        if self._raises:
            raise RuntimeError("locator not found")

    def count(self):
        return self._count


def _fake_playwright(page: _FakePage, new_context_calls: list | None = None):
    """Build a sync_playwright() context-manager double that yields `page`.

    Every real page (see screenshot.py's full_page fix) is created via
    ``browser.new_context(viewport=...).new_page()`` — a too-short first
    measurement closes that page and opens a SECOND context sized to fit,
    replaying every interaction on it. Recording each ``new_context`` call's
    kwargs (when a list is supplied) lets a test assert on that replay
    directly instead of on a same-page resize call that no longer exists."""

    def _new_context(**kwargs):
        if new_context_calls is not None:
            new_context_calls.append(kwargs)
        return SimpleNamespace(new_page=lambda: page)

    browser = SimpleNamespace(new_context=_new_context, close=lambda: None)
    pw = SimpleNamespace(firefox=SimpleNamespace(launch=lambda: browser))

    class _CM:
        def __enter__(self):
            return pw

        def __exit__(self, *_a):
            return False

    return lambda: _CM()


def _run_main(monkeypatch, argv, page: _FakePage, new_context_calls: list | None = None):
    mod = _load_module()
    monkeypatch.setattr(mod, "sync_playwright", _fake_playwright(page, new_context_calls))
    monkeypatch.setattr(sys, "argv", argv)
    return mod.main()


def test_demo_click_failure_raises_loud(monkeypatch, tmp_path):
    """If `--demo` can't find the button, the script must fail non-zero.

    Pre-fix behaviour: stderr warning + return 0 + empty PNG written.
    """
    page = _FakePage(demo_click_raises=True)
    out = tmp_path / "x.png"
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, ["screenshot.py", str(out), "--demo"], page)
    assert exc.value.code == 2
    # And critically: no screenshot was taken — the empty-PNG footgun is gone.
    assert page.screenshot.call_count == 0


def test_demo_sentinel_missing_raises_loud(monkeypatch, tmp_path):
    """`--demo` click "succeeds" but Streamlit didn't actually load.

    The post-click probe should catch this and fail non-zero.
    """
    page = _FakePage(demo_click_raises=False, demo_sentinel_visible=False)
    out = tmp_path / "x.png"
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, ["screenshot.py", str(out), "--demo"], page)
    assert exc.value.code == 2
    assert page.screenshot.call_count == 0


def test_demo_happy_path_returns_zero(monkeypatch, tmp_path):
    """Sanity: when the click works AND the sentinel shows, screenshot lands."""
    page = _FakePage(demo_click_raises=False, demo_sentinel_visible=True)
    out = tmp_path / "x.png"
    rc = _run_main(monkeypatch, ["screenshot.py", str(out), "--demo"], page)
    assert rc == 0
    assert page.screenshot.call_count == 1


def test_viewport_grows_to_fit_real_content_height(monkeypatch, tmp_path):
    """Regression test: `full_page=True` alone was confirmed (2026-07-13) to
    silently truncate to the passed `--height` for this app — its real
    scroll container's `scrollHeight` is tied to the viewport rather than
    genuine overflow, and a same-page resize doesn't recover the missing
    content either. The script must close the short page and replay every
    interaction against a fresh, taller one whenever the measured content
    extent exceeds what was passed — with a margin added on top of the
    measurement, since real content only reveals itself gradually as the
    viewport grows (see `_max_content_bottom`'s docstring)."""
    page = _FakePage(demo_click_raises=False, demo_sentinel_visible=True, content_height=9000)
    out = tmp_path / "x.png"
    calls: list = []
    rc = _run_main(
        monkeypatch, ["screenshot.py", str(out), "--demo", "--height", "1200"], page, calls
    )
    assert rc == 0
    assert calls == [
        {"viewport": {"width": 1400, "height": 1200}},
        {"viewport": {"width": 1400, "height": 9200}},
    ], "must open a second, correctly-sized (measurement + margin) context, not resize the first"
    assert page.screenshot.call_count == 1


def test_viewport_left_alone_when_content_fits(monkeypatch, tmp_path):
    """The inverse: don't replay via a second context when the passed
    height already covers the real content — no point paying for a second
    full interaction sequence."""
    page = _FakePage(demo_click_raises=False, demo_sentinel_visible=True, content_height=500)
    out = tmp_path / "x.png"
    calls: list = []
    rc = _run_main(
        monkeypatch, ["screenshot.py", str(out), "--demo", "--height", "1200"], page, calls
    )
    assert rc == 0
    assert calls == [{"viewport": {"width": 1400, "height": 1200}}]
    assert page.screenshot.call_count == 1


def test_viewport_growth_keeps_replaying_until_measurement_stabilizes(monkeypatch, tmp_path):
    """Pins the exact quirk that broke a single-resize fix (2026-07-13): a
    1100px viewport measured real content at ~6448px, but re-measuring at
    a 6494px viewport found the true figure was actually ~9112px, and
    higher again at 9000px, before finally stabilizing near 13000px — the
    content only reveals itself gradually as the viewport grows. The
    script must keep replaying against progressively taller contexts
    until a measurement no longer exceeds the viewport it was taken in,
    not stop after a single resize."""
    page = _FakePage(
        demo_click_raises=False,
        demo_sentinel_visible=True,
        content_height=[6448, 9112, 12988, 12988],
    )
    out = tmp_path / "x.png"
    calls: list = []
    rc = _run_main(
        monkeypatch, ["screenshot.py", str(out), "--demo", "--height", "1100"], page, calls
    )
    assert rc == 0
    # height=1100 (initial) -> measures 6448 (>1100, grow to 6448+200) ->
    # measures 9112 (>6648, grow to 9112+200) -> measures 12988 (>9312, grow
    # to 12988+200) -> measures 12988 again (<=13188, converged, stop).
    assert calls == [
        {"viewport": {"width": 1400, "height": 1100}},
        {"viewport": {"width": 1400, "height": 6648}},
        {"viewport": {"width": 1400, "height": 9312}},
        {"viewport": {"width": 1400, "height": 13188}},
    ], "must keep growing until a measurement fits inside its own viewport"
    assert page.screenshot.call_count == 1


def test_viewport_growth_gives_up_after_max_attempts(monkeypatch, tmp_path):
    """A pathological page whose measured content keeps exceeding the
    viewport forever must not loop forever — cap attempts and shoot
    whatever the last replay produced rather than hang. Each measurement
    below is deliberately larger than the previous viewport plus its
    margin, so the loop never converges and must hit the hard cap."""
    page = _FakePage(
        demo_click_raises=False,
        demo_sentinel_visible=True,
        content_height=[2200, 5000, 11000, 23000, 47000],
    )
    out = tmp_path / "x.png"
    calls: list = []
    rc = _run_main(
        monkeypatch, ["screenshot.py", str(out), "--demo", "--height", "1100"], page, calls
    )
    assert rc == 0
    # 1 initial context + exactly `_MAX_RESIZE_ATTEMPTS` (5) resize replays,
    # then it must give up rather than loop forever.
    assert len(calls) == 6, "must be bounded, not an unbounded/infinite replay loop"
    assert page.screenshot.call_count == 1


def test_tab_click_failure_raises_loud(monkeypatch, tmp_path):
    """`--tab gear` should also be load-bearing.

    A tab screenshot that can't find the tab is useless to a reviewer.
    """
    page = _FakePage(
        demo_click_raises=False,
        demo_sentinel_visible=True,
        tab_click_raises=True,
    )
    out = tmp_path / "x.png"
    with pytest.raises(SystemExit) as exc:
        _run_main(
            monkeypatch,
            ["screenshot.py", str(out), "--demo", "--tab", "gear"],
            page,
        )
    assert exc.value.code == 2
    assert page.screenshot.call_count == 0
