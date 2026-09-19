"""Slot-dialog honesty/copy parity fixes (2026-07-28).

Three real gaps between the paperdoll card's already-fixed honesty states
(PR #276/#433) and the slot-dialog drill-down surface for the SAME data:

1. The enchant hint's ``optimal_grace`` state (a recognized, non-empty
   current enchant sitting just below the swap bar) still rendered the
   confident "already the best survival enchant here" line in the dialog,
   even though ``EnchantRow`` already carried ``is_identity_optimal`` /
   ``best_label`` / ``delta_label`` to say so honestly — the exact bug
   PR #433 fixed for the paperdoll card, reopened here. (Reworded 2026-07-30:
   this state no longer says "kept" either — current → best + delta only,
   no verdict word.)
2. A strict-worse alternative's Try button read "Try X" (a recommendation
   affordance) even though the row already sits under a "Worse — for
   reference" header — the vault cell's own ``is_downgrade`` reword never
   made it to this sibling surface.
3. The per-dungeon expander label ("per-dungeon") didn't match the
   Gear-tab section's own wording ("Per-dungeon breakdown", PR #434).

Tests 1 and 2 drive the real slot dialog end-to-end (AppTest); item 3 is
covered by construction (both expanders now share the literal string) plus
the loosely-lowercased assertion in test_gear_per_dungeon_breakdown.py.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from simf.io.simc_import import ItemSpec
from simf.optimizer.enchant_suggester import EnchantCandidate, EnchantSuggestion

APP_PATH = Path(__file__).parent.parent / "src" / "simf" / "ui" / "app.py"

WARRIOR_STATE = {
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
    return AppTest.from_file(str(APP_PATH), default_timeout=60)


def _open_slot_dialog(app: AppTest, slot: str) -> None:
    with patch("simf.io.item_db.is_configured", return_value=False):
        app.run()
        assert not app.exception, f"render error: {app.exception}"
        pickers = [s for s in app.selectbox if s.key == "_slot_browse_select"]
        assert pickers, "slot browse picker not found"
        pickers[0].set_value(slot).run()
        btns = [b for b in app.button if b.key == "_slot_browse_btn"]
        assert btns, "Browse button not found after picking a slot"
        btns[0].click().run()
    assert not app.exception, f"dialog render error: {app.exception}"


# ─── 1. Enchant optimal_grace: honest current → best state, not a fabricated
#        "optimal" ────────────────────────────────────────────────────────


def test_slot_dialog_enchant_optimal_grace_names_the_better_enchant(app, monkeypatch):
    """A recognized, non-empty ring enchant that's a hair below the model's
    best (``optimal_grace`` — real, known, delta below the epsilon) must
    render the same honest "{current} → **{best}**  ·  **{delta}**" state
    the paperdoll card uses (no verdict word — 2026-07-30), not the
    confident "already the best" line reserved for a genuine identity
    match."""
    ring = ItemSpec(
        slot="finger1", item_id=251115, name="TestRing", ilvl=285, enchant_id=8025
    )  # Silvermoon's Alacrity — real catalog id, so the row isn't "unrecognized".
    app.session_state["char_data"] = WARRIOR_STATE
    app.session_state["simc_equipped"] = {"finger1": ring}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {}
    app.session_state["view"] = "gear"
    app.session_state["_item_stats_cache"] = {(251115, (), ()): {"stamina": 800}}

    # A hand-built suggestion pins the exact optimal_grace inputs
    # (current_known, non-empty current_enchant_id, delta_ehp below the 1.0
    # eHP epsilon) without depending on the closed-form marginals happening
    # to produce a near-tie between two real catalog enchants.
    fake_suggestion = EnchantSuggestion(
        slot="finger1",
        current_enchant_id=8025,
        current_name="Silvermoon's Alacrity",
        current_known=True,
        current_value=1000.0,
        current_wowhead_spell_id=1236088,
        best=EnchantCandidate(
            enchant_id=555555,
            name="Zul'jin's Mastery",
            stats={"mastery_rating": 29},
            wowhead_spell_id=1236060,
        ),
        best_value=1000.5,
        delta_ehp=0.5,
        modeled=True,
    )
    monkeypatch.setattr(
        "simf.ui.helpers.enchant_panel.suggest_enchants",
        lambda *a, **kw: [fake_suggestion],
    )

    _open_slot_dialog(app, "finger1")

    body = "\n".join(str(m.value) for m in app.markdown)
    assert "already the best survival enchant" not in body, (
        f"optimal_grace still rendered the confident-optimal line — {body[:1500]}"
    )
    # Scoped to the specific rendered line (not a bare "kept" substring check
    # against the WHOLE page's markdown — that coincidentally matched a
    # `<style>` comment, "Token names kept from the v0.9 dark palette,"
    # making the old assertion pass for the wrong reason).
    assert "Silvermoon's Alacrity → **Zul'jin's Mastery**" in body, (
        f"optimal_grace must show the neutral current → best + delta line, no "
        f"verdict word — {body[:1500]}"
    )


# ─── 2. Strict-worse alternative: "Trial anyway ·", not a bare "Try" ──────────


def test_slot_dialog_worse_alternative_uses_trial_anyway_label(app):
    """A strict-worse bag/vault alternative (negative ΔeHP) already renders
    under a "Worse — for reference" header and a secondary/ghost button —
    its label and help text must say so too, mirroring the vault cell's
    ``is_downgrade`` reword instead of the bare, recommendation-shaped
    "Try X"."""
    equipped = ItemSpec(slot="chest", item_id=10, name="StrongChest", ilvl=289, bonus_ids=[1])
    weak_bag = ItemSpec(slot="chest", item_id=99, name="WeakChest", ilvl=250, bonus_ids=[2])
    app.session_state["char_data"] = WARRIOR_STATE
    app.session_state["simc_equipped"] = {"chest": equipped}
    app.session_state["simc_bag_items"] = {"chest": [weak_bag]}
    app.session_state["simc_vault_items"] = {}
    app.session_state["view"] = "gear"
    app.session_state["_gear_active_subtab"] = "gear"
    app.session_state["_item_stats_cache"] = {
        (10, (1,), (), "protection_warrior"): {
            "stamina": 2000,
            "armor_from_gear": 800,
            "versatility_rating": 200,
        },
        (99, (2,), (), "protection_warrior"): {"stamina": 200},
    }

    _open_slot_dialog(app, "chest")

    body = "\n".join(str(m.value) for m in app.markdown)
    assert "Worse — for reference:" in body, (
        f"weak bag item didn't land in the strict-worse bucket — {body[:1500]}"
    )

    trial_btn = next((b for b in app.button if (b.label or "").startswith("Trial anyway ·")), None)
    assert trial_btn is not None, (
        f"'Trial anyway ·' button not found — observed labels: "
        f"{[b.label for b in app.button if b.key and 'try_alt' in b.key]}"
    )
    assert "net loss vs your equipped gear" in (trial_btn.help or ""), (
        f"help text doesn't name the net-loss trade-off — {trial_btn.help!r}"
    )
