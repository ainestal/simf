"""AppTest smoke for the Gear-surface upgrade-impact panel.

Pins that the panel renders without exception and that the per-row math
flows through to a visible delta — guard against accidentally cutting
off the panel from the gear surface, or breaking the marginals call
shape it depends on.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from simf.io.simc_import import ItemSpec
from simf.optimizer.item_upgrade import UpgradeRow
from simf.ui.upgrade_panel import _can_project_crest_upgrade, _upgrade_row_html

APP_PATH = Path(__file__).parent.parent / "src" / "simf" / "ui" / "app.py"
UPGRADE_PANEL_SOURCE = (
    Path(__file__).parent.parent / "src" / "simf" / "ui" / "upgrade_panel.py"
).read_text()
APP_SOURCE = APP_PATH.read_text()


def _baseline_char_state() -> dict:
    return {
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


@pytest.fixture
def app(monkeypatch) -> AppTest:
    # Use the fast-marginals bypass so the AppTest doesn't spend ~10s
    # spinning up the sim-derived survivability path. The closed-form
    # fallback's marginals are well-shaped for upgrade scoring even if
    # they under-credit haste/crit/mastery — we just need *some* numbers.
    monkeypatch.setenv("SIMF_FAST_MARGINALS", "1")
    # AppTest internal timeout. Standalone the test wraps in ~20s; under
    # xdist-parallel contention on Pi the first-run sometimes spills past
    # that, surfacing as a `script run timed out after 20(s)` from
    # Streamlit's `require_widgets_deltas`. 30s leaves headroom without
    # masking a real regression — a true hang still trips before pytest's
    # own job timeout.
    return AppTest.from_file(str(APP_PATH), default_timeout=30)


def test_upgrade_panel_renders_with_equipped_items(app):
    """With a small equipped set + ilvls present, the upgrade panel
    heading and a row with an ilvl arrow should both appear."""
    # Midnight 12.0.5 ilvls — under the season ceiling so the panel scores
    # both rows. The pre-cap baseline used 660/658 (legacy expansion), which
    # now correctly excludes every slot as "past the cap." A recognised Myth
    # rank `bonus_id` is required on each armor item now too (2026-07-04
    # fix) — an armor-category item with NO confirmed track is excluded as
    # "can't tell an already-maxed lower track from real crest headroom,"
    # same honesty rule the paperdoll badge already applies.
    chest = ItemSpec(slot="chest", item_id=12345, name="TestChest", ilvl=285, bonus_ids=[12805])
    head = ItemSpec(slot="head", item_id=12346, name="TestHelm", ilvl=283, bonus_ids=[12803])

    app.session_state["char_data"] = _baseline_char_state()
    app.session_state["simc_equipped"] = {"chest": chest, "head": head}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {}
    app.session_state["view"] = "gear"
    # Force a deterministic stat dict so the panel doesn't depend on
    # network fetch in CI / on the Pi during unit-test runs. The cache
    # key shape is ``(item_id, sorted_bonus_ids_tuple, sorted_crafted_stats_tuple)``
    # since the 2026-05-24 slot-dialog-stat-feed fix (widened 2026-07-11 to
    # include crafted_stats) — see test_slot_dialog_stat_feed.py for the
    # underlying bug.
    app.session_state["_item_stats_cache"] = {
        (12345, (12805,), (), "protection_warrior"): {"stamina": 1000, "armor_from_gear": 4000},
        (12346, (12803,), (), "protection_warrior"): {"stamina": 800, "haste_rating": 600},
    }

    # The `_stats_for_item` helper reads from `_item_stats_cache` first,
    # so no network call should fire. But Blizzard creds aren't expected
    # in CI either — patch `is_configured` defensively so the icon path
    # doesn't try to call out.
    with patch("simf.io.item_db.is_configured", return_value=False):
        app.run()

    assert not app.exception, f"Unhandled exception: {app.exception}"

    # The panel emits an `### Apply an upgrade …` heading. AppTest exposes
    # markdown elements via `app.markdown`; assert one contains our
    # heading substring rather than requiring exact-match copy.
    headings = [md.value for md in app.markdown]
    assert any("Apply an upgrade" in h for h in headings), (
        "Upgrade panel heading missing from gear surface — "
        f"observed markdowns: {[h[:40] for h in headings]}"
    )

    # The ilvl arrow `660 → 666` (or similar) should appear in at least
    # one rendered row.
    assert any("→" in h and "i" not in h[:3] for h in headings if isinstance(h, str)) or any(
        "→" in h for h in headings
    )


def test_upgrade_panel_absent_when_no_equipped(app):
    """The panel is silent when nothing is equipped — better than rendering
    an empty heading."""
    app.session_state["char_data"] = _baseline_char_state()
    app.session_state["simc_equipped"] = {}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {}
    app.session_state["view"] = "gear"

    with patch("simf.io.item_db.is_configured", return_value=False):
        app.run()

    assert not app.exception
    headings = [md.value for md in app.markdown]
    assert not any("Apply an upgrade" in h for h in headings)


def test_upgrade_panel_labeled_subordinate_and_darkened(app):
    """P4 polish (2026-07-02): the panel names itself a distinct, subordinate
    utility ('Cross-slot ranker') and its row list joins the dark
    character-sheet treatment the paperdoll above it already uses — so the
    two surfaces read as one design language instead of a dark grid dropping
    back to a plain light list."""
    chest = ItemSpec(slot="chest", item_id=12345, name="TestChest", ilvl=285, bonus_ids=[12805])
    app.session_state["char_data"] = _baseline_char_state()
    app.session_state["simc_equipped"] = {"chest": chest}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {}
    app.session_state["view"] = "gear"
    app.session_state["_item_stats_cache"] = {
        (12345, (12805,), (), "protection_warrior"): {"stamina": 1000, "armor_from_gear": 4000},
    }

    with patch("simf.io.item_db.is_configured", return_value=False):
        app.run()

    assert not app.exception, f"Unhandled exception: {app.exception}"
    body = "\n".join(str(m.value) for m in app.markdown)
    assert "Cross-slot ranker" in body, "subordinate eyebrow label missing"
    assert 'class="gear-sheet-dark upgrade-panel-dark"' in body, (
        "upgrade rows must join the dark character-sheet scope"
    )


# ── _can_project_crest_upgrade unit tests (2026-07-04 fix) ─────────────────
# Real report: a crafted chest and a ring already maxed on the (unregistered)
# Hero track both showed a bogus crest-upgrade projection. Neither a crest
# (nothing left to buy) nor a Voidcore (armor-category slots don't take one)
# could actually move either piece further.


def test_can_project_crest_upgrade_excludes_crafted_gear() -> None:
    """Crafted gear uses its own quality-rank system, not a loot upgrade
    track — no honest crest ceiling exists to project, at any slot category."""
    item = ItemSpec(
        slot="chest", item_id=1, name="Silvermoon Agent's Coat", ilvl=285, crafting_quality=5
    )
    assert _can_project_crest_upgrade("chest", item) is False


def test_can_project_crest_upgrade_excludes_armor_with_unconfirmed_track() -> None:
    """An armor-category item with no recognised Myth-rank bonus_id could be
    genuinely un-trackable OR already maxed on a lower (e.g. Hero) track —
    276 is BOTH 'Hero 6/6' (no headroom left) and 'Myth 2/6' (real headroom),
    and guessing which one repeats the exact bug this fix closes."""
    item = ItemSpec(slot="finger1", item_id=2, name="Band of the Triumvirate", ilvl=276)
    assert _can_project_crest_upgrade("finger1", item) is False


def test_can_project_crest_upgrade_excludes_confirmed_hero_track_too() -> None:
    """A CONFIRMED-but-not-Myth track (Hero 6/6, bonus_id 12798) must be
    excluded too — not just genuinely unrecognised items. This is the exact
    real bug: 12798 was originally mislabeled "Myth 2/6" in constants.yaml,
    so an earlier version of this check (requiring only ANY recognised
    track, not specifically Myth) was fooled into including it. Fixed both
    the label (constants.yaml, verified against Wowhead's own "Upgrade
    Level: Hero 6/6" tooltip text for this id) and this check (now requires
    ``track == "Myth"`` specifically)."""
    item = ItemSpec(
        slot="finger2", item_id=6, name="Band of the Triumvirate", ilvl=276, bonus_ids=[12798]
    )
    assert _can_project_crest_upgrade("finger2", item) is False


def test_can_project_crest_upgrade_includes_armor_with_confirmed_myth_rank() -> None:
    """An armor item WITH a recognised Myth-rank bonus_id has a real, honest
    ceiling to project (rank < max_rank means real crest headroom remains)."""
    item = ItemSpec(slot="chest", item_id=3, name="Some Chest", ilvl=279, bonus_ids=[12803])
    assert _can_project_crest_upgrade("chest", item) is True


def test_can_project_crest_upgrade_does_not_require_track_for_weapon_or_trinket() -> None:
    """Weapon/trinket slots are NOT held to the same bar — only the top rung
    (ceiling) bonus_id is registered today, so requiring a confirmed track
    here would wrongly exclude every not-yet-maxed weapon/trinket (a real
    regression; no matching ilvl-ambiguity risk justifies it the way armor's
    Hero/Myth overlap does)."""
    item = ItemSpec(slot="trinket1", item_id=4, name="Some Trinket", ilvl=290)
    assert _can_project_crest_upgrade("trinket1", item) is True
    weapon = ItemSpec(slot="main_hand", item_id=5, name="Some Weapon", ilvl=290)
    assert _can_project_crest_upgrade("main_hand", weapon) is True


def test_upgrade_panel_excludes_crafted_and_unconfirmed_track_slots(app):
    """End-to-end: a crafted chest + an unconfirmed-track ring are both
    absent from the rendered panel, and the 'not shown' caption names the
    count — real report, real gear (AnonGuardian1, 2026-07-04)."""
    crafted_chest = ItemSpec(
        slot="chest", item_id=1, name="Silvermoon Agent's Coat", ilvl=285, crafting_quality=5
    )
    unconfirmed_ring = ItemSpec(slot="finger1", item_id=2, name="Band of the Triumvirate", ilvl=276)
    real_shoulder = ItemSpec(
        slot="shoulder", item_id=3, name="Night Ender's Pauldrons", ilvl=279, bonus_ids=[12803]
    )

    app.session_state["char_data"] = _baseline_char_state()
    app.session_state["simc_equipped"] = {
        "chest": crafted_chest,
        "finger1": unconfirmed_ring,
        "shoulder": real_shoulder,
    }
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {}
    app.session_state["view"] = "gear"
    app.session_state["_item_stats_cache"] = {
        (1, (), (), "protection_warrior"): {"stamina": 1000, "armor_from_gear": 4000},
        (2, (), (), "protection_warrior"): {"haste_rating": 700},
        (3, (12803,), (), "protection_warrior"): {"stamina": 900},
    }

    with patch("simf.io.item_db.is_configured", return_value=False):
        app.run()

    assert not app.exception, f"Unhandled exception: {app.exception}"
    body = "\n".join(str(m.value) for m in app.markdown)
    # The item names also legitimately appear on the paperdoll CARD above the
    # ranker (a different surface) — scope the assertion to the ranker's own
    # row markup (`slot-row-name`, distinct from the card's `gear-card-name`).
    ranker_start = body.index('class="upgrade-rows"')
    ranker_html = body[ranker_start:]
    assert "Silvermoon Agent's Coat" not in ranker_html
    assert "Band of the Triumvirate" not in ranker_html
    assert "Night Ender's Pauldrons" in ranker_html
    captions = "\n".join(str(c.value) for c in app.caption)
    assert "2 equipped slots not shown" in captions


# ── dead-state fix: an all-at-ceiling result must not still show a live
# stepper above a caption that blames the user's export ──────────────────


def test_upgrade_panel_all_at_ceiling_shows_only_one_sentence(app):
    """Real report: with every projectable slot already at its own reachable
    ceiling (armor 289 / weapon 298 in Midnight 12.0.5), the section used to
    still render the heading + subtitle + live 'ilvl gain' stepper above a
    caption that (falsely) blamed a missing `/simc` export — the cards above
    it already show a known ilvl, so that branch could only ever fire when
    ilvls WERE known. The section must now collapse to the single honest
    'already at ceiling' sentence: no eyebrow, no heading, no stepper, and
    the word `/simc` must not appear anywhere near it."""
    chest = ItemSpec(slot="chest", item_id=1, name="Ceiling Chest", ilvl=289, bonus_ids=[12805])
    weapon = ItemSpec(slot="main_hand", item_id=2, name="Ceiling Weapon", ilvl=298)

    app.session_state["char_data"] = _baseline_char_state()
    app.session_state["simc_equipped"] = {"chest": chest, "main_hand": weapon}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {}
    app.session_state["view"] = "gear"
    app.session_state["_item_stats_cache"] = {
        (1, (12805,), (), "protection_warrior"): {"stamina": 1000, "armor_from_gear": 4000},
        (2, (), (), "protection_warrior"): {"strength": 500},
    }

    with patch("simf.io.item_db.is_configured", return_value=False):
        app.run()

    assert not app.exception, f"Unhandled exception: {app.exception}"

    headings = [md.value for md in app.markdown]
    assert not any("Apply an upgrade" in h for h in headings), (
        "heading must not render when there is nothing to rank"
    )
    body = "\n".join(str(h) for h in headings)
    assert "Cross-slot ranker" not in body, "eyebrow must not render either"
    assert not app.number_input, "the ilvl-gain stepper must not render on a dead result"

    captions = "\n".join(str(c.value) for c in app.caption)
    assert "already at its reachable ceiling" in captions
    assert "/simc" not in captions, (
        "reaching this state means every ilvl was already known — never blame the export"
    )
    assert "/simc" not in body


def test_upgrade_panel_with_headroom_still_renders_heading_and_stepper(app):
    """Sibling case: a real, non-empty ranking still renders the full
    section unchanged — heading, subtitle, the 'ilvl gain' stepper, and at
    least one ranked row."""
    chest = ItemSpec(slot="chest", item_id=12345, name="TestChest", ilvl=285, bonus_ids=[12805])
    app.session_state["char_data"] = _baseline_char_state()
    app.session_state["simc_equipped"] = {"chest": chest}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {}
    app.session_state["view"] = "gear"
    app.session_state["_item_stats_cache"] = {
        (12345, (12805,), (), "protection_warrior"): {"stamina": 1000, "armor_from_gear": 4000},
    }

    with patch("simf.io.item_db.is_configured", return_value=False):
        app.run()

    assert not app.exception, f"Unhandled exception: {app.exception}"
    headings = [md.value for md in app.markdown]
    assert any("Apply an upgrade" in h for h in headings)
    assert any(ni.label == "ilvl gain" for ni in app.number_input), (
        "the stepper must still render when a slot has real headroom"
    )
    body = "\n".join(str(h) for h in headings)
    assert 'class="upgrade-rows"' in body


# ── _upgrade_row_html unit tests (pure string builder) ─────────────────────


def _row(**overrides) -> UpgradeRow:
    defaults = dict(
        slot="chest",
        item_id=100,
        item_name="Test Chest",
        current_ilvl=285,
        upgraded_ilvl=291,
        delta_ehp=1200.0,
        delta_dps_pct=0.003,
        is_trinket=False,
        is_capped=False,
    )
    defaults.update(overrides)
    return UpgradeRow(**defaults)


def test_upgrade_row_html_is_a_pure_builder() -> None:
    """`_upgrade_row_html` returns the row's HTML rather than rendering it
    itself — required so the panel can join every row into ONE `st.markdown`
    call (a separate call per row would show cream seams between them on the
    dark background, since Streamlit gaps sibling elements)."""
    body = UPGRADE_PANEL_SOURCE[
        UPGRADE_PANEL_SOURCE.find("def _upgrade_row_html(") : UPGRADE_PANEL_SOURCE.find(
            "\ndef ", UPGRADE_PANEL_SOURCE.find("def _upgrade_row_html(") + 1
        )
    ]
    # Check for an actual call (the open paren), not just a mention — the
    # docstring itself references "a single ``st.markdown`` call" in prose.
    assert "st.markdown(" not in body and "st.write(" not in body, (
        "_upgrade_row_html must be a pure string builder, not a renderer"
    )
    html = _upgrade_row_html(_row(), equipped={})
    assert isinstance(html, str)
    assert html.startswith('<div class="slot-row">')


def test_upgrade_row_html_requests_large_icon_source(monkeypatch) -> None:
    """The row's icon fetches Wowhead's 'large' (56px) source so the
    52px-displayed icon downsamples instead of upscaling the 36px default."""
    import simf.ui.item_html as item_html_mod

    monkeypatch.setattr(item_html_mod, "_icon_for_item", lambda spec: "inv_chest_cloth_01")
    item = ItemSpec(slot="chest", item_id=100, name="Test Chest", ilvl=285)
    html = _upgrade_row_html(_row(), equipped={"chest": item})
    assert "/large/" in html, f"expected a large-source icon URL, got: {html}"


def test_panel_css_present() -> None:
    for needle in (
        ".upgrade-panel-eyebrow {",
        ".gear-sheet-dark.upgrade-panel-dark {",
        ".gear-sheet-dark .slot-row-icon.q-epic",
        ".gear-sheet-dark .upgrade-cap-badge",
    ):
        assert needle in APP_SOURCE, f"missing upgrade-panel dark CSS: {needle}"
