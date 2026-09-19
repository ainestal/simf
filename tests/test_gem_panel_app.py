"""AppTest smoke for the Gear-surface gem info.

2026-07-01: the old consolidated "#### 💎 Gems" text block below the paperdoll
was REMOVED (user ask — redundant once each card names its own gem). Pins
what replaced it: a socketed card shows a named, Wowhead-linked gem line, and
the honesty captions (Guardian mastery/haste, Eversong meta-drop) still render
standalone below the sheet — a guard against accidentally cutting either half
or breaking the marginals/equipped call shape they depend on. (The ranking
math is covered by test_gem_suggester; the row formatting by test_gem_panel.)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from simf.io.simc_import import ItemSpec

APP_PATH = Path(__file__).parent.parent / "src" / "simf" / "ui" / "app.py"

NECK_GEM = 240983  # Indecipherable Eversong Diamond
RING_GEM = 240894  # Flawless Versatile Peridot


def _warrior_state() -> dict:
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
    # Closed-form marginals bypass — fast, and still well-shaped enough that a
    # socketed gem scores a non-zero survival value (vers/armor/stamina).
    monkeypatch.setenv("SIMF_FAST_MARGINALS", "1")
    return AppTest.from_file(str(APP_PATH), default_timeout=30)


def test_card_shows_gem_name_and_honesty_caption_for_socketed_gear(app):
    neck = ItemSpec(slot="neck", item_id=50228, name="TestNeck", ilvl=285, gem_ids=[NECK_GEM])
    finger1 = ItemSpec(
        slot="finger1", item_id=251115, name="TestRing", ilvl=285, gem_ids=[RING_GEM]
    )

    app.session_state["char_data"] = _warrior_state()
    app.session_state["simc_equipped"] = {"neck": neck, "finger1": finger1}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {}
    app.session_state["view"] = "gear"
    app.session_state["_item_stats_cache"] = {
        (50228, (), (), "protection_warrior"): {"stamina": 900, "strength": 80},
        (251115, (), (), "protection_warrior"): {
            "stamina": 800,
            "haste_rating": 90,
            "versatility_rating": 40,
        },
    }

    with patch("simf.io.item_db.is_configured", return_value=False):
        app.run()

    assert not app.exception, f"Unhandled exception: {app.exception}"
    headings = [md.value for md in app.markdown]
    # The old consolidated section is gone — no heading, anywhere.
    assert not any("#### 💎 Gems" in h for h in headings)
    # The card itself names a recommended gem (catalog gems are 'Flawless …'
    # or 'Eversong Diamond') and Wowhead-links it for the hover tooltip.
    assert any("Flawless" in h or "Eversong" in h for h in headings), (
        f"no gem name found on any card — observed: {[h[:60] for h in headings]}"
    )
    # class="gear-card-gems (not the bare substring) — the static CSS block
    # also contains ".gear-card-gems { ... }" as a selector, always present
    # regardless of whether any card actually renders the class.
    assert any('class="gear-card-gems' in h for h in headings)
    assert any("https://www.wowhead.com/item=" in h for h in headings)
    # Survival-only / throughput-cost honesty must survive as a standalone
    # caption (Brutoh 2026-06-24): the warrior's str Eversong is dropped here,
    # so the meta-drop note fires.
    all_captions = " ".join(c.value for c in app.caption).lower()
    assert "throughput" in all_captions, f"meta-drop honesty note missing — {all_captions[:200]}"


def _guardian_state() -> dict:
    return {
        "name": "TestBear",
        "race": "tauren",
        "class_spec": "guardian_druid",
        "talents": "anonguardian2-guardian",
        "strength": 0,
        "agility": 1900,
        "stamina": 33000,
        "armor_from_gear": 919,
        "haste_rating": 1235,
        "crit_rating": 800,
        "mastery_rating": 668,
        "versatility_rating": 498,
    }


def test_gem_section_guardian_shows_mastery_and_dotc_notes(app):
    """Target spec: a (non-Elune's-Chosen) Guardian sees the mastery-not-modeled
    disclosure AND the Druid-of-the-Claw 'haste does nothing' steering."""
    neck = ItemSpec(slot="neck", item_id=50228, name="TestNeck", ilvl=285, gem_ids=[NECK_GEM])
    finger1 = ItemSpec(
        slot="finger1", item_id=251115, name="TestRing", ilvl=285, gem_ids=[RING_GEM]
    )
    app.session_state["char_data"] = _guardian_state()
    app.session_state["simc_equipped"] = {"neck": neck, "finger1": finger1}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {}
    app.session_state["view"] = "gear"
    app.session_state["_item_stats_cache"] = {
        (50228, (), (), "guardian_druid"): {"stamina": 900, "agility": 80},
        (251115, (), (), "guardian_druid"): {"stamina": 800, "versatility_rating": 90},
    }

    with patch("simf.io.item_db.is_configured", return_value=False):
        app.run()

    assert not app.exception, f"Unhandled exception: {app.exception}"
    captions = " ".join(c.value for c in app.caption).lower()
    assert "mastery" in captions, f"Guardian mastery-not-modeled note missing — {captions[:200]}"
    assert "haste does nothing" in captions, f"DotC haste steering missing — {captions[:200]}"


def test_gem_line_absent_without_sockets(app):
    """No socketed gear → no gem line on any card, and no honesty captions
    (nothing to say when there's nothing to re-gem)."""
    chest = ItemSpec(slot="chest", item_id=12345, name="TestChest", ilvl=285)  # no gem_ids
    app.session_state["char_data"] = _warrior_state()
    app.session_state["simc_equipped"] = {"chest": chest}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {}
    app.session_state["view"] = "gear"
    app.session_state["_item_stats_cache"] = {
        (12345, (), (), "protection_warrior"): {"stamina": 1000, "armor_from_gear": 4000}
    }

    with patch("simf.io.item_db.is_configured", return_value=False):
        app.run()

    assert not app.exception
    headings = [md.value for md in app.markdown]
    # class="gear-card-gems (not the bare substring, which the static CSS
    # block's ".gear-card-gems { ... }" selector always matches regardless).
    assert not any('class="gear-card-gems' in h for h in headings)
    assert not any(h.strip().startswith("#### 💎 Gems") for h in headings)
