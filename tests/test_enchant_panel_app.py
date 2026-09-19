"""AppTest smoke for the Gear-surface enchant info.

Pins that a card shows a named enchant line ("✨"), that the once-per-sheet
"not modeled" caption fires when a slot's enchant choices aren't priced by
the survival model (head, in this fixture), and that a confirmed-no-enchant
slot (back) renders its own honest caption instead of no line at all.
Mirrors test_gem_panel_app.py.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from simf.io.simc_import import ItemSpec

APP_PATH = Path(__file__).parent.parent / "src" / "simf" / "ui" / "app.py"

WORLDSOUL = 7987  # Mark of the Worldsoul — chest, modeled (+50 primary)
HEX_OF_LEECHING = 7961  # Empowered Hex of Leeching — head, unmodeled


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
    monkeypatch.setenv("SIMF_FAST_MARGINALS", "1")
    return AppTest.from_file(str(APP_PATH), default_timeout=30)


def test_card_shows_enchant_name_and_not_modeled_caption(app):
    chest = ItemSpec(slot="chest", item_id=249955, name="TestChest", ilvl=285, enchant_id=WORLDSOUL)
    head = ItemSpec(
        slot="head", item_id=151333, name="TestHead", ilvl=285, enchant_id=HEX_OF_LEECHING
    )

    app.session_state["char_data"] = _warrior_state()
    app.session_state["simc_equipped"] = {"chest": chest, "head": head}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {}
    app.session_state["view"] = "gear"
    app.session_state["_item_stats_cache"] = {
        (249955, (), (), "protection_warrior"): {"stamina": 900, "strength": 80},
        (151333, (), (), "protection_warrior"): {"stamina": 850, "strength": 70},
    }

    with patch("simf.io.item_db.is_configured", return_value=False):
        app.run()

    assert not app.exception, f"Unhandled exception: {app.exception}"
    headings = [md.value for md in app.markdown]
    # The chest card names its current enchant (or its recommended swap —
    # either way "Worldsoul"/"Nalorakk" shows up somewhere on the sheet).
    assert any("Worldsoul" in h or "Nalorakk" in h for h in headings), (
        f"no enchant name found on any card — observed: {[h[:60] for h in headings]}"
    )
    assert any('class="gear-card-enchant' in h for h in headings)
    # The head card's enchant choices aren't modeled — pinned via the caption,
    # not a card claim (a fake ΔeHP on an Avoidance/Leech/Speed-only slot
    # would be exactly the kind of dishonest number this project avoids).
    all_captions = " ".join(c.value for c in app.caption).lower()
    assert "aren't priced by the survival model" in all_captions, (
        f"not-modeled honesty note missing — {all_captions[:200]}"
    )


def test_enchant_line_shows_no_enchant_exists_caption_for_back(app):
    """back (cloak) can't carry a permanent enchant in Midnight 12.0.5. That
    used to render no line at all, which read as broken rather than as a
    confirmed, researched absence — the card must now say so plainly, not
    crash, and not trigger the unrelated "catalog exists but scores ~0"
    honesty caption."""
    back = ItemSpec(slot="back", item_id=193712, name="TestCloak", ilvl=285)
    app.session_state["char_data"] = _warrior_state()
    app.session_state["simc_equipped"] = {"back": back}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {}
    app.session_state["view"] = "gear"
    app.session_state["_item_stats_cache"] = {(193712, (), ()): {"stamina": 1000}}

    with patch("simf.io.item_db.is_configured", return_value=False):
        app.run()

    assert not app.exception
    headings = [md.value for md in app.markdown]
    assert any(
        'class="gear-card-enchant' in h and "No enchant exists for this slot in Midnight" in h
        for h in headings
    ), (
        f"no-enchant-exists caption missing from back's card — observed: {[h[:80] for h in headings]}"
    )
    all_captions = " ".join(c.value for c in app.caption).lower()
    assert "aren't priced by the survival model" not in all_captions
