"""End-to-end smoke tests of the actual user flows.

Not pure unit tests — these load real Brutoh data, click the demo button,
and assert what the user actually sees. Catches the class of bug where the
unit-test stubs hid a misuse of the real API.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).parent.parent / "src" / "simf" / "ui" / "app.py"
EXAMPLES = Path(__file__).parent.parent / "examples"


@pytest.fixture
def app() -> AppTest:
    # Bumped to 30s after the Gear-tab recommender landed — cold-cache
    # _per_slot_picks scans ~30 items on first paint. Production reruns
    # hit session-cache and finish in <1s.
    return AppTest.from_file(str(APP_PATH), default_timeout=30)


def test_brutoh_demo_button_loads_character_without_error(app):
    """Clicking the demo button must populate char_data and not error.

    The label has evolved ('Load Brutoh demo' → 'Try a demo character' →
    'Load sample build' on 2026-06-14 when the demo was demoted to a fallback).
    Requirements unchanged:
      - char_data has the calibrated Brutoh stats from brutoh.yaml.
      - Equipped/bag/vault items are parsed from the bundled SimC file.
      - No st.error shown.
    """
    app.run()
    demo_btn = next((b for b in app.button if "sample build" in (b.label or "").lower()), None)
    assert demo_btn is not None, "Demo button not found on first paint"

    demo_btn.click().run()
    assert not app.exception, f"Exception during demo load: {app.exception}"

    errors = [str(e.value) for e in app.error]
    assert not errors, f"Demo load surfaced error(s): {errors}"

    # char_data should be populated with the Brutoh stats from yaml.
    # AppTest's session_state wrapper doesn't support `.get()` — use mapping access.
    assert "char_data" in app.session_state, "char_data not set after demo click"
    cd = app.session_state["char_data"]
    assert cd["name"] == "Brutoh"
    assert cd["stamina"] > 0
    assert cd["armor_from_gear"] > 0
    assert cd["haste_rating"] > 0

    # SafeSessionState (AppTest) doesn't support .get(), so use membership + bracket.
    equipped = app.session_state["simc_equipped"] if "simc_equipped" in app.session_state else {}  # noqa: SIM401
    assert any(getattr(it, "item_id", 0) for it in equipped.values()), (
        "No equipped items parsed from Brutoh SimC"
    )

    vault = app.session_state["simc_vault_items"] if "simc_vault_items" in app.session_state else {}  # noqa: SIM401
    assert sum(len(v) for v in vault.values()) > 0, (
        "Brutoh demo has vault items in SimC but none were loaded"
    )


def test_brutoh_demo_then_vault_panel_renders_real_verdict(app):
    """After loading Brutoh, the vault panel should show a verdict card
    that names a slot — not the fallback 'no upgrade' string."""
    app.run()
    demo_btn = next((b for b in app.button if "sample build" in (b.label or "").lower()), None)
    demo_btn.click().run()
    assert not app.exception

    # The verdict-card markdown should appear
    body = "\n".join(str(m.value) for m in app.markdown)
    has_take = "Take the" in body
    has_no_upgrade = "No upgrade" in body
    # One of two acceptable outcomes — but BOTH at once is the bug (contradictory).
    assert not (has_take and has_no_upgrade), (
        "Verdict card contradicts itself — says 'Take the X' AND 'No upgrade'. "
        "This is the visual bug the user reported."
    )


def test_advanced_toggle_persists_across_rerun(app):
    """Flipping Advanced ON then triggering any other interaction must not
    lose the toggle state. Gated behind `_ADVANCED_HAS_CONTENT` — when no
    Advanced surface is shipped, the toggle is hidden (ui-critic #9,
    2026-05-16) and there is nothing to persist."""
    from simf.ui import app as app_module

    if not app_module._ADVANCED_HAS_CONTENT:
        pytest.skip("Advanced toggle gated off until an Advanced surface ships.")
    app.run()
    toggle = next((t for t in app.toggle if t.label == "Advanced"), None)
    assert toggle is not None
    toggle.set_value(True).run()
    # Find any other button (e.g. header buttons), click it to force rerun
    rerun_target = next(
        (b for b in app.button if b.key == "nav_gear" or b.label == "Why did I die?"),
        None,
    )
    if rerun_target:
        rerun_target.click().run()
    # Toggle must still be on
    toggle_after = next((t for t in app.toggle if t.label == "Advanced"), None)
    assert toggle_after.value is True, (
        "Advanced toggle lost its state after a rerun — widget binding is wrong."
    )


def test_run_config_strip_reads_calibration_load_from_constants(app):
    """The trust banner must reflect a real calibration load, not a
    placeholder. The strip text speaks to a tank — a single
    signed-percent error number — not the magic K constant or a raw
    RMSE float. (The strip is suppressed on the cold load form — see
    test_cold_landing_hides_run_config_strip_until_loaded — so load the
    demo first to render it.)"""
    app.run()
    next(b for b in app.button if "sample build" in (b.label or "").lower()).click().run()
    labels = [p.proto.popover.label for p in app.get("popover")]
    body = "\n".join(str(m.value) for m in app.markdown)
    # Post-F-001/F-002 (R2 2026-07-17): the calibration chip carries the
    # confidence tier in its label. The demo (Prot Warrior) was `calibrated`
    # until 2026-07-18 (downgraded to `characterized` — Demo Shout/Phalanx
    # replay double-count fix, PR #387), RE-PROMOTED to `calibrated`
    # 2026-07-22 once the LOO-CV gate (PR #416) passed against the ratified
    # corpus with Vanguard live, then DOWNGRADED again 2026-07-25 when that
    # same bar failed against 15 independent players (mean bias +11.0% vs
    # the ≤5% bar) — see
    # docs/validation/protwarrior_calibrated_downgrade_2026_07_25.md. Renders
    # a "Characterized" chip; an EMPTY banner or a leaked "K=2700" would
    # still be a regression.
    assert any("Characterized" in lbl for lbl in labels), (
        f"Calibration chip missing its confidence tier. Popover labels: {labels}"
    )
    # Dev jargon must never leak — the previous shape had `K=2700 · RMSE
    # 0.079 · sha 307feb6` which the user couldn't decode.
    assert "K=" not in body[:400]
    assert "RMSE " not in body[:400]
    # Log count is provenance, not signal — popover only, never the strip.
    # (Search a tight window so the assertion doesn't false-positive on
    # other "logs" mentions elsewhere on the page.)
    assert "Calibrated on" not in body[:400]


def test_log_surface_link_navigates_from_gear(app):
    """Clicking 'Why did I die?' from the gear surface should switch view."""
    # Pre-populate so we're past the empty state
    app.session_state["char_data"] = {
        "name": "X",
        "race": "human",
        "class_spec": "protection_warrior",
        "talents": "kiratank-defensive",
        "strength": 1000,
        "stamina": 30000,
        "armor_from_gear": 5000,
        "haste_rating": 2000,
        "crit_rating": 1000,
        "mastery_rating": 1500,
        "versatility_rating": 300,
    }
    app.run()
    log_btn = next((b for b in app.button if b.label == "Why did I die?"), None)
    assert log_btn is not None
    log_btn.click().run()
    assert app.session_state["view"] == "log"
    assert not app.exception


def test_log_surface_renders_log_picker_when_logs_exist(app):
    """With char_data + view=log, the log picker should render — or, if no
    `WoWCombatLog-*.txt` files exist (CI / clean clone), an info banner
    prompting the user to upload one. Either is a valid steady state."""
    app.session_state["char_data"] = {
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
    }
    app.session_state["view"] = "log"
    app.run()
    assert not app.exception
    has_log_picker = any("Log file" in (s.label or "") for s in app.selectbox)
    has_no_logs_banner = any("no combat logs uploaded" in str(i.value).lower() for i in app.info)
    assert has_log_picker or has_no_logs_banner


def test_change_character_button_clears_state(app):
    """The inline 'Change character' button (in the run-config strip)
    must drop char_data and gear state so the user is sent back to the
    SimC paste form. Was a sidebar button in v0.10.6 and earlier; moved
    inline in v0.10.7 when the sidebar was removed entirely."""
    app.session_state["char_data"] = {
        "name": "X",
        "race": "human",
        "class_spec": "protection_warrior",
        "talents": "kiratank-defensive",
        "strength": 1000,
        "stamina": 30000,
        "armor_from_gear": 5000,
        "haste_rating": 2000,
        "crit_rating": 1000,
        "mastery_rating": 1500,
        "versatility_rating": 300,
    }
    app.session_state["simc_equipped"] = {"head": object()}
    app.run()
    change_btn = next(
        (b for b in app.button if "Change character" in (b.label or "")),
        None,
    )
    assert change_btn is not None, (
        "Change-character button not found — it lives inline in the "
        "run-config strip now (the old sidebar home was removed)."
    )
    change_btn.click().run()
    assert "char_data" not in app.session_state
    assert "simc_equipped" not in app.session_state
