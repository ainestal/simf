"""AppTest: the slot dialog's upgrade-normalized comparison.

Verifies end-to-end that opening a slot dialog renders the new "Compare at
item level" control and that the default "Match my gear" mode flips a
lower-ilvl vault piece (with better stat-per-ilvl) from a downgrade to a win —
the Tuesday-vault problem this feature solves.

Stats are pre-seeded into the session-state ``_item_stats_cache`` (keyed on
``(item_id, sorted bonus_ids, sorted crafted_stats)``) so ``_stats_for_item``
resolves without hitting Wowhead — the same cache the live app fills from
the network.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from simf.io.simc_import import ItemSpec

APP_PATH = Path(__file__).parent.parent / "src" / "simf" / "ui" / "app.py"


def _char_state() -> dict:
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


def _seed(app: AppTest) -> None:
    # Equipped chest ilvl 289; vault chest ilvl 259 with MORE stamina-per-ilvl
    # (950/259 > 1000/289) so it loses as-dropped but wins normalized to 289.
    equipped = ItemSpec(slot="chest", item_id=10, name="EquippedChest", ilvl=289, bonus_ids=[1])
    vault = ItemSpec(slot="chest", item_id=2, name="VaultChest", ilvl=259, bonus_ids=[2])
    app.session_state["char_data"] = _char_state()
    app.session_state["simc_equipped"] = {"chest": equipped}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {"chest": [vault]}
    app.session_state["simc_account_ilvl_ceiling"] = 298
    app.session_state["view"] = "gear"
    # A non-empty vault defaults the Vault/Gear sub-nav to Vault; only the
    # ACTIVE sub-tab's panel renders (2026-07-11 — see `_set_gear_subtab`),
    # so switch to Gear explicitly for tests that open the slot dialog there.
    app.session_state["_gear_active_subtab"] = "gear"
    # Pre-seed stats so _stats_for_item is network-free (same cache shape the
    # live helper uses: key = (item_id, tuple(sorted(bonus_ids)), tuple(sorted(crafted_stats)))).
    app.session_state["_item_stats_cache"] = {
        (10, (1,), (), "protection_warrior"): {"stamina": 1000, "armor_from_gear": 500},
        (2, (2,), (), "protection_warrior"): {"stamina": 950, "armor_from_gear": 480},
    }


@pytest.fixture
def app() -> AppTest:
    return AppTest.from_file(str(APP_PATH), default_timeout=60)


def _open_chest_dialog(app: AppTest) -> None:
    app.run()
    assert not app.exception, f"render error: {app.exception}"
    # The card paperdoll is display-only; open a slot's dialog via the off-sheet
    # browse control (slot picker → Browse button).
    pickers = [s for s in app.selectbox if s.key == "_slot_browse_select"]
    assert pickers, "slot browse picker not found"
    pickers[0].set_value("chest").run()
    assert not app.exception, f"picker render error: {app.exception}"
    btns = [b for b in app.button if b.key == "_slot_browse_btn"]
    assert btns, "Browse button not found after picking a slot"
    btns[0].click().run()
    assert not app.exception, f"dialog render error: {app.exception}"


def test_slot_dialog_renders_compare_control(app):
    _seed(app)
    _open_chest_dialog(app)
    labels = [r.label or "" for r in app.radio]
    assert any("Compare at item level" in lbl for lbl in labels), (
        f"compare control missing; radios seen: {labels}"
    )


def test_match_gear_default_flips_low_ilvl_vault_to_upgrade(app):
    _seed(app)
    _open_chest_dialog(app)
    # Default mode is "Match my gear" → the vault chest, normalized to 289,
    # beats equipped, so the positive "Alternatives" header renders (it only
    # appears when at least one candidate scores ΔeHP ≥ 0).
    body = "\n".join(str(m.value) for m in app.markdown)
    assert "Alternatives" in body, f"no positive-bucket header; markdown:\n{body[:1500]}"


def test_compare_control_defaults_to_match_gear(app):
    _seed(app)
    _open_chest_dialog(app)
    compare = [r for r in app.radio if "Compare at item level" in (r.label or "")]
    assert compare, "compare control radio not found"
    assert compare[0].value == "Match my gear"
