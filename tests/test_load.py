"""Tests for `simf/ui/load.py`'s gear-cache staleness disclosure.

Online gear lookups (Raider.IO / Blizzard `/equipment`) had no display-facing
freshness signal — a visitor could be looking at a character snapshot from
hours ago with no way to tell. `_do_raider_io_load` now stamps a wall-clock
`_gear_fetched_at` + `_gear_source` on a successful online load, rendered as
a caption by `_render_run_config_strip`; any non-online load (`/simc` paste,
demo character) clears both so a fresher gear set never keeps the stale
caption attached.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
import yaml
from streamlit.testing.v1 import AppTest

from simf.core.character import Character
from simf.core.constants import DATA_DIR
from simf.ui.load import _format_elapsed

APP_PATH = Path(__file__).parent.parent / "src" / "simf" / "ui" / "app.py"


# ─── _format_elapsed: pure thresholds, no Streamlit needed ─────────────────


def test_format_elapsed_just_now_below_one_minute():
    assert _format_elapsed(0) == "just now"
    assert _format_elapsed(59) == "just now"


def test_format_elapsed_minutes():
    assert _format_elapsed(60) == "1m ago"
    assert _format_elapsed(179) == "2m ago"
    assert _format_elapsed(3599) == "59m ago"


def test_format_elapsed_hours():
    assert _format_elapsed(3600) == "1h ago"
    assert _format_elapsed(86399) == "23h ago"


def test_format_elapsed_days():
    assert _format_elapsed(86400) == "1d ago"
    assert _format_elapsed(3 * 86400) == "3d ago"


def test_format_elapsed_clamps_negative_to_just_now():
    """Clock-skew guard: a negative delta must never render as nonsense
    like '-1m ago'."""
    assert _format_elapsed(-5) == "just now"


# ─── AppTest integration: metadata set on load, cleared on a fresher one ───


@pytest.fixture
def app() -> AppTest:
    return AppTest.from_file(str(APP_PATH), default_timeout=30)


def _brutoh_char_data() -> dict:
    """Real, fully-valid Character fields (the same demo-character YAML
    `test_brutoh_demo_button_loads_character_without_error` proves renders
    cleanly end-to-end) — used here as a stand-in "online lookup result" so
    the fake fetch exercises the real render path, not just the session-
    state assignment."""
    with open(DATA_DIR / "characters" / "brutoh.yaml") as f:
        d = yaml.safe_load(f)
    return {k: v for k, v in d.items() if k in Character.__dataclass_fields__}


def _fill_lookup(app: AppTest, name: str = "Brutoh", realm: str = "Uldum") -> None:
    next(ti for ti in app.text_input if ti.key == "_rio_name").set_value(name).run()
    next(sb for sb in app.selectbox if sb.key == "_rio_realm").set_value(realm).run()
    next(b for b in app.button if b.label == "Look up my character").click().run()


class _FakeHydrateResult:
    def __init__(self, char_data: dict):
        self.char_data = char_data
        self.equipped: dict = {}
        self.summary = "Loaded Brutoh — 0 equipped."


def test_successful_online_lookup_stamps_fetched_at_and_source(app, monkeypatch):
    """A successful online lookup must stamp both a wall-clock fetch time
    and the source actually used, unconditionally (not public-mode-gated —
    the owner benefits from the staleness caption too)."""
    monkeypatch.delenv("SIMF_PUBLIC", raising=False)

    def _fake_fetch(name, realm, region, source):
        return _FakeHydrateResult(_brutoh_char_data()), "raiderio"

    monkeypatch.setattr("simf.io.gear_import.fetch_online_gear", _fake_fetch)
    before = time.time()
    app.run()
    _fill_lookup(app)
    after = time.time()

    assert "_gear_fetched_at" in app.session_state, "expected _gear_fetched_at to be stamped"
    fetched_at = app.session_state["_gear_fetched_at"]
    assert before <= fetched_at <= after
    assert app.session_state["_gear_source"] == "raiderio"

    captions = " ".join(getattr(c, "value", "") or "" for c in app.caption)
    assert "Gear fetched via raiderio" in captions
    assert "just now" in captions


def test_enter_in_name_field_searches_without_clicking_button(app, monkeypatch):
    """Pressing Enter in the Character name field must search on its own —
    QoL fix so a visitor doesn't have to reach for the button after typing
    (`_trigger_rio_fetch`, the name field's `on_change`). Realm is set first
    (its own selectbox, unrelated to the fix); the button is never clicked
    here — only the name field's commit should fire the same lookup
    `_fill_lookup`'s explicit `.click()` exercises elsewhere in this file."""
    monkeypatch.delenv("SIMF_PUBLIC", raising=False)

    def _fake_fetch(name, realm, region, source):
        return _FakeHydrateResult(_brutoh_char_data()), "raiderio"

    monkeypatch.setattr("simf.io.gear_import.fetch_online_gear", _fake_fetch)
    app.run()
    next(sb for sb in app.selectbox if sb.key == "_rio_realm").set_value("Uldum").run()
    next(ti for ti in app.text_input if ti.key == "_rio_name").set_value("Brutoh").run()

    assert not app.exception
    assert "_gear_fetched_at" in app.session_state, (
        "typing a name (no button click) must trigger the same lookup as clicking it"
    )
    assert app.session_state["_gear_source"] == "raiderio"


def test_simc_paste_clears_stale_gear_fetch_metadata(app, monkeypatch):
    """A /simc paste after an online lookup must drop the online-lookup
    staleness metadata — otherwise the fresher, more-complete gear set
    would still be captioned 'fetched via raiderio'."""
    from simf.ui.helpers.simc_load import SimcLoadOk

    monkeypatch.delenv("SIMF_PUBLIC", raising=False)
    app.session_state["_gear_fetched_at"] = time.time() - 120
    app.session_state["_gear_source"] = "raiderio"

    def _fake_load_from_simc(raw, **kwargs):
        return SimcLoadOk(
            char_data=_brutoh_char_data(),
            equipped={},
            bag_items={},
            vault_items={},
            summary="Loaded Brutoh — 0 equipped.",
        )

    monkeypatch.setattr("simf.ui.load.load_from_simc", _fake_load_from_simc)
    app.run()
    next(ta for ta in app.text_area if ta.key == "_simc_textarea").set_value("anything").run()
    next(b for b in app.button if b.label == "Load from /simc").click().run()

    assert "_gear_fetched_at" not in app.session_state
    assert "_gear_source" not in app.session_state


def test_demo_load_clears_stale_gear_fetch_metadata(app, monkeypatch):
    """Loading the sample build after a prior online lookup must also drop
    the staleness metadata — same reasoning as the /simc-paste case."""
    monkeypatch.delenv("SIMF_PUBLIC", raising=False)
    app.session_state["_gear_fetched_at"] = time.time() - 120
    app.session_state["_gear_source"] = "raiderio"
    app.run()

    demo_btn = next(b for b in app.button if "sample build" in (b.label or "").lower())
    demo_btn.click().run()

    assert "_gear_fetched_at" not in app.session_state
    assert "_gear_source" not in app.session_state


def test_change_character_button_clears_gear_fetch_metadata(app, monkeypatch):
    """The 'Change character' button's reset must also drop the staleness
    metadata — otherwise a freshly-cleared session could still show a
    leftover 'fetched via raiderio' caption with no character loaded.

    Seeds `char_data` directly (same pattern as the pre-existing
    `test_change_character_button_clears_state` in test_app_real_flows.py)
    rather than driving the online-lookup form first — going through that
    form leaves its selectbox widgets registered against a render tree
    that no longer exists once a character is loaded and the gear surface
    takes over, which AppTest's widget-state bookkeeping doesn't like on
    the subsequent rerun. Seeding session state directly exercises the
    same button handler without that unrelated AppTest wrinkle.
    """
    monkeypatch.delenv("SIMF_PUBLIC", raising=False)
    app.session_state["char_data"] = _brutoh_char_data()
    app.session_state["_gear_fetched_at"] = time.time()
    app.session_state["_gear_source"] = "raiderio"
    app.run()

    next(b for b in app.button if b.label == "Change character").click().run()
    assert "_gear_fetched_at" not in app.session_state
    assert "_gear_source" not in app.session_state
    assert "_gear_stats_estimated" not in app.session_state


# ─── _gear_from_log: Gear-tab provenance for a log-hydrated character ──────
# `_render_local_log_flow` (log_surface.py) stamps this on a COMBATANT_INFO
# auto-hydrate; a reader landing on the Gear tab afterwards previously saw
# item-id cards with no explanation why. Mirrors the `_gear_source`
# lifecycle tests above — any non-log load must clear it just as thoroughly.


def test_gear_from_log_caption_renders_when_set(app, monkeypatch):
    """`_render_run_config_strip` must render the log-provenance caption
    (log basename + the "ids not names" + "bag/vault empty" consequences)
    whenever `_gear_from_log` is set, regardless of which spec is loaded."""
    monkeypatch.delenv("SIMF_PUBLIC", raising=False)
    app.session_state["char_data"] = _brutoh_char_data()
    app.session_state["_gear_from_log"] = "WoWCombatLog-071225_193000.txt"
    app.run()

    captions = " ".join(str(c.value) for c in app.caption)
    assert "Gear read from your combat log" in captions
    assert "WoWCombatLog-071225_193000.txt" in captions
    assert "Change character" in captions


def test_gear_from_log_caption_absent_when_unset(app, monkeypatch):
    """No caption without the key — a normal /simc-paste or online-lookup
    load must never show a log-provenance disclaimer it doesn't need."""
    monkeypatch.delenv("SIMF_PUBLIC", raising=False)
    app.session_state["char_data"] = _brutoh_char_data()
    app.run()

    captions = " ".join(str(c.value) for c in app.caption)
    assert "Gear read from your combat log" not in captions


def test_simc_paste_clears_gear_from_log(app, monkeypatch):
    """A /simc paste after a log-hydrated character must drop the log
    provenance flag — otherwise the fresher paste-loaded gear (real names,
    real bags/vault) would still carry the "ids not names, empty bags"
    caption."""
    from simf.ui.helpers.simc_load import SimcLoadOk

    monkeypatch.delenv("SIMF_PUBLIC", raising=False)
    app.session_state["_gear_from_log"] = "WoWCombatLog-old.txt"

    def _fake_load_from_simc(raw, **kwargs):
        return SimcLoadOk(
            char_data=_brutoh_char_data(),
            equipped={},
            bag_items={},
            vault_items={},
            summary="Loaded Brutoh — 0 equipped.",
        )

    monkeypatch.setattr("simf.ui.load.load_from_simc", _fake_load_from_simc)
    app.run()
    next(ta for ta in app.text_area if ta.key == "_simc_textarea").set_value("anything").run()
    next(b for b in app.button if b.label == "Load from /simc").click().run()

    assert "_gear_from_log" not in app.session_state


def test_demo_load_clears_gear_from_log(app, monkeypatch):
    """Loading the sample build after a log-hydrated character must also
    drop the log-provenance flag — same reasoning as the /simc-paste case."""
    monkeypatch.delenv("SIMF_PUBLIC", raising=False)
    app.session_state["_gear_from_log"] = "WoWCombatLog-old.txt"
    app.run()

    demo_btn = next(b for b in app.button if "sample build" in (b.label or "").lower())
    demo_btn.click().run()

    assert "_gear_from_log" not in app.session_state


def test_change_character_button_clears_gear_from_log(app, monkeypatch):
    """The 'Change character' button's reset must also drop the
    log-provenance flag — a freshly-cleared session must never keep
    claiming a since-discarded character came from a log."""
    monkeypatch.delenv("SIMF_PUBLIC", raising=False)
    app.session_state["char_data"] = _brutoh_char_data()
    app.session_state["_gear_from_log"] = "WoWCombatLog-old.txt"
    app.run()

    next(b for b in app.button if b.label == "Change character").click().run()
    assert "_gear_from_log" not in app.session_state


# ─── _gear_stats_estimated: persistent caveat replaces the one-time toast ──
# Found 2026-07-10 chasing an implausible composition-line number (a ring
# swap's ΔeHP swung >5x, even flipped sign, between two fresh resolutions of
# the same /simc-paste-resolved character) — the resolver's own
# `stats_estimated` flag existed and was tested, but was only ever surfaced
# as a toast (`_surface_load_summary_toast`), long gone by the time a reader
# is looking at a swap card. `_gear_stats_estimated()` (ui/state.py) persists
# it for a caption on the Gear tab itself (test_gear_stats_estimated_caption.py).


def test_simc_paste_sets_gear_stats_estimated_true_when_resolver_used(app, monkeypatch):
    from simf.ui.helpers.simc_load import SimcLoadOk

    monkeypatch.delenv("SIMF_PUBLIC", raising=False)

    def _fake_load_from_simc(raw, **kwargs):
        return SimcLoadOk(
            char_data=_brutoh_char_data(),
            equipped={},
            bag_items={},
            vault_items={},
            summary="Loaded Brutoh — 0 equipped.",
            stats_estimated=True,
        )

    monkeypatch.setattr("simf.ui.load.load_from_simc", _fake_load_from_simc)
    app.run()
    next(ta for ta in app.text_area if ta.key == "_simc_textarea").set_value("anything").run()
    next(b for b in app.button if b.label == "Load from /simc").click().run()

    assert app.session_state["_gear_stats_estimated"] is True


def test_simc_paste_sets_gear_stats_estimated_false_when_export_has_gear_stats(app, monkeypatch):
    from simf.ui.helpers.simc_load import SimcLoadOk

    monkeypatch.delenv("SIMF_PUBLIC", raising=False)
    # A prior estimated load in this session must not leak into a fresher,
    # exact one — same staleness-clearing contract as _gear_fetched_at.
    app.session_state["_gear_stats_estimated"] = True

    def _fake_load_from_simc(raw, **kwargs):
        return SimcLoadOk(
            char_data=_brutoh_char_data(),
            equipped={},
            bag_items={},
            vault_items={},
            summary="Loaded Brutoh — 0 equipped.",
            stats_estimated=False,
        )

    monkeypatch.setattr("simf.ui.load.load_from_simc", _fake_load_from_simc)
    app.run()
    next(ta for ta in app.text_area if ta.key == "_simc_textarea").set_value("anything").run()
    next(b for b in app.button if b.label == "Load from /simc").click().run()

    assert app.session_state["_gear_stats_estimated"] is False


def test_demo_load_sets_gear_stats_estimated_false(app, monkeypatch):
    """Demo YAMLs are curated/exact — never estimated, even after a prior
    /simc paste in this session left the flag True."""
    monkeypatch.delenv("SIMF_PUBLIC", raising=False)
    app.session_state["_gear_stats_estimated"] = True
    app.run()

    demo_btn = next(b for b in app.button if "sample build" in (b.label or "").lower())
    demo_btn.click().run()

    assert app.session_state["_gear_stats_estimated"] is False


def test_online_lookup_via_raiderio_sets_gear_stats_estimated_true(app, monkeypatch):
    """Raider.IO has no exact-stats endpoint — its gear list is ALWAYS run
    through the same per-item resolver the /simc-paste fallback uses (see
    io/resolver_estimated_stats.py), so a load whose `used` source comes back
    "raiderio" carries the identical estimate-grade caveat and must flag it,
    same as the paste-resolver path. Found 2026-07-11: this used to be
    hardcoded False regardless of source, silently hiding the caveat from
    every Raider.IO visitor (the default path for cold public visitors)."""
    monkeypatch.delenv("SIMF_PUBLIC", raising=False)
    app.session_state["_gear_stats_estimated"] = False

    def _fake_fetch(name, realm, region, source):
        return _FakeHydrateResult(_brutoh_char_data()), "raiderio"

    monkeypatch.setattr("simf.io.gear_import.fetch_online_gear", _fake_fetch)
    app.run()
    _fill_lookup(app)

    assert app.session_state["_gear_stats_estimated"] is True


def test_online_lookup_via_blizzard_sets_gear_stats_estimated_false(app, monkeypatch):
    """The Blizzard `/statistics` endpoint returns exact aggregate character
    stats (not a resolver estimate) — a load whose `used` source comes back
    "blizzard" stays unflagged, even after a prior /simc paste in this
    session left the flag True."""
    monkeypatch.delenv("SIMF_PUBLIC", raising=False)
    app.session_state["_gear_stats_estimated"] = True

    def _fake_fetch(name, realm, region, source):
        return _FakeHydrateResult(_brutoh_char_data()), "blizzard"

    monkeypatch.setattr("simf.io.gear_import.fetch_online_gear", _fake_fetch)
    app.run()
    _fill_lookup(app)

    assert app.session_state["_gear_stats_estimated"] is False


# ─── Batch H follow-up (2026-07-08): trust-strip vs calibration badge ──────


def _popover_labels(app):
    """Chip/popover trigger labels — AppTest exposes a popover as a Block, its
    label only on the underlying proto (there is no `.label` accessor)."""
    return [p.proto.popover.label for p in app.get("popover")]


def test_run_config_strip_hides_model_error_for_non_calibrated_spec(app, monkeypatch):
    """A `characterized`/`placeholder`-tier spec must NOT show the Warrior-only
    ±X% number as its own. Post-F-001/F-002 redesign (review round R2,
    2026-07-17) the single calibration chip shows that spec's honest confidence
    tier + clause with NO number, and the details popover reframes the corpus
    figure as a reference — never "the number to trust" for this build (advisor
    catch: global_rmse is Warrior's corpus, no per-spec variant yet)."""
    monkeypatch.delenv("SIMF_PUBLIC", raising=False)
    d = _brutoh_char_data()
    d["class_spec"] = "blood_death_knight"  # characterized tier
    d["talents"] = "default-blood-dk"
    app.session_state["char_data"] = d
    app.run()

    labels = _popover_labels(app)
    assert any("Characterized" in lbl for lbl in labels), labels
    assert not any("off real logs" in lbl for lbl in labels), labels
    body = "\n".join(str(m.value) for m in app.markdown)
    assert "% model error" not in body
    # Popover reframes the corpus number as a reference for this non-corpus
    # spec, never presenting it as authoritative for this build.
    assert "number to trust" not in body
    assert "one calibrated corpus" in body


def test_run_config_strip_shows_characterized_for_corpus_spec(app, monkeypatch):
    """Regression guard: the CORPUS spec (Prot Warrior — `CALIBRATION_CORPUS_SPEC`)
    tier history: `calibrated` → `characterized` 2026-07-18 (Demo Shout/Phalanx
    replay double-count fix, PR #387) → RE-PROMOTED to `calibrated` 2026-07-22
    once the LOO-CV gate (newly wired into `simf calibrate-k`, PR #416) passed
    against the ratified corpus with Vanguard live → DOWNGRADED again
    2026-07-25 when that same bar failed against 15 independent players (mean
    bias +11.0% vs the ≤5% bar) — see
    docs/validation/protwarrior_calibrated_downgrade_2026_07_25.md. The single
    calibration chip (F-001/F-002, R2 2026-07-17) must read the LIVE tier — with
    the corpus spec back at `characterized`, it must NOT get special-cased
    "off real logs"/precision-number treatment just because it's the corpus
    spec, matching how any other `characterized` spec renders (covered
    generically by test_run_config_strip_hides_model_error_for_non_calibrated_spec
    above, using a different spec). This test exists specifically to catch a
    "corpus spec always shows calibrated" special case regressing back in.
    The tier-to-label mechanism itself stays covered at the unit level
    (test_run_config_strip.py::test_chip_label_corpus_spec_carries_the_number
    and ::test_chip_label_non_calibrated_tiers_show_confidence_only)
    independent of which real spec (if any) currently holds that tier."""
    monkeypatch.delenv("SIMF_PUBLIC", raising=False)
    app.session_state["char_data"] = _brutoh_char_data()  # protection_warrior
    app.run()

    labels = _popover_labels(app)
    assert any("Characterized" in lbl for lbl in labels), labels
    assert not any("off real logs" in lbl for lbl in labels), labels


def test_log_surface_no_character_never_shows_model_error(app, monkeypatch):
    """The Why-did-I-die surface with NO character loaded is the one state
    where nothing on screen is sim-derived (the Cooldown Planner, the only
    panel that would run a forward simulation, needs a loaded character and
    hard-returns without one) — showing the Warrior-corpus ±X% forward-sim
    RMSE here was a real honesty gap (dual-reviewed 2026-07-26,
    calibration-scientist + copy-microcopy-editor, closing a Season 2
    punch-list item). The strip must say so plainly instead, and must NOT
    fall back to the generic "Uncalibrated build" text either (that reads
    as a whole-app trust complaint, not what's true here)."""
    monkeypatch.delenv("SIMF_PUBLIC", raising=False)
    app.session_state["view"] = "log"
    app.run()
    assert not app.exception, f"Unhandled exception: {app.exception}"

    body = "\n".join(str(m.value) for m in app.markdown)
    assert "% model error" not in body
    assert "Uncalibrated build" not in body
    assert "Read straight from your log" in body

    labels = _popover_labels(app)
    assert any("Calibration details" in lbl for lbl in labels), labels


# ─── Batch E — novice on-ramp ───────────────────────────────────────────────


def test_cold_landing_has_no_addon_reassurance_and_skip_hint(app):
    """Items 1+3 — an up-front reassurance sentence ahead of both load
    paths, and an explicit pointer to the character-free 'Why did I die?'
    surface, must both render on the cold landing page."""
    app.run()
    captions = [getattr(c, "value", "") or "" for c in app.caption]
    assert any("No addons, no combat logs" in c for c in captions)
    assert any("Why did I die?" in c and "died last pull" in c for c in captions)


def test_spec_comparison_expander_lists_all_six_specs_with_live_tier(app):
    """Items 2+4 — the spec-comparison expander must cover every tank spec
    with a feel description + complexity, and the model-confidence value
    must be the LIVE `calibration_tier` from constants.yaml, not a
    hardcoded copy that could silently go stale."""
    from simf.core.constants import load_constants
    from simf.io.spec_ids import class_spec_display_name
    from simf.ui.load import _SPEC_FEEL

    app.run()
    exp = next(e for e in app.expander if "Compare all 6" in e.label)
    exp.expanded = True
    app.run()

    md_texts = " ".join(getattr(m, "value", "") or "" for m in app.markdown)
    specs_cfg = load_constants().get("specs", {})
    for class_spec in _SPEC_FEEL:
        display = class_spec_display_name(class_spec)
        assert display in md_texts, f"{display} missing from spec comparison"
        tier = specs_cfg[class_spec]["calibration_tier"]
        assert f"model confidence: {tier}" in md_texts, (
            f"{display}'s live calibration_tier ({tier}) not reflected in the comparison"
        )


def test_demo_button_loads_sample_build_as_single_escape_hatch(app):
    """The demo entry is a single 'Load sample build' button — no preview
    teaser sits ahead of it. Regression guard for the removed '👀 Preview a
    real example' button + its ~10-20s teaser sweep."""
    app.run()
    labels = [b.label for b in app.button]
    assert "Load sample build" in labels
    assert not any("Preview a real example" in label for label in labels)

    btn = next(b for b in app.button if b.label == "Load sample build")
    btn.click().run()
    assert not app.exception
    assert "char_data" in app.session_state


# ─── novice_tank review (2026-07-09): scroll-to-top on a load transition ───


def test_flag_scroll_to_top_sets_then_helper_pops_and_scrolls_once():
    """Unit-level contract for the flag/consumer pair: `_flag_scroll_to_top`
    arms a one-shot session flag, and `_scroll_to_top_if_requested` pops
    (reads-and-clears) it and emits the scroll script exactly once. A
    second call with no fresh flag must NOT re-emit the script — otherwise
    a plain widget-only rerun (no new load) would keep re-scrolling the
    visitor to the top on every interaction.

    ALSO asserts the stable-slot contract (2026-07-19, PR #397): the helper
    must create its `st.empty()` placeholder on EVERY call, flag or no flag
    — never zero-or-one elements. The original zero-or-one version shifted
    every element on the page by one tree position on the first rerun after
    a load, which (combined with the key-level verdict's 8-30s inline
    sweep) rendered the whole verdict panel twice — the real root cause of
    examples/screenshots/double.png. See the helper's docstring."""
    from simf.ui import load as load_mod

    session: dict = {}
    original_ss = load_mod._ss
    html_calls: list = []
    empty_calls: list = []

    class _FakeSlot:
        def html(self, body, **kw):
            html_calls.append(body)

    def _fake_empty():
        empty_calls.append(True)
        return _FakeSlot()

    load_mod._ss = lambda: session  # type: ignore[assignment]
    original_empty = load_mod.st.empty
    load_mod.st.empty = _fake_empty  # type: ignore[assignment]
    try:
        load_mod._flag_scroll_to_top()
        assert session.get("_scroll_to_top_on_next_render") is True

        load_mod._scroll_to_top_if_requested()
        assert html_calls == [load_mod._SCROLL_TO_TOP_JS]
        assert "_scroll_to_top_on_next_render" not in session
        assert len(empty_calls) == 1

        # No fresh flag — must not re-scroll, but MUST still occupy the
        # stable slot (one st.empty per call, unconditionally).
        load_mod._scroll_to_top_if_requested()
        assert html_calls == [load_mod._SCROLL_TO_TOP_JS]
        assert len(empty_calls) == 2
    finally:
        load_mod._ss = original_ss  # type: ignore[assignment]
        load_mod.st.empty = original_empty  # type: ignore[assignment]


def _scroll_script_count(app) -> int:
    """How many rendered elements carry the scroll-to-top script this run.

    Inspects the element tree rather than monkeypatching `st.html`: since
    the stable-slot fix (2026-07-19, PR #397) the script is written via
    `st.empty().html(...)`, a bound method on the slot's DeltaGenerator —
    a module-level `st.html` patch never sees that call. `scrollTo(0, 0)`
    is unique to `_SCROLL_TO_TOP_JS` (the only other `st.html` script on
    the page, the Wowhead tooltip loader, never scrolls)."""
    return sum(
        1 for el in app.get("html") if "scrollTo(0, 0)" in str(getattr(el.proto, "body", ""))
    )


def test_demo_load_triggers_scroll_to_top(app, monkeypatch):
    """Integration: clicking 'Load sample build' must arm the flag consumed
    by `main()`'s next render, resulting in exactly one scroll-to-top
    script injection and a cleared flag once the app settles. This is the
    actual fix for the reported bug — a demo load used to leave the
    browser at its previous scroll offset, so a novice's first paint after
    clicking was a mid-page card with no wordmark, tabs, or 'Change
    character' button, reading as 'the click did nothing.'"""
    monkeypatch.delenv("SIMF_PUBLIC", raising=False)

    app.run()
    # Cold landing render must not itself scroll — only a load transition does.
    assert _scroll_script_count(app) == 0

    demo_btn = next(b for b in app.button if "sample build" in (b.label or "").lower())
    demo_btn.click().run()

    assert not app.exception
    assert _scroll_script_count(app) == 1, (
        "expected exactly one scroll-to-top injection after the demo load"
    )
    assert "_scroll_to_top_on_next_render" not in app.session_state
    # The no-reflag-on-a-plain-rerun contract is covered at the unit level
    # above (`test_flag_scroll_to_top_sets_then_helper_pops_and_scrolls_once`)
    # — a second `app.run()` here would hit AppTest's known stale-widget
    # KeyError once the landing form's widgets are gone (see
    # `test_change_character_button_clears_gear_fetch_metadata`'s docstring
    # for the same wrinkle).


def test_change_character_button_triggers_scroll_to_top(app, monkeypatch):
    """The reverse transition (gear surface → landing form) is the same
    kind of full page-content swap and must scroll to top too."""
    monkeypatch.delenv("SIMF_PUBLIC", raising=False)

    app.session_state["char_data"] = _brutoh_char_data()
    app.run()
    assert _scroll_script_count(app) == 0

    next(b for b in app.button if b.label == "Change character").click().run()

    assert not app.exception
    assert _scroll_script_count(app) == 1
    assert "_scroll_to_top_on_next_render" not in app.session_state


def test_simc_textarea_capped_in_public_mode(app, monkeypatch):
    """2026-08-11 ship-readiness fix: the /simc textarea had no size limit,
    so a giant public-mode paste paid the full parse_simc_string cost before
    `_PUBLIC_MAX_EXTRA_ITEMS` (which only trims the ALREADY-PARSED bag+vault
    list) ever got a chance to bound anything. `max_chars` is enforced by
    Streamlit's frontend before a paste can even reach `_do_simc_load`."""
    from simf.ui.load import _PUBLIC_MAX_SIMC_CHARS

    monkeypatch.setenv("SIMF_PUBLIC", "1")
    app.run()
    assert not app.exception
    textarea = next(ta for ta in app.text_area if ta.key == "_simc_textarea")
    assert textarea.max_chars == _PUBLIC_MAX_SIMC_CHARS


def test_simc_textarea_uncapped_outside_public_mode(app, monkeypatch):
    """The owner's own (non-public) session keeps pasting a real export
    frictionless — the cap only exists to bound an anonymous public paste."""
    monkeypatch.delenv("SIMF_PUBLIC", raising=False)
    app.run()
    assert not app.exception
    textarea = next(ta for ta in app.text_area if ta.key == "_simc_textarea")
    assert not textarea.max_chars


def test_public_simc_cap_generous_vs_every_real_example_export():
    """The cap must never clip a real character's export — regression guard
    against picking too tight a number. Checks every /simc-shaped file in
    examples/ (equipped + bags + vault is the largest realistic paste)."""
    from simf.ui.load import _PUBLIC_MAX_SIMC_CHARS

    examples_dir = Path(__file__).parent.parent / "examples"
    simc_like = list(examples_dir.glob("*.simc")) + list(examples_dir.glob("*vault*.txt"))
    assert simc_like, "expected at least one real example export to check against"
    for f in simc_like:
        size = len(f.read_text())
        assert size < _PUBLIC_MAX_SIMC_CHARS, (
            f"{f.name} is {size} chars, at or above the public cap "
            f"{_PUBLIC_MAX_SIMC_CHARS} — the cap is too tight"
        )


# ─── usage-analytics: character_loaded event per load method (2026-08-16) ──


def test_demo_load_records_character_loaded_event(app, monkeypatch, tmp_path):
    hits_path = tmp_path / "hits.jsonl"
    monkeypatch.delenv("SIMF_PUBLIC", raising=False)
    monkeypatch.setenv("SIMF_SHARE_HITS_PATH", str(hits_path))
    app.run()
    demo_btn = next(b for b in app.button if "sample build" in (b.label or "").lower())
    demo_btn.click().run()

    entries = [json.loads(line) for line in hits_path.read_text().splitlines()]
    matches = [e for e in entries if e.get("event") == "character_loaded"]
    assert len(matches) == 1
    assert matches[0]["load_method"] == "demo"
    assert "sid" in matches[0]


def test_simc_paste_records_character_loaded_event(app, monkeypatch, tmp_path):
    from simf.ui.helpers.simc_load import SimcLoadOk

    hits_path = tmp_path / "hits.jsonl"
    monkeypatch.delenv("SIMF_PUBLIC", raising=False)
    monkeypatch.setenv("SIMF_SHARE_HITS_PATH", str(hits_path))

    def _fake_load_from_simc(raw, **kwargs):
        return SimcLoadOk(
            char_data=_brutoh_char_data(),
            equipped={},
            bag_items={},
            vault_items={},
            summary="Loaded Brutoh — 0 equipped.",
        )

    monkeypatch.setattr("simf.ui.load.load_from_simc", _fake_load_from_simc)
    app.run()
    next(ta for ta in app.text_area if ta.key == "_simc_textarea").set_value("anything").run()
    next(b for b in app.button if b.label == "Load from /simc").click().run()

    entries = [json.loads(line) for line in hits_path.read_text().splitlines()]
    matches = [e for e in entries if e.get("event") == "character_loaded"]
    assert len(matches) == 1
    assert matches[0]["load_method"] == "simc"


def test_online_lookup_records_character_loaded_event_with_source(app, monkeypatch, tmp_path):
    hits_path = tmp_path / "hits.jsonl"
    monkeypatch.delenv("SIMF_PUBLIC", raising=False)
    monkeypatch.setenv("SIMF_SHARE_HITS_PATH", str(hits_path))

    def _fake_fetch(name, realm, region, source):
        return _FakeHydrateResult(_brutoh_char_data()), "raiderio"

    monkeypatch.setattr("simf.io.gear_import.fetch_online_gear", _fake_fetch)
    app.run()
    _fill_lookup(app)

    entries = [json.loads(line) for line in hits_path.read_text().splitlines()]
    matches = [e for e in entries if e.get("event") == "character_loaded"]
    assert len(matches) == 1
    assert matches[0]["load_method"] == "online_raiderio"
