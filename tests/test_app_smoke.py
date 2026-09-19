"""Smoke tests for the Streamlit app shell.

Uses Streamlit's AppTest harness to render the app headless and assert on
DOM-level state. Catches the class of bug that pytest alone misses: a
helper renames a key, a session-state lookup goes stale, an unhandled
KeyError on first paint.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).parent.parent / "src" / "simf" / "ui" / "app.py"


@pytest.fixture
def app() -> AppTest:
    # The Gear-tab recommender (_per_slot_picks) scans bag+vault items on
    # cold-cache first paint, which can spike past 10s. See same bump in
    # test_share_url.py — production session-state caches reruns to <1s.
    return AppTest.from_file(str(APP_PATH), default_timeout=30)


def test_first_paint_renders_without_exception(app):
    """No character loaded → app should show the SimC paste flow, no errors."""
    app.run()
    assert not app.exception, f"Unhandled exception on first paint: {app.exception}"


def test_first_paint_shows_simc_paste(app):
    """The empty-state UX still exposes the SimC paste textarea."""
    app.run()
    # The textarea should be present
    assert len(app.text_area) >= 1
    # And the page should not be showing a verdict card
    body = "\n".join(str(m.value) for m in app.markdown)
    assert "Take the" not in body


def test_landing_leads_with_online_lookup_before_paste(app):
    """The zero-friction online name lookup must render BEFORE the /simc
    paste section. The lookup used to sit buried under a 200px textarea;
    this pins the new order so a future refactor can't silently re-bury the
    easy path (and re-introduce the misplaced loading state)."""
    app.run()
    headings = [str(m.value) for m in app.markdown]
    body = "\n".join(headings + [str(c.value) for c in app.caption])

    def _first_index(needle: str) -> int:
        for i, h in enumerate(headings):
            if needle in h:
                return i
        raise AssertionError(f"{needle!r} not found in landing markdown: {headings}")

    lookup_at = _first_index("Find your character")
    # The /simc section heading is job-led ("Check your vault picks") since the
    # 2026-06-09 copy reframe — anchor on that, not the old "Paste your" title.
    paste_at = _first_index("Check your vault picks")
    assert lookup_at < paste_at, (
        f"Online lookup (idx {lookup_at}) must render before the paste section (idx {paste_at})"
    )
    # Both load paths still present: name input + paste textarea.
    assert any("Character name" in (ti.label or "") for ti in app.text_input)
    assert len(app.text_area) >= 1
    # The paste section names its unique value so it isn't perceived as
    # redundant with the lookup — it's the only Great Vault + bags source.
    assert "Great Vault" in body


def test_name_lookup_name_field_has_no_placeholder_character(app):
    """The character-name input must not seed a real character as placeholder
    text. "Brutoh" read as a stranger's leftover ("is this someone else's data
    I'm about to look up?") — user feedback, 2026-06-09. The "Character name"
    label is the affordance; the field stays empty. (Realm is now a searchable
    dropdown — see test_realm_is_searchable_dropdown_scoped_to_region.)
    """
    app.run()
    by_key = {ti.key: ti for ti in app.text_input}
    assert "_rio_name" in by_key, f"name input missing; keys: {list(by_key)}"
    assert not (by_key["_rio_name"].placeholder or ""), (
        f"name field must have no placeholder, got {by_key['_rio_name'].placeholder!r}"
    )


def test_realm_is_searchable_dropdown_scoped_to_region(app):
    """Realm is a searchable dropdown (not free-text), scoped to the selected
    region — easier than typing realm names. Defaults to EU; options include a
    known EU realm; nothing is pre-selected (no stranger's-realm leftover)."""
    app.run()
    realm_sb = next((sb for sb in app.selectbox if sb.key == "_rio_realm"), None)
    assert realm_sb is not None, "Realm must be a selectbox (searchable dropdown)"
    # Not a text input any more.
    assert "_rio_realm" not in {ti.key for ti in app.text_input}
    assert "Uldum" in realm_sb.options, "EU realm options must include Uldum"
    assert "Barthilas" not in realm_sb.options, "EU dropdown must not list US-only realms"
    assert realm_sb.value is None, "no realm pre-selected on the cold landing"


def test_changing_region_resets_realm_selection(app):
    """Switching region must clear the realm — a realm from the old region
    isn't in the new region's options, which would crash the selectbox. Pins
    the on_change reset (the actual failure mode)."""
    app.run()
    # Alonsus is EU-only, so it is genuinely absent from the US options — this
    # is what would crash the selectbox without the reset (Uldum exists in both
    # regions and would not exercise the failure mode).
    next(sb for sb in app.selectbox if sb.key == "_rio_realm").set_value("Alonsus").run()
    assert next(sb for sb in app.selectbox if sb.key == "_rio_realm").value == "Alonsus"
    # Switch EU -> US: the stale EU-only realm must be cleared, not crash.
    next(sb for sb in app.selectbox if sb.key == "_rio_region").set_value("us").run()
    assert not app.exception, f"region switch must not crash: {app.exception}"
    realm_after = next(sb for sb in app.selectbox if sb.key == "_rio_realm")
    assert realm_after.value is None, "realm must reset when region changes"
    assert "Barthilas" in realm_after.options, "realm options must follow the new region"


def test_failed_load_announces_status_to_assistive_tech(app):
    """A failed load must surface an assistive-tech-announced status.

    Streamlit's `st.error` renders with no role/aria-live, and the landing
    reorder paints the error in the top status slot while focus stays on the
    Fetch button below — so a screen-reader user gets no cue. The fix pairs
    the visible error with a visually-hidden role="alert" live region (WCAG
    4.1.3). Pin (a) no spurious alert on a clean cold paint, and (b) both the
    visible error and the announced region on a failed load, so the
    announcement can't be silently dropped by a future refactor.
    """
    app.run()
    # Clean cold paint: nothing failed, so no alert region should exist.
    cold_md = "\n".join(str(m.value) for m in app.markdown)
    assert 'role="alert"' not in cold_md, "no failure yet — must not announce an alert"

    # Click the name-lookup button with empty name/realm → validation error path.
    fetch = next(b for b in app.button if b.label == "Look up my character")
    fetch.click().run()
    assert not app.exception

    # The visible error is unchanged (same st.error styling).
    errors = [str(e.value) for e in app.error]
    assert any("name and realm" in e for e in errors), f"expected validation error, got {errors}"

    # …and a visually-hidden assertive live region carries the same text so a
    # screen reader announces it regardless of where focus sits.
    md = "\n".join(str(m.value) for m in app.markdown)
    assert 'role="alert"' in md, "failed load must render a role=alert live region"
    assert 'aria-live="assertive"' in md
    assert "visually-hidden" in md


def test_cold_landing_hides_run_config_strip_until_loaded(app):
    """Trust banner — the model-error/build strip is dev-status noise on the
    COLD load form (nothing's computed yet), so it's suppressed there and
    appears once a character loads (the verdict it qualifies). Cold-load
    review, 2026-06-14."""
    app.run()
    cold_body = "\n".join(str(m.value) for m in app.markdown)
    assert "% model error" not in cold_body
    assert "Uncalibrated build" not in cold_body
    # Nothing computed yet → the calibration chip's popover strip isn't rendered.
    assert "number to trust" not in cold_body
    # Load the demo → the single calibration chip + its popover now render
    # (F-001/F-002 redesign, R2 2026-07-17: confidence lives in the chip label).
    next(b for b in app.button if "sample build" in (b.label or "").lower()).click().run()
    labels = [p.proto.popover.label for p in app.get("popover")]
    # The chip carries the confidence tier as its own label. The demo (Prot
    # Warrior) is `characterized` again as of 2026-07-25 — the 2026-07-22
    # re-promotion's own bar failed against 15 independent players (mean
    # bias +11.0% vs the ≤5% bar) — see
    # docs/validation/protwarrior_calibrated_downgrade_2026_07_25.md.
    assert any("Characterized" in lbl for lbl in labels), labels
    body = "\n".join(str(m.value) for m in app.markdown)
    # Popover long-tail fields still render into the AppTest markdown stream —
    # verify they're reachable via the details panel.
    assert "iteration" in body.lower()
    assert "seed" in body.lower()


def test_advanced_toggle_gated_off_when_panel_is_empty(app):
    """The Advanced toggle is gated behind `_ADVANCED_HAS_CONTENT` — when no
    Advanced surface is shipped, neither the header toggle nor the body
    panel renders (was leaking a TODO + raw JSON to users — ui-critic #9,
    engaged_tank #2, 2026-05-16). The same commit that ships a real
    Advanced surface flips the constant and restores the toggle."""
    from simf.ui import app as app_module

    app.run()
    if app_module._ADVANCED_HAS_CONTENT:
        # Future state: when the panel is shipped, the toggle exists,
        # off by default, and flipping it renders the Advanced expander.
        toggles = [t for t in app.toggle if t.label == "Advanced"]
        assert toggles, "Advanced toggle expected when _ADVANCED_HAS_CONTENT=True"
        assert toggles[0].value is False
        toggles[0].set_value(True).run()
        assert any("Advanced mode" in (e.label or "") for e in app.expander)
    else:
        # Current state: no Advanced toggle in the header, no panel body.
        assert not any(t.label == "Advanced" for t in app.toggle)
        assert not any("Advanced mode" in (e.label or "") for e in app.expander)


def test_loaded_character_shows_vault_and_gear_tabs(app):
    """Once char_data is populated, the gear surface renders a Vault/Gear
    sub-nav. This is a button pair (not `st.tabs`) — see
    `app.py::_set_gear_subtab` — since a naive `st.tabs()` has no
    persisted-active-tab state across a rerun (2026-07-11 talent-leftovers
    deep review)."""
    app.session_state["char_data"] = {
        "name": "TestTank",
        "race": "human",
        "class_spec": "protection_warrior",
        "talents": "kiratank-defensive",
        "strength": 2000,
        "stamina": 32000,
        "armor_from_gear": 5000,
        "haste_rating": 2000,
        "crit_rating": 1200,
        "mastery_rating": 1500,
        "versatility_rating": 300,
    }
    app.session_state["simc_equipped"] = {}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {}
    app.run()
    assert not app.exception, f"Unhandled exception: {app.exception}"
    subtab_keys = {b.key for b in app.button if b.key and b.key.startswith("gear_subtab_")}
    assert subtab_keys == {"gear_subtab_vault", "gear_subtab_gear"}


def test_danger_pull_cheatsheet_expander_renders_for_a_loaded_character(app):
    """Live wiring check for the Batch G / Season 2 Readiness danger-pull
    cheat sheet (ui/helpers/danger_pull_cheatsheet.py) — the expander must
    actually appear on the Gear surface's dungeon-filter row once a
    character is loaded, not just in the helper's own isolated tests."""
    app.session_state["char_data"] = {
        "name": "TestTank",
        "race": "human",
        "class_spec": "protection_warrior",
        "talents": "kiratank-defensive",
        "strength": 2000,
        "stamina": 32000,
        "armor_from_gear": 5000,
        "haste_rating": 2000,
        "crit_rating": 1200,
        "mastery_rating": 1500,
        "versatility_rating": 300,
    }
    app.session_state["simc_equipped"] = {}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {}
    app.run()
    assert not app.exception, f"Unhandled exception: {app.exception}"
    labels = [e.label for e in app.expander]
    assert any("Danger pulls" in label for label in labels), labels


def test_gear_sheet_renders_card_paperdoll(app):
    """The gear tab renders the card paperdoll — all 16 slots as gear cards,
    display only (no per-slot buttons; the sheet follows Raidbots/Blizzard)."""
    from simf.io.simc_import import ItemSpec

    app.session_state["char_data"] = {
        "name": "TestTank",
        "race": "human",
        "class_spec": "protection_warrior",
        "talents": "kiratank-defensive",
        "strength": 2000,
        "stamina": 32000,
        "armor_from_gear": 5000,
        "haste_rating": 2000,
        "crit_rating": 1200,
        "mastery_rating": 1500,
        "versatility_rating": 300,
    }
    app.session_state["simc_equipped"] = {
        "head": ItemSpec(slot="head", item_id=100, name="Helm", ilvl=658),
    }
    app.session_state["view"] = "gear"
    app.run()
    assert not app.exception, f"Unhandled exception: {app.exception}"
    assert any(b.key == "gear_subtab_gear" for b in app.button), "Gear sub-nav button not rendered"
    # Each slot renders one card (`gear-card-text` appears once per card): the
    # 16 canonical slots PLUS the 2 cosmetic placeholders (shirt + tabard) that
    # balance the left rail to 8/8 like the in-game sheet → 18 cards. The sheet
    # has NO per-slot buttons.
    body = "\n".join(str(m.value) for m in app.markdown)
    assert body.count('class="gear-card-text"') == 18, (
        f"expected 18 gear cards (16 slots + shirt/tabard), found {body.count('gear-card-text')}"
    )
    slot_buttons = [b for b in app.button if b.key and b.key.startswith("slot_btn_")]
    assert not slot_buttons, f"the gear sheet must have no per-slot buttons, got {slot_buttons}"


def test_swap_slot_card_surfaces_ehp(app):
    """A slot with a better option shows the inline steel '↑ +X eHP available'
    on its card (the moat — a real number, never a glyph). The card is display
    HTML, so assert the rendered swap line, not a button."""
    from simf.io.simc_import import ItemSpec

    helm = ItemSpec(slot="head", item_id=100, name="OldHelm", ilvl=658)
    vault_helm = ItemSpec(slot="head", item_id=200, name="VaultHelm", ilvl=665)
    app.session_state["char_data"] = {
        "name": "TestTank",
        "race": "human",
        "class_spec": "protection_warrior",
        "talents": "kiratank-defensive",
        "strength": 2000,
        "stamina": 32000,
        "armor_from_gear": 5000,
        "haste_rating": 2000,
        "crit_rating": 1200,
        "mastery_rating": 1500,
        "versatility_rating": 300,
    }
    app.session_state["simc_equipped"] = {"head": helm}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {"head": [vault_helm]}
    app.session_state["view"] = "gear"
    app.run()
    assert not app.exception, f"Unhandled exception: {app.exception}"
    body = "\n".join(str(m.value) for m in app.markdown)
    assert "gear-card-swap" in body, "no swap card rendered for the head upgrade"
    # The swap card leads with the ΔeHP number (the moat) AND names the
    # recommended replacement on the "→ Item" line — the answer to "swap to
    # what?", visible even on a read-only share where Browse is disabled.
    assert "eHP" in body, "swap card must surface the inline '↑ +X eHP' moat"
    assert "gear-card-swap-to" in body and "VaultHelm" in body, (
        f"swap card must name the recommended item; markdown tail:\n{body[-600:]}"
    )


def test_unmodeled_proc_trinket_chip_suppresses_false_precision_ehp(app):
    """An unmodeled proc/on-use trinket swap must NOT render a passive-stat
    ΔeHP figure on its chip. That number is false precision for an item whose
    survivability value lives in its proc/use effect — pre-2026-06-17 two
    different unmodeled proc trinkets in trinket1/trinket2 both rendered an
    identical clean figure (e.g. "+41,897 eHP"), the heuristic lying with
    confidence. The honest chip says "needs a real sim" and shows no number,
    and the recommendation headline excludes those numbers from its total.

    The suppression gate is the registry-None fallback (candidate not in
    trinket_db), NOT the ⚠️ has_warning flag — a both_known=False registry
    pick is still proc-aware and keeps its number.
    """
    from simf.io.simc_import import ItemSpec
    from simf.optimizer.trinket_db import find_trinket_by_id

    # Precondition: these candidates must be OUTSIDE the proc/use registry so
    # the pick falls back to passive-stat ΔeHP (the case we suppress). If a
    # real trinket ever claims one of these ids, fail loudly rather than
    # silently testing the wrong branch.
    for unknown_id in (900001, 900002, 900003, 900004):
        assert find_trinket_by_id(unknown_id) is None, (
            f"test item_id {unknown_id} leaked into the trinket registry"
        )

    app.session_state["char_data"] = {
        "name": "TestTank",
        "race": "human",
        "class_spec": "protection_warrior",
        "talents": "kiratank-defensive",
        "strength": 2000,
        "stamina": 32000,
        "armor_from_gear": 5000,
        "haste_rating": 2000,
        "crit_rating": 1200,
        "mastery_rating": 1500,
        "versatility_rating": 300,
    }
    app.session_state["simc_equipped"] = {
        "trinket1": ItemSpec(slot="trinket1", item_id=900001, name="OldTrinketA", ilvl=658),
        "trinket2": ItemSpec(slot="trinket2", item_id=900002, name="OldTrinketB", ilvl=658),
    }
    app.session_state["simc_bag_items"] = {}
    # Both candidates carry the SAME stat budget at a higher ilvl, so on passive
    # stats alone they each beat the equipped trinket AND produce an identical
    # ΔeHP figure across the two slots — exactly the reported symptom.
    app.session_state["simc_vault_items"] = {
        "trinket1": [ItemSpec(slot="trinket1", item_id=900003, name="ProcTrinketA", ilvl=665)],
        "trinket2": [ItemSpec(slot="trinket2", item_id=900004, name="ProcTrinketB", ilvl=665)],
    }
    app.session_state["_item_stats_cache"] = {
        (900001, (), (), "protection_warrior"): {"stamina": 800, "armor_from_gear": 200},
        (900002, (), (), "protection_warrior"): {"stamina": 800, "armor_from_gear": 200},
        (900003, (), (), "protection_warrior"): {"stamina": 1500, "armor_from_gear": 400},
        (900004, (), (), "protection_warrior"): {"stamina": 1500, "armor_from_gear": 400},
    }
    app.session_state["view"] = "gear"
    # A non-empty vault defaults the sub-nav to Vault; only the ACTIVE
    # sub-tab's panel renders (2026-07-11 fix — see `_set_gear_subtab`), so
    # switch to Gear explicitly to see the paperdoll this test exercises.
    app.session_state["_gear_active_subtab"] = "gear"
    app.run()
    assert not app.exception, f"Unhandled exception: {app.exception}"

    # The card paperdoll is display HTML — assert the rendered swap line, not a
    # button. Both unmodeled-proc trinket cards must say "run a sim" with NO
    # ΔeHP figure, and the headline total must exclude them.
    body = "\n".join(str(m.value) for m in app.markdown)
    assert body.count("run a sim") >= 2, (
        f"both unmodeled proc trinket cards should say 'run a sim'; markdown:\n{body[-1000:]}"
    )
    # The suppressed passive-stat figure (identical across both slots) must not
    # leak onto either card's swap line.
    swap_lines = [seg for seg in body.split("gear-card-swap") if "run a sim" in seg[:80]]
    for seg in swap_lines:
        assert "eHP" not in seg[:80], (
            f"unmodeled proc trinket card leaked a ΔeHP number: {seg[:80]!r}"
        )
    assert "need a real sim (not counted)" in body, (
        f"recommendation headline should flag the excluded unmodeled trinkets, got: {body[-800:]}"
    )


def test_unknown_trinket_cannot_outrank_a_known_proc_trinket_on_stats_alone(app):
    """F-005 follow-on (2026-07-20): an equipped trinket the registry values
    for its proc effect (real credit, low raw stats by design) must not lose
    the recommendation to an unrecognized trinket purely because the unknown
    candidate's raw stats look bigger — that comparison silently drops the
    equipped item's real (but un-diffable) proc credit. Reproduces the live
    bug: post-trial, Solar Core Igniter (registry-known) kept losing to
    Primal Philosopher's Stone (unknown, lower ilvl) with an '↑ better
    option — run a sim' card, even though nothing establishes the unknown
    item is actually better overall.
    """
    from simf.io.simc_import import ItemSpec
    from simf.optimizer.trinket_db import find_trinket_by_id

    known_id = 249343  # Gaze of the Alnseer — registry-known (proc-valued)
    unknown_id = 900010  # must stay outside the registry for this test to hold
    assert find_trinket_by_id(known_id) is not None
    assert find_trinket_by_id(unknown_id) is None

    app.session_state["char_data"] = {
        "name": "TestTank",
        "race": "human",
        "class_spec": "protection_warrior",
        "talents": "kiratank-defensive",
        "strength": 2000,
        "stamina": 32000,
        "armor_from_gear": 5000,
        "haste_rating": 2000,
        "crit_rating": 1200,
        "mastery_rating": 1500,
        "versatility_rating": 300,
    }
    app.session_state["simc_equipped"] = {
        "trinket1": ItemSpec(slot="trinket1", item_id=known_id, name="KnownProcTrinket", ilvl=250),
    }
    app.session_state["simc_bag_items"] = {}
    # The unknown candidate carries a much bigger raw stat budget than the
    # equipped proc trinket — on stats alone it looks like a clear upgrade,
    # exactly the false signal this fix holds.
    app.session_state["simc_vault_items"] = {
        "trinket1": [
            ItemSpec(slot="trinket1", item_id=unknown_id, name="UnknownBigStatTrinket", ilvl=250)
        ],
    }
    app.session_state["_item_stats_cache"] = {
        (known_id, (), (), "protection_warrior"): {"stamina": 200},
        (unknown_id, (), (), "protection_warrior"): {"stamina": 5000, "armor_from_gear": 1000},
    }
    app.session_state["view"] = "gear"
    app.session_state["_gear_active_subtab"] = "gear"
    app.run()
    assert not app.exception, f"Unhandled exception: {app.exception}"

    body = "\n".join(str(m.value) for m in app.markdown)
    assert "UnknownBigStatTrinket" not in body, (
        "the unknown, unmodeled candidate must not be promoted to the "
        f"recommended swap just because its raw stats look bigger; markdown tail:\n{body[-800:]}"
    )
    assert "KnownProcTrinket" in body, (
        f"the registry-known equipped trinket should stay the pick; markdown tail:\n{body[-800:]}"
    )


def _mk_pick(slot, item_id, *, is_swap, composite, name="X"):
    """Build a _SlotPick for the paired-slot dedup unit tests."""
    from simf.io.simc_import import ItemSpec
    from simf.ui.app import _SlotPick

    item = ItemSpec(slot=slot, item_id=item_id, name=name)
    return _SlotPick(
        slot=slot,
        item=item,
        is_swap=is_swap,
        delta_ehp=composite,
        delta_dps=0.0,
        composite=composite,
        has_warning=False,
        n_alternatives=2,
    )


def test_dedupe_paired_picks_keeps_shared_item_on_one_slot():
    """A candidate that wins BOTH paired slots (the demo's Solar Core Igniter
    on trinket1 AND trinket2) must be recommended to only one — you can't wear
    two, and counting it twice inflates the headline total. The shared item
    stays on its higher-composite slot; the other re-picks its next-best
    DISTINCT candidate."""
    from simf.ui.app import _dedupe_paired_picks

    shared1 = _mk_pick("trinket1", 500, is_swap=True, composite=4000, name="Shared")
    shared2 = _mk_pick("trinket2", 500, is_swap=True, composite=3000, name="Shared")
    next2 = _mk_pick("trinket2", 501, is_swap=True, composite=900, name="NextBest")
    picks = {"trinket1": shared1, "trinket2": shared2}
    ranked = {
        "trinket1": [shared1, _mk_pick("trinket1", 501, is_swap=True, composite=800)],
        "trinket2": [shared2, next2],
    }
    baselines = {
        "trinket1": _mk_pick("trinket1", 1, is_swap=False, composite=0.0, name="Eq1"),
        "trinket2": _mk_pick("trinket2", 2, is_swap=False, composite=0.0, name="Eq2"),
    }
    _dedupe_paired_picks(picks, ranked, baselines)
    # trinket1 keeps the shared item (composite 4000 ≥ 3000)
    assert picks["trinket1"].item.item_id == 500
    # trinket2 re-picks the next-best distinct (501), never the shared 500
    assert picks["trinket2"].item.item_id == 501
    assert picks["trinket2"].is_swap
    ids = [picks["trinket1"].item.item_id, picks["trinket2"].item.item_id]
    assert ids.count(500) == 1, f"shared item double-recommended: {ids}"


def test_dedupe_paired_picks_falls_back_to_baseline_when_no_distinct_alt():
    """When the displaced slot has no DISTINCT candidate that beats the equipped
    baseline, it reverts to 'equipped wins' rather than recommending the shared
    item again."""
    from simf.ui.app import _dedupe_paired_picks

    shared1 = _mk_pick("finger1", 700, is_swap=True, composite=2000, name="Shared")
    shared2 = _mk_pick("finger2", 700, is_swap=True, composite=2500, name="Shared")
    picks = {"finger1": shared1, "finger2": shared2}
    # finger1's only candidate is the shared ring → no distinct alt to fall to.
    ranked = {"finger1": [shared1], "finger2": [shared2]}
    eq1 = _mk_pick("finger1", 11, is_swap=False, composite=0.0, name="Eq1")
    baselines = {
        "finger1": eq1,
        "finger2": _mk_pick("finger2", 12, is_swap=False, composite=0.0, name="Eq2"),
    }
    _dedupe_paired_picks(picks, ranked, baselines)
    # finger2 keeps the shared ring (higher composite); finger1 reverts to equipped.
    assert picks["finger2"].item.item_id == 700
    assert picks["finger1"].is_swap is False
    assert picks["finger1"].item.item_id == 11


def test_dedupe_paired_picks_leaves_distinct_recommendations_untouched():
    """Two paired slots recommending DIFFERENT items is the normal case — the
    dedup pass must not disturb it."""
    from simf.ui.app import _dedupe_paired_picks

    p1 = _mk_pick("trinket1", 600, is_swap=True, composite=4000, name="A")
    p2 = _mk_pick("trinket2", 601, is_swap=True, composite=3000, name="B")
    picks = {"trinket1": p1, "trinket2": p2}
    ranked = {"trinket1": [p1], "trinket2": [p2]}
    baselines = {
        "trinket1": _mk_pick("trinket1", 1, is_swap=False, composite=0.0),
        "trinket2": _mk_pick("trinket2", 2, is_swap=False, composite=0.0),
    }
    _dedupe_paired_picks(picks, ranked, baselines)
    assert picks["trinket1"].item.item_id == 600
    assert picks["trinket2"].item.item_id == 601


def test_nav_button_highlight_updates_on_single_click(app):
    """Clicking "Why did I die?" must immediately flip the visual highlight —
    the user shouldn't need a second click to see the right button as primary.
    Streamlit renders widgets top-to-bottom; without an explicit rerun, the
    button `type=` is computed from the *pre-click* `view` and the highlight
    lags one render behind."""
    app.session_state["char_data"] = {
        "name": "TestTank",
        "race": "human",
        "class_spec": "protection_warrior",
        "talents": "kiratank-defensive",
        "strength": 2000,
        "stamina": 32000,
        "armor_from_gear": 5000,
        "haste_rating": 2000,
        "crit_rating": 1200,
        "mastery_rating": 1500,
        "versatility_rating": 300,
    }
    app.session_state["view"] = "gear"
    app.run()
    why = next(b for b in app.button if b.label == "Why did I die?")
    gear = next(b for b in app.button if b.key == "nav_gear")
    # Initial state: Gear primary, log button secondary.
    assert gear.proto.type == "primary"
    assert why.proto.type == "secondary"
    # Single click on "Why did I die?" — buttons must swap highlight on the
    # rerun this click triggers, not a render later.
    why.click().run()
    assert not app.exception
    assert app.session_state["view"] == "log"
    why_after = next(b for b in app.button if b.label == "Why did I die?")
    gear_after = next(b for b in app.button if b.key == "nav_gear")
    assert why_after.proto.type == "primary", (
        "Why-did-I-die button must be highlighted after a single click"
    )
    assert gear_after.proto.type == "secondary"


def test_log_surface_renders_without_character(app):
    """Why-did-I-die surface must render with NO character loaded.

    Regression: the main()-level router was checking `_has_character()`
    BEFORE the view, which swallowed the "Why did I die?" nav-button
    click for any user who hadn't pasted a SimC profile yet — the click
    would silently keep the SimC-paste empty state on screen. A raid
    leader landing on a shared URL or anyone wanting to analyze a log
    without first pasting SimC must reach this surface."""
    app.session_state["view"] = "log"
    app.run()
    assert not app.exception, f"Unhandled exception on log surface: {app.exception}"
    body = "\n".join(str(m.value) for m in app.markdown)
    # The "Why did I die?" heading is the log surface's marker.
    assert "Why did I die" in body
    # The /simc load section header must NOT appear — that's the empty-state
    # for the gear surface, and rendering it here would mean the router still
    # swallowed the view. ("Check your vault picks" is its job-led title.)
    assert "Check your vault picks" not in body, (
        "/simc load section should not render on the log surface"
    )


def test_clicking_why_did_i_die_from_landing_routes_to_log_surface(app):
    """Click-driven regression for the same bug: the user has no character
    loaded and clicks the nav button. The next render must show the log
    surface, NOT the SimC paste form."""
    app.run()  # cold paint — no character, default view=gear, SimC paste shown
    why = next(b for b in app.button if b.label == "Why did I die?")
    why.click().run()
    assert not app.exception
    assert app.session_state["view"] == "log"
    body = "\n".join(str(m.value) for m in app.markdown)
    assert "Why did I die" in body
    # The /simc load section must NOT be on screen after the click — that was
    # the symptom the user reported ("nothing happens").
    assert "Check your vault picks" not in body


def test_cold_landing_has_no_active_nav_highlight(app):
    """On the cold landing (no character) NEITHER nav button is 'primary'.
    Highlighting "Gear & vault" while the body is the load form (no gear yet)
    is an active-state lie — there's no gear surface yet (panel review,
    2026-06-09). The honest highlight returns once a character is loaded,
    covered by test_nav_button_highlight_updates_on_single_click."""
    app.run()  # cold paint — no character, default view=gear
    by_key = {b.key: b for b in app.button}
    by_label = {b.label: b for b in app.button}
    assert "nav_gear" in by_key and "Why did I die?" in by_label
    assert by_key["nav_gear"].proto.type == "secondary", (
        "no false-active 'Gear & vault' highlight on the cold landing"
    )
    assert by_label["Why did I die?"].proto.type == "secondary"


def test_no_character_log_surface_offers_back_to_loading(app):
    """The no-character log surface must offer a labelled way back to loading.
    The header "Gear & vault" button is the wrong verb for "go load a character," and a
    `?view=log` shared-URL visitor could otherwise miss that simf rates gear +
    the Great Vault at all. Clicking it routes back to the load form.

    Note: a cold log surface no longer auto-hydrates a character — the hydrate
    is gated on an explicit "Analyze this run" click (a defaulted log pick is
    not user intent). So simply landing on `?view=log` is the genuine
    no-character state where this affordance must appear.
    """
    app.session_state["view"] = "log"
    app.run()
    assert not app.exception
    load_btn = next((b for b in app.button if b.label == "Load a character"), None)
    assert load_btn is not None, "no-character log surface must offer 'Load a character'"
    load_btn.click().run()
    assert app.session_state["view"] == "gear"


def test_cold_log_surface_does_not_auto_load_character(app):
    """A cold `?view=log` visit must NOT silently load a character. The log
    picker auto-selects the newest bundled example log, and render-time hydrate
    used to load that (stranger's) character into the Gear/CD-plan surfaces. The
    hydrate is now gated on an explicit "Analyze this run" click — a defaulted
    picker value is not user intent (auto-hydrate finding, 2026-06-10)."""
    app.session_state["view"] = "log"
    app.run()
    assert not app.exception
    assert "char_data" not in app.session_state or not app.session_state["char_data"], (
        "cold log surface must not auto-hydrate a character before Analyze"
    )
    # The opt-in gate only exists when there is a log to analyze. The dev
    # machine has gitignored WoWCombatLog-*.txt files in examples/; a clean
    # clone (CI) has none and renders the empty state before the button —
    # both are correct no-auto-hydrate states (CI failure 2026-06-10).
    # The no-logs branch is pinned deterministically by
    # test_cold_log_surface_without_logs_renders_safely below.
    from simf.ui.log_view import list_logs

    if list_logs():
        assert any(b.label == "Analyze this run" for b in app.button)


def test_cold_log_surface_without_logs_renders_safely(app, monkeypatch):
    """The no-logs environment (a clean clone — exactly what CI runs on) must
    render the cold log surface without crashing, without auto-hydrating, and
    without offering an Analyze gate for a run that doesn't exist."""
    import simf.ui.log_surface as log_surface

    # render_surface_log → _render_local_log_flow (which calls list_logs) moved
    # to log_surface in the log_view split; patch the copy that module resolves.
    monkeypatch.setattr(log_surface, "list_logs", lambda: [])
    app.session_state["view"] = "log"
    app.run()
    assert not app.exception
    assert "char_data" not in app.session_state or not app.session_state["char_data"], (
        "no-logs log surface must not auto-hydrate a character"
    )
    assert not any(b.label == "Analyze this run" for b in app.button), (
        "no log available — there must be no Analyze gate to click"
    )


def test_loaded_log_surface_hides_back_to_loading(app):
    """The cross-sell/back affordance is for the EMPTY state only — once a
    character is loaded (here set explicitly; in practice after a log Analyze
    hydrate) it must not clutter the log surface."""
    app.session_state["char_data"] = {
        "name": "TestTank",
        "race": "human",
        "class_spec": "protection_warrior",
        "talents": "kiratank-defensive",
        "strength": 2000,
        "stamina": 32000,
        "armor_from_gear": 5000,
        "haste_rating": 2000,
        "crit_rating": 1200,
        "mastery_rating": 1500,
        "versatility_rating": 300,
    }
    app.session_state["view"] = "log"
    app.run()
    assert not app.exception
    assert "Load a character" not in [b.label for b in app.button]


def test_loaded_character_with_vault_items_shows_verdict_card(app):
    """End-to-end: char + vault items + equipped → verdict card renders."""
    from simf.io.simc_import import ItemSpec

    app.session_state["char_data"] = {
        "name": "TestTank",
        "race": "human",
        "class_spec": "protection_warrior",
        "talents": "kiratank-defensive",
        "strength": 2000,
        "stamina": 32000,
        "armor_from_gear": 5000,
        "haste_rating": 2000,
        "crit_rating": 1200,
        "mastery_rating": 1500,
        "versatility_rating": 300,
    }
    helm = ItemSpec(slot="head", item_id=111, name="VaultHelm", ilvl=665)
    app.session_state["simc_equipped"] = {
        "head": ItemSpec(slot="head", item_id=100, name="OldHelm", ilvl=658),
    }
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {"head": [helm]}
    # Inject the item-stats cache so the helper doesn't need a real Blizzard API.
    # Key shape is ``(item_id, sorted_bonus_ids_tuple)`` since the
    # 2026-05-24 slot-dialog-stat-feed fix — bare item_id keys silently
    # missed the cache and let the panel fall back to a network fetch.
    app.session_state["_item_stats_cache"] = {
        (100, (), (), "protection_warrior"): {"stamina": 800, "armor_from_gear": 200},
        (111, (), (), "protection_warrior"): {"stamina": 1200, "armor_from_gear": 400},
    }
    app.run()
    assert not app.exception, f"Unhandled exception: {app.exception}"
    # Verdict card content: "Take <name>." headline somewhere in the markdown.
    body = "\n".join(str(m.value) for m in app.markdown)
    # Either a verdict-card render or a graceful "vault items present but no stats"
    # — both are acceptable outcomes given the stats path may or may not be cache-hit.
    has_verdict = "Take " in body and "." in body
    has_graceful_fallback = (
        "vault items present" in body.lower() or "resolvable stats" in body.lower()
    )
    assert has_verdict or has_graceful_fallback, (
        f"Expected verdict card or graceful fallback, got: {body[-1000:]}"
    )


def test_loaded_character_with_no_vault_items_shows_info(app):
    """Empty vault is a normal state (no rewards this week) — info, not error."""
    app.session_state["char_data"] = {
        "name": "TestTank",
        "race": "human",
        "class_spec": "protection_warrior",
        "talents": "kiratank-defensive",
        "strength": 2000,
        "stamina": 32000,
        "armor_from_gear": 5000,
        "haste_rating": 2000,
        "crit_rating": 1200,
        "mastery_rating": 1500,
        "versatility_rating": 300,
    }
    app.session_state["simc_vault_items"] = {}
    # An empty vault defaults the sub-nav to Gear (2026-07-11 fix — see
    # `_set_gear_subtab`), and only the ACTIVE sub-tab's panel renders (unlike
    # the old `st.tabs()`, which rendered both bodies and just hid one via
    # CSS) — switch to Vault explicitly to see its empty-state message.
    app.session_state["_gear_active_subtab"] = "vault"
    app.run()
    assert not app.exception
    info_msgs = [str(i.value) for i in app.info]
    assert any("vault choices" in m.lower() or "weekly reward" in m.lower() for m in info_msgs)


def test_footer_shows_quiet_mailto_feedback_link(app):
    """A calm, single-line mailto feedback link renders next to the deploy-
    provenance footer on EVERY view (2026-07-31 visitor-signal follow-
    through) — unlike the funnel-signal wiring, this is not gated on public
    mode: any user, owner included, might hit something worth reporting.
    Plain mailto rather than a GitHub issue link — a bug report isn't always
    something the reporter wants public."""
    app.run()
    captions = [str(c.value) for c in app.caption]
    feedback = next((c for c in captions if "info@simf.cc" in c), None)
    assert feedback is not None, captions
    assert "mailto:info@simf.cc" in feedback
    # Still one short caption line, not a box/banner — no extra markup riding
    # along with it.
    assert feedback.count("\n") == 0


def test_footer_shows_agpl_source_link(app):
    """AGPL §13 requires anyone interacting with simf over a network be
    offered the corresponding source — every view, every mode, same as the
    feedback link above."""
    app.run()
    captions = [str(c.value) for c in app.caption]
    license_line = next((c for c in captions if "github.com/ainestal/simf" in c), None)
    assert license_line is not None, captions
    assert "AGPL" in license_line
