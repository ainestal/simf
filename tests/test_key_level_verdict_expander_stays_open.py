"""Regression tests for the "Key-level verdict" panel — a panel that went
through SEVEN fix attempts chasing a real, live-reported duplicate-panel bug
(examples/screenshots/double.png, 2026-07-19) before the root cause was
found and reproduced deterministically on localhost.

The real mechanism (proven by reproduction, not theory — see PR #397):

1. **Positional element-tree drift.** Streamlit reconciles elements purely
   by tree position. `load._scroll_to_top_if_requested` used to render its
   one-shot scroll-to-top `st.html` ONLY on the load-transition run, so the
   main flow held one more element on that run than on every run after it —
   shifting every element on the page up one position on the first rerun
   after a load.
2. **A long-blocking run makes the shift visible.** This panel is the only
   place in the app that blocks the script mid-render for 8-30s (the sweep,
   plus the skill ladder's ~5s). On the shifted rerun, the new panel writes
   one slot earlier while the previous run's panel node — whose slot is only
   overwritten when the script writes PAST the panel (the build footer,
   rendered after the compute returns) — sits on screen as a stale, dimmed,
   `data-stale="true"` duplicate for the entire compute.
3. The natural user flow hits this 100% of the time: load character → open
   expander (client-side, no rerun) → click Compute — the click is the
   FIRST rerun after the transition run. Every prior automated repro flow
   had at least one extra rerun in between, which consumed the drift
   invisibly in milliseconds. That — not network latency, not browser
   engine — is why six rounds of localhost testing never reproduced it.

The fix is two independent layers, each sufficient for this panel:

* Root cause: `_scroll_to_top_if_requested` now renders exactly one element
  (an `st.empty()` slot) on EVERY run — tested here by asserting the main
  flow's element count is identical between a transition run and the run
  after it.
* Hardening: the panel body renders inside `st.fragment`, so the compute
  click reruns only the panel's own stable container and never participates
  in full-page positional reconciliation — no matter what conditional
  elements exist above it, today or in the future.

Earlier fix attempts that are KEPT (real bugs, just not this bug's cause):
the control-row caption no longer contradicts fresh results; both
`st.spinner()` calls stay removed (streamlit/streamlit#14404 — an open
upstream bug where a widget block followed by a later spinner in the same
run can leave the previous render's subtree permanently unpruned; it made
the transient duplicate PERMANENT on the spinner build); and `expanded=` is
a static `False` every render, relying on Streamlit 1.56+'s native
client-side open-state persistence (confirmed empirically with a
standalone probe app: a manually-opened expander stays open indefinitely
under a permanently-`False` server-sent prop).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from simf.core.key_level_verdict import KeyLevelPoint, KeyLevelVerdict

APP_PATH = Path(__file__).parent.parent / "src" / "simf" / "ui" / "app.py"

_EXPANDER_LABEL = "🔑 Key-level verdict — am I tankable enough?"


def _prot_warrior_char_data() -> dict:
    return {
        "name": "Brutoh",
        "race": "earthen",
        "class_spec": "protection_warrior",
        "talents": "kiratank-defensive",
        "strength": 2182,
        "stamina": 34176,
        "armor_from_gear": 5015,
        "haste_rating": 2318,
        "crit_rating": 1391,
        "mastery_rating": 1608,
        "versatility_rating": 296,
        "max_hp_override": 751872,
    }


def _fake_verdict() -> KeyLevelVerdict:
    return KeyLevelVerdict(
        points=[
            KeyLevelPoint(
                key_level=15,
                damage_multiplier=1.0,
                death_rate=0.02,
                mean_dtps=40_000.0,
                p99_5s_window=1.0,
                band="comfortable",
                mean_hrps=1_000.0,
                normalized_tank_score=0.85,
            )
        ],
        comfortable_max=15,
        prog_ceiling=None,
        affix="fortified",
    )


@pytest.fixture
def app(monkeypatch) -> AppTest:
    # The real sweep runs ~10 sims and would make this test slow/flaky
    # under CI CPU contention — same reasoning `test_load.py`'s Batch E
    # preview-button fix already gives for mocking the sim call rather
    # than running it for real. The panel re-imports
    # `compute_key_level_verdict` lazily inside the render body, so
    # patching the source module's attribute lands even under AppTest.
    import simf.core.key_level_verdict as klv_mod

    monkeypatch.setattr(klv_mod, "compute_key_level_verdict", lambda **kwargs: _fake_verdict())

    at = AppTest.from_file(str(APP_PATH), default_timeout=30)
    at.session_state["char_data"] = _prot_warrior_char_data()
    at.session_state["view"] = "gear"
    return at


def _find_expander(at: AppTest):
    matches = [e for e in at.expander if e.label == _EXPANDER_LABEL]
    assert matches, f"Expander {_EXPANDER_LABEL!r} not found in rendered app"
    # Exactly one, not just "at least one" — a prior version of this helper
    # only checked truthiness and would have silently passed even if two
    # real expanders with this label existed (the actual live bug shape:
    # examples/screenshots/double.png, 2026-07-19).
    assert len(matches) == 1, f"Expected exactly 1 expander, found {len(matches)}"
    return matches[0]


def test_main_flow_element_count_stable_across_load_transition(app: AppTest) -> None:
    """THE drift regression test — the root cause of the duplicated panel.

    A load-transition run (scroll-to-top flag armed) must render exactly
    as many top-level main-flow elements as the plain rerun that follows
    it. Before the stable-slot fix in `load._scroll_to_top_if_requested`,
    the transition run rendered one MORE (the one-shot scroll `st.html`),
    shifting every element's tree position on the next rerun — which,
    combined with this panel's 8-30s inline compute, rendered the whole
    panel twice for the duration of the sweep (the live user report).

    Mutation-verified: with the one-shot `if`-gated `st.html` restored,
    this asserts 22 == 21 and fails.
    """
    app.session_state["_scroll_to_top_on_next_render"] = True
    app.run()
    assert not app.exception, f"Unhandled exception: {app.exception}"
    n_transition = len(app.main.children)

    app.run()
    assert not app.exception, f"Unhandled exception: {app.exception}"
    n_plain = len(app.main.children)

    assert n_transition == n_plain, (
        f"main-flow element count drifted across the load transition "
        f"({n_transition} → {n_plain}) — every element below the drift "
        "point changes tree position on the rerun, which re-opens the "
        "duplicated-verdict-panel bug (examples/screenshots/double.png)"
    )
    # And the panel itself must exist exactly once on both runs.
    _find_expander(app)


def test_expander_collapsed_before_any_compute(app: AppTest) -> None:
    """Sanity: with no cached verdict yet, the panel is collapsed by
    default."""
    app.run()
    assert not app.exception, f"Unhandled exception: {app.exception}"
    assert _find_expander(app).proto.expanded is False


def test_expander_stays_statically_collapsed_after_compute(app: AppTest) -> None:
    """The server-sent `expanded=` value never changes — by design, see
    the module docstring. "Stays open" is entirely a client-side behavior
    this test layer cannot see (verified instead via a live standalone
    probe app). What IS still verifiable here: the compute actually ran
    inside the fragment and its results are present — the click didn't
    silently do nothing."""
    app.run()
    assert not app.exception, f"Unhandled exception: {app.exception}"

    compute_btn = next(b for b in app.button if b.label == "Compute verdict")
    compute_btn.click().run()
    assert not app.exception, f"Unhandled exception: {app.exception}"

    assert _find_expander(app).proto.expanded is False
    body = "\n".join(str(m.value) for m in app.markdown)
    assert "+15" in body


def test_control_row_does_not_contradict_freshly_computed_results(app: AppTest) -> None:
    """Real user report (2026-07-19): "when clicking on calculate the
    snippet duplicates." One real contributing bug (of several this panel
    went through — see the module docstring): the control row's caption
    (next to the Compute/Re-compute button) was decided from `has_fresh`
    BEFORE the compute block ran below it, so on the exact rerun that just
    computed a fresh verdict, it still read "Takes about 20-30 seconds" — an
    unanswered "ask" hint sitting directly above the freshly rendered
    headline/results in the SAME render.

    This test asserts the end state: neither a stale "not started" nor a
    false "done" claim renders in this slot once `trigger` has fired — the
    compute caption below and the results further down each tell their own
    true story without a third, conflicting one printed here."""
    app.run()
    compute_btn = next(b for b in app.button if b.label == "Compute verdict")
    compute_btn.click().run()
    assert not app.exception, f"Unhandled exception: {app.exception}"

    captions = [str(c.value) for c in app.caption]
    assert not any("Takes about 20-30 seconds" in c for c in captions), captions
    assert not any("Sweep complete" in c for c in captions), captions


def test_recompute_label_and_cache_persist_across_a_later_unrelated_rerun(app: AppTest) -> None:
    """Once a fresh verdict is cached for this character, server-side state
    that legitimately SHOULD persist (the cache itself, and the button
    relabeling to "Re-compute verdict") must survive a later, unrelated
    rerun — not just the one rerun immediately after the click. The
    expander's own open/closed state is no longer server-driven at all (see
    the module docstring), so it isn't asserted here."""
    app.run()
    compute_btn = next(b for b in app.button if b.label == "Compute verdict")
    compute_btn.click().run()
    assert not app.exception, f"Unhandled exception: {app.exception}"

    # A plain re-run with no further interaction, standing in for "the
    # user clicked something unrelated elsewhere in the app."
    app.run()
    assert not app.exception, f"Unhandled exception: {app.exception}"

    assert any(b.label == "Re-compute verdict" for b in app.button), (
        "cache didn't persist across a rerun"
    )
    body = "\n".join(str(m.value) for m in app.markdown)
    assert "+15" in body


def test_no_stale_progress_captions_remain_after_compute(app: AppTest) -> None:
    """Live report (examples/screenshots/elite-05-stale-sweeping-caption.png):
    the finished panel rendered "Sweeping +2 through +24…" directly above
    the completed headline, because the in-flight progress caption was never
    cleared once the sweep (and the skill ladder right after it) returned.
    Once results are on screen, neither progress caption may still be
    present — the panel must not read as still-computing."""
    app.run()
    compute_btn = next(b for b in app.button if b.label == "Compute verdict")
    compute_btn.click().run()
    assert not app.exception, f"Unhandled exception: {app.exception}"

    captions = [str(c.value) for c in app.caption]
    assert not any("Sweeping +2 through +24" in c for c in captions), captions
    assert not any("Computing skill ladder" in c for c in captions), captions
    # And the results really did land — this isn't just an empty panel.
    body = "\n".join(str(m.value) for m in app.markdown)
    assert "+15" in body
