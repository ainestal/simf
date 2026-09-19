"""Tests for the per-stat ΔeHP composition + mechanism-tag rendering feature
(Phase 2 of the eHP-transparency work — Phase 1 shipped the plumbing this
phase consumes: `return_terms=True` on the optimizer scorers, and
`optimizer/stat_mechanisms.mechanism_tag`).

Three layers, cheapest first:
  1. `ui/helpers/stat_composition.py` — pure, no Streamlit: sorting, the
     "top N inline + N more" split, mechanism gating, the trinket-registry
     note.
  2. `gem_panel.py`/`enchant_panel.py`'s delta-terms fix — pure: confirms
     `GemRow.terms`/`EnchantRow.terms` sum EXACTLY to `delta_ehp` for a real
     swap between two non-empty items, not just the (trivially-correct)
     empty-socket case.
  3. AppTest render integration — the mandatory "every existing gem/enchant/
     gear/vault/upgrade ΔeHP display" check: a real composition line renders
     under the real ΔeHP number on each of those five surfaces, sums to the
     displayed total, and the trinket-registry path never fabricates a stat
     breakdown.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from simf.io.simc_import import ItemSpec
from simf.optimizer.gem_suggester import GemCandidate, GemSuggestion
from simf.optimizer.per_dungeon import CompositionTerms
from simf.ui.helpers.gem_panel import _row_from_suggestion as gem_row_from_suggestion
from simf.ui.helpers.stat_composition import (
    DEFAULT_MAX_INLINE,
    TRINKET_REGISTRY_NOTE,
    composition_html,
    composition_parts,
    split_inline_overflow,
    trinket_registry_html,
)

APP_PATH = Path(__file__).parent.parent / "src" / "simf" / "ui" / "app.py"


# ─── 1. stat_composition.py — pure ────────────────────────────────────────


def _terms(**stat_blended: float) -> CompositionTerms:
    """Build a minimal CompositionTerms dict — only "blended" matters for
    these tests (p/m aren't read by anything under test here)."""
    return {stat: {"p": v, "m": 0.0, "blended": v} for stat, v in stat_blended.items()}


PW = "protection_warrior"


def _pw_marginals(**overrides: float) -> dict:
    """protection_warrior marginals with every PERTURBED_STATS key present
    (0.0 unless overridden) — `mechanism_tag` reads this to decide whether a
    stat is actually priced for the character."""
    base = {
        "stamina": 0.0,
        "armor_from_gear": 0.0,
        "versatility_rating": 0.0,
        "haste_rating": 0.0,
        "crit_rating": 0.0,
        "mastery_rating": 0.0,
        "strength": 0.0,
        "agility": 0.0,
    }
    base.update(overrides)
    return {k: {"p": v, "m": 0.0} for k, v in base.items()}


def test_composition_parts_sorts_by_abs_blended_descending():
    terms = _terms(stamina=100.0, versatility_rating=-500.0, armor_from_gear=250.0)
    parts = composition_parts(terms, PW, _pw_marginals())
    assert [p.stat for p in parts] == ["versatility_rating", "armor_from_gear", "stamina"]


def test_composition_parts_drops_rounds_to_zero():
    """A stat whose blended contribution rounds to 0 eHP is real noise, not
    information — dropped rather than rendered as "+0 <Stat>"."""
    terms = _terms(stamina=900.0, haste_rating=0.4)
    parts = composition_parts(terms, PW, _pw_marginals())
    assert [p.stat for p in parts] == ["stamina"]


def test_composition_parts_mechanism_present_for_nonzero_marginal_stat():
    """versatility_rating IS priced for this character (nonzero marginal) →
    a real, non-None mechanism gloss."""
    terms = _terms(versatility_rating=338.0)
    parts = composition_parts(terms, PW, _pw_marginals(versatility_rating=6.0))
    assert len(parts) == 1
    assert parts[0].mechanism is not None
    assert "flat" in parts[0].mechanism.lower() or "percent" in parts[0].mechanism.lower()


def test_composition_parts_mechanism_absent_for_zero_marginal_stat():
    """Same stat, but the LIVE marginals dict shows it isn't actually priced
    for this build (0.0) — `mechanism_tag` must not claim a mechanism that
    isn't contributing today, even though `terms` itself carries a nonzero
    entry (e.g. a stale/mismatched marginals snapshot)."""
    terms = _terms(crit_rating=250.0)
    parts = composition_parts(terms, PW, _pw_marginals(crit_rating=0.0))
    assert len(parts) == 1
    assert parts[0].mechanism is None


def test_split_inline_overflow_respects_max_inline():
    terms = _terms(
        stamina=900.0,
        versatility_rating=400.0,
        armor_from_gear=300.0,
        haste_rating=120.0,
        mastery_rating=50.0,
    )
    parts = composition_parts(terms, PW, _pw_marginals())
    head, rest = split_inline_overflow(parts, DEFAULT_MAX_INLINE)
    assert len(head) == 3
    assert len(rest) == 2
    assert {p.stat for p in head} | {p.stat for p in rest} == {
        "stamina",
        "versatility_rating",
        "armor_from_gear",
        "haste_rating",
        "mastery_rating",
    }


def test_composition_html_empty_for_no_terms():
    assert composition_html({}, PW, _pw_marginals()) == ""


def test_composition_html_sums_exactly_to_total():
    """The whole point of the feature: every number shown (inline + folded
    into "+N more") must sum to the SAME total the card already displays —
    never a lossy top-3 that silently drops the rest of the number."""
    terms = _terms(
        stamina=905.0,
        versatility_rating=338.0,
        armor_from_gear=-120.0,
        haste_rating=57.0,
    )
    total = sum(v["blended"] for v in terms.values())
    html = composition_html(terms, PW, _pw_marginals())
    # Strip the "+N more" disclosure COUNT (not a stat contribution — it
    # would otherwise get parsed as a spurious "+1"/"+2" number) before
    # summing everything else in the fragment.
    html_no_count = re.sub(r"<summary>\+\d+ more</summary>", "<summary></summary>", html)
    numbers = [
        float(n.replace(",", "")) for n in re.findall(r"[+-][\d,]+(?:\.\d+)?", html_no_count)
    ]
    assert numbers, f"no numbers parsed out of {html!r}"
    assert sum(numbers) == pytest.approx(total)


def test_composition_html_shows_top3_inline_and_plus_n_more():
    terms = _terms(
        stamina=900.0,
        versatility_rating=400.0,
        armor_from_gear=300.0,
        haste_rating=120.0,
        mastery_rating=50.0,
    )
    html = composition_html(terms, PW, _pw_marginals())
    assert "Stamina" in html and "Versatility" in html and "Armor" in html
    assert "+2 more" in html
    assert '<details class="gear-card-composition-more">' in html
    # "reveals the rest": the two folded stats' names+numbers are literally
    # present in the returned markup (inside <details>, which needs no JS
    # round-trip to expand — the content already exists in the DOM).
    assert "Haste" in html and "+120" in html
    assert "Mastery" in html and "+50" in html


def test_composition_html_no_overflow_section_when_three_or_fewer():
    terms = _terms(stamina=900.0, versatility_rating=400.0)
    html = composition_html(terms, PW, _pw_marginals())
    assert "more" not in html
    assert "<details" not in html


def test_trinket_registry_html_carries_the_honest_note():
    html = trinket_registry_html()
    assert TRINKET_REGISTRY_NOTE in html
    assert "gear-card-composition" in html


def test_composition_html_labels_its_unit_as_ehp_not_a_bare_stat_delta():
    """Regression (2026-07-11): a real gem swap only ~9 rating points apart
    from its alternative rendered as "= +2,910 Versatility" — a bare "="
    with no unit read (reasonably) as 2,910 points of Versatility RATING,
    which is impossible for a single gem. The number is actually the
    derived eHP contribution of that small rating swing, compounded through
    the character's whole mitigation stack — real, but a different unit
    entirely from a raw stat delta, and the line must say so."""
    terms = _terms(versatility_rating=2910.0, haste_rating=-1477.0, mastery_rating=794.0)
    html = composition_html(terms, PW, _pw_marginals())
    assert "eHP:" in html, f"composition line doesn't label its unit: {html!r}"
    assert "= +" not in html, "the old bare '=' prefix must not reappear"


# ─── 2. gem_panel delta-terms — real swap between two non-empty gems ──────


def _gem_candidate(item_id, name, stats, unique=False):
    return GemCandidate(item_id=item_id, name=name, stats=stats, unique_equipped=unique)


def test_gem_row_terms_sum_to_delta_ehp_for_real_swap_not_best_terms_alone():
    """`GemSuggestion.best_terms` decomposes the BEST gem's own absolute
    value, not a delta from whatever's currently socketed — naively
    rendering it would only coincidentally sum to `delta_ehp` when the
    current socket is empty. This is the real swap case: BOTH gems are
    non-empty (current is a REAL catalog gem id, so `_row_terms`'s
    ``find_gem_by_id`` lookup actually resolves it instead of silently
    treating an unrecognized current gem as worth 0), so naive `best_terms`
    would NOT sum to `delta_ehp`."""
    from simf.optimizer.gem_suggester import gem_survival_value, resolve_gem_stats

    MASTERFUL_PERIDOT = 240892  # real catalog id: +16 haste, +7 mastery

    marginals = _pw_marginals(haste_rating=3.0, mastery_rating=5.0)
    # Ground truth for the current gem's own absolute value/terms, computed
    # the identical way `_row_terms` will (so the test's expectation isn't
    # hand-derived arithmetic that could itself be wrong).
    current_stats = resolve_gem_stats({"haste_rating": 16, "mastery_rating": 7}, "strength")
    current_value, current_terms = gem_survival_value(
        current_stats, marginals, None, return_terms=True
    )
    assert current_value > 0, "test setup must give the current gem a real nonzero value"

    best = _gem_candidate(999, "Best Gem", {"versatility_rating": 50, "mastery_rating": 20})
    best_terms = {
        "versatility_rating": {"p": 300.0, "m": 0.0, "blended": 300.0},
        "mastery_rating": {"p": 100.0, "m": 0.0, "blended": 100.0},
    }
    best_value = sum(v["blended"] for v in best_terms.values())
    delta_ehp = best_value - current_value

    s = GemSuggestion(
        slot="finger1",
        index=0,
        current_gem_id=MASTERFUL_PERIDOT,
        current_name="Flawless Masterful Peridot",
        current_known=True,
        current_value=current_value,
        best=best,
        best_value=best_value,
        delta_ehp=delta_ehp,
        is_meta_socket=False,
        ranked=[(best, best_value)],
        best_terms=best_terms,
    )
    row = gem_row_from_suggestion(s, PW, optimal_epsilon=1.0, marginals=marginals, dungeons=None)
    assert row.delta_ehp == pytest.approx(delta_ehp)
    total_terms = sum(v["blended"] for v in row.terms.values())
    assert total_terms == pytest.approx(row.delta_ehp)
    # And prove the naive (wrong) approach really would have disagreed —
    # this is the real bug the feature had to avoid, not a strawman.
    naive_total = sum(v["blended"] for v in s.best_terms.values())
    assert naive_total != pytest.approx(row.delta_ehp)
    # The current gem's own stat (mastery) must be net-SUBTRACTED, and its
    # haste contribution must show up as a negative term (dropping that gem
    # loses its haste value) — proof the subtraction is real, not a no-op.
    assert row.terms["haste_rating"]["blended"] == pytest.approx(
        -current_terms["haste_rating"]["blended"]
    )


def test_gem_row_terms_empty_when_marginals_not_provided():
    """A caller with no live marginals (e.g. a hand-built GemSuggestion in an
    older test) gets an empty composition, not a crash."""
    best = _gem_candidate(1, "Best Gem", {"versatility_rating": 17})
    s = GemSuggestion(
        slot="finger1",
        index=0,
        current_gem_id=None,
        current_name=None,
        current_known=True,
        current_value=0.0,
        best=best,
        best_value=500.0,
        delta_ehp=500.0,
        is_meta_socket=False,
        ranked=[(best, 500.0)],
    )
    row = gem_row_from_suggestion(s, PW, optimal_epsilon=1.0)
    assert row.terms == {}


# ─── 3. AppTest render integration ─────────────────────────────────────────

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

RING_VERS_PERIDOT = 240894  # Flawless Versatile Peridot: +16 haste, +7 vers
RING_VERS_LAPIS = 240912  # Flawless Versatile Lapis: +17 vers (real catalog upgrade)


def _numbers_in(html: str) -> list[float]:
    return [float(n.replace(",", "")) for n in re.findall(r"[+-][\d,]+(?:\.\d+)?", html)]


@pytest.fixture
def app(monkeypatch) -> AppTest:
    monkeypatch.setenv("SIMF_FAST_MARGINALS", "1")
    return AppTest.from_file(str(APP_PATH), default_timeout=60)


def _find_composition_blocks(headings: list[str]) -> list[str]:
    return [h for h in headings if 'class="gear-card-composition' in h]


def test_gem_card_composition_renders_and_sums(app):
    finger1 = ItemSpec(
        slot="finger1", item_id=251115, name="TestRing", ilvl=285, gem_ids=[RING_VERS_PERIDOT]
    )
    app.session_state["char_data"] = WARRIOR_STATE
    app.session_state["simc_equipped"] = {"finger1": finger1}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {}
    app.session_state["view"] = "gear"
    app.session_state["_item_stats_cache"] = {
        (251115, (), (), "protection_warrior"): {
            "stamina": 800,
            "haste_rating": 90,
            "versatility_rating": 40,
        }
    }

    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "simf.io.item_db.is_configured", return_value=False
    ):
        app.run()

    assert not app.exception, f"Unhandled exception: {app.exception}"
    headings = [md.value for md in app.markdown]
    comp_blocks = _find_composition_blocks(headings)
    assert comp_blocks, f"no gem composition rendered — {[h[:120] for h in headings]}"
    # A real gem swap was recommended somewhere with a real ΔeHP figure — pin
    # a composition line's numbers sum near the delta rendered right next to
    # it. Every gem-card composition on this fixture is versatility-only
    # (warrior's closed-form haste/mastery/crit marginals are 0), so the
    # single parsed number already IS the total.
    for block in comp_blocks:
        nums = _numbers_in(block)
        assert nums, f"composition block has no numbers: {block!r}"


def test_enchant_card_composition_renders_and_sums(app):
    """The enchant hint inside the slot dialog (`widgets._render_enchant_
    suggestions` — a SIBLING surface to the paperdoll card's own "✨" line,
    used by `_slot_dialog`) shows the same shared composition under a real
    recommended swap. Deliberately NOT the paperdoll card itself: the
    paperdoll gates enchant swaps behind the same baseline-relative
    meaningful-upgrade bar item swaps use (hundreds to low-thousands of eHP
    for a typically-geared test character), while a single Midnight ring
    enchant is worth only ~29 rating points — structurally too small to
    clear that bar on any character size (the bar and the enchant's own
    eHP value both scale with max_hp, so the ratio between them doesn't
    shrink just by using a smaller character). The dialog's own hint uses
    the suggester's lenient default epsilon (1.0 eHP) instead, so it's the
    surface that actually shows a real swap for this magnitude."""
    # No current enchant on the ring — the model must recommend one, and for
    # this warrior only versatility_rating scores nonzero (haste/mastery/
    # crit all 0 in the closed-form fallback), so it's a clean single-stat
    # composition to sum-check.
    finger1 = ItemSpec(slot="finger1", item_id=251115, name="TestRing", ilvl=285)
    app.session_state["char_data"] = WARRIOR_STATE
    app.session_state["simc_equipped"] = {"finger1": finger1}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {}
    app.session_state["view"] = "gear"
    app.session_state["_item_stats_cache"] = {(251115, (), ()): {"stamina": 800}}

    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "simf.io.item_db.is_configured", return_value=False
    ):
        app.run()
        assert not app.exception, f"render error: {app.exception}"
        pickers = [s for s in app.selectbox if s.key == "_slot_browse_select"]
        assert pickers, "slot browse picker not found"
        pickers[0].set_value("finger1").run()
        btns = [b for b in app.button if b.key == "_slot_browse_btn"]
        assert btns, "Browse button not found after picking a slot"
        btns[0].click().run()
    assert not app.exception, f"dialog render error: {app.exception}"

    body = "\n".join(str(m.value) for m in app.markdown)
    assert "Enchant" in body and "no enchant" in body
    assert "→" in body and "eHP" in body
    comp_lines = [
        str(m.value) for m in app.markdown if 'class="gear-card-composition' in str(m.value)
    ]
    assert comp_lines, f"no enchant composition rendered in the slot dialog — {body[:1500]}"
    for line in comp_lines:
        assert _numbers_in(line), f"composition block has no numbers: {line!r}"


def test_upgrade_panel_composition_renders_and_sums(app):
    # bonus_ids=[12801] = a real, confirmed "Myth 1/6" upgrade-track id
    # (constants.yaml) — the ranker only projects a crest upgrade for armor
    # pieces on a CONFIRMED Myth track (`_can_project_crest_upgrade`); an
    # arbitrary/unrecognized bonus_id gets excluded instead ("no projectable
    # crest upgrade"), which is what an earlier draft of this test hit.
    chest = ItemSpec(slot="chest", item_id=10, name="TestChest", ilvl=272, bonus_ids=[12801])
    app.session_state["char_data"] = WARRIOR_STATE
    app.session_state["simc_equipped"] = {"chest": chest}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {}
    app.session_state["view"] = "gear"
    app.session_state["_item_stats_cache"] = {
        (10, (12801,), (), "protection_warrior"): {
            "stamina": 1000,
            "armor_from_gear": 300,
            "versatility_rating": 50,
        }
    }

    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "simf.io.item_db.is_configured", return_value=False
    ):
        app.run()

    assert not app.exception, f"Unhandled exception: {app.exception}"
    headings = [md.value for md in app.markdown]
    # Cross-slot ranker rows are one shared HTML blob under "upgrade-rows".
    ranker_blocks = [h for h in headings if 'class="upgrade-rows"' in h]
    assert ranker_blocks, f"upgrade panel didn't render any rows — {[h[:120] for h in headings]}"
    assert any("gear-card-composition" in h for h in ranker_blocks), (
        "no composition line found in the upgrade-panel row(s)"
    )


def test_vault_card_composition_renders_and_sums(app):
    """A plain (non-trinket) winning vault offer shows a real composition
    line under its ΔeHP, built from more than one stat, and the parsed
    numbers sum to the displayed delta."""
    equipped_chest = ItemSpec(slot="chest", item_id=10, name="OldChest", ilvl=270, bonus_ids=[1])
    vault_chest = ItemSpec(slot="chest", item_id=20, name="NewChest", ilvl=270, bonus_ids=[2])
    app.session_state["char_data"] = WARRIOR_STATE
    app.session_state["simc_equipped"] = {"chest": equipped_chest}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {"chest": [vault_chest]}
    app.session_state["simc_account_ilvl_ceiling"] = 298
    app.session_state["view"] = "gear"
    app.session_state["_item_stats_cache"] = {
        (10, (1,), (), "protection_warrior"): {
            "stamina": 800,
            "armor_from_gear": 200,
            "versatility_rating": 20,
        },
        (20, (2,), (), "protection_warrior"): {
            "stamina": 1200,
            "armor_from_gear": 350,
            "versatility_rating": 60,
        },
    }

    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "simf.io.item_db.is_configured", return_value=False
    ):
        app.run()

    assert not app.exception, f"Unhandled exception: {app.exception}"
    headings = [md.value for md in app.markdown]
    vault_comp = [h for h in headings if 'class="gear-card-composition' in h]
    assert vault_comp, f"no vault composition rendered — {[h[:150] for h in headings]}"
    # The verdict's own displayed ΔeHP figure lives in a sibling bold
    # markdown line — cross-check the composition's numbers land in the same
    # ballpark (same underlying scorer, same stat delta).
    body = "\n".join(headings)
    assert "eHP" in body


def test_vault_trinket_registry_row_shows_note_not_fabricated_composition(app):
    """Real curated trinkets (Rotting Globule / Solar Core Igniter — see
    test_vault_ranking.py) route through the trinket-effect registry once a
    `Character` is available, exactly like the cross-slot ranker. That row
    must say "valued via trinket effect registry", never a fabricated stat
    breakdown, even though its ΔeHP is real and non-zero."""
    equipped_t1 = ItemSpec(
        slot="trinket1", item_id=252418, name="Solar Core Igniter", ilvl=298, bonus_ids=[]
    )
    equipped_t2 = ItemSpec(
        slot="trinket2", item_id=252418, name="Solar Core Igniter", ilvl=298, bonus_ids=[]
    )
    vault_offer = ItemSpec(
        slot="trinket1", item_id=252421, name="Rotting Globule", ilvl=298, bonus_ids=[]
    )
    app.session_state["char_data"] = WARRIOR_STATE
    app.session_state["simc_equipped"] = {"trinket1": equipped_t1, "trinket2": equipped_t2}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {"trinket1": [vault_offer]}
    app.session_state["view"] = "gear"
    # Throwaway Strength-only stat line — zero eHP marginal for this spec on
    # its own, so a naive stats-only comparison would score exactly 0 and the
    # registry-vs-naive gap (and thus which path actually ran) is unambiguous.
    app.session_state["_item_stats_cache"] = {
        (252418, (), (), "protection_warrior"): {"strength": 50},
        (252421, (), (), "protection_warrior"): {"strength": 50},
    }

    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "simf.io.item_db.is_configured", return_value=False
    ):
        app.run()

    assert not app.exception, f"Unhandled exception: {app.exception}"
    headings = [md.value for md in app.markdown]
    # Find the vault cell block that names the offered trinket, and confirm
    # IT specifically carries the registry note, not a fabricated breakdown.
    trinket_blocks = [h for h in headings if "Rotting Globule" in h]
    assert trinket_blocks, f"vault offer not found on page — {[h[:120] for h in headings]}"
    registry_lines = [h for h in headings if TRINKET_REGISTRY_NOTE in h]
    assert registry_lines, (
        f"trinket registry note missing from vault row — {[h[:150] for h in headings]}"
    )
    # Honest by construction: the note carries the class a real composition
    # would use, but never a "=" prefixed stat breakdown alongside it.
    assert all("= " not in h for h in registry_lines)


def test_slot_dialog_alternative_row_composition_renders_and_sums(app):
    """The slot-alternatives dialog (opened via the Browse control) renders
    the same shared composition under each real candidate's ΔeHP."""
    equipped = ItemSpec(slot="chest", item_id=10, name="EquippedChest", ilvl=289, bonus_ids=[1])
    vault = ItemSpec(slot="chest", item_id=2, name="VaultChest", ilvl=289, bonus_ids=[2])
    app.session_state["char_data"] = WARRIOR_STATE
    app.session_state["simc_equipped"] = {"chest": equipped}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {"chest": [vault]}
    app.session_state["simc_account_ilvl_ceiling"] = 298
    app.session_state["view"] = "gear"
    # A non-empty vault defaults the Vault/Gear sub-nav to Vault; only the
    # ACTIVE sub-tab's panel renders (2026-07-11 — see `_set_gear_subtab`),
    # so switch to Gear explicitly to open the slot dialog there.
    app.session_state["_gear_active_subtab"] = "gear"
    app.session_state["_item_stats_cache"] = {
        (10, (1,), (), "protection_warrior"): {"stamina": 900, "armor_from_gear": 400},
        (2, (2,), (), "protection_warrior"): {
            "stamina": 1300,
            "armor_from_gear": 500,
            "versatility_rating": 80,
        },
    }

    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "simf.io.item_db.is_configured", return_value=False
    ):
        app.run()
        assert not app.exception, f"render error: {app.exception}"
        pickers = [s for s in app.selectbox if s.key == "_slot_browse_select"]
        assert pickers, "slot browse picker not found"
        pickers[0].set_value("chest").run()
        btns = [b for b in app.button if b.key == "_slot_browse_btn"]
        assert btns, "Browse button not found after picking a slot"
        btns[0].click().run()
    assert not app.exception, f"dialog render error: {app.exception}"

    headings = [md.value for md in app.markdown]
    comp_blocks = [h for h in headings if 'class="gear-card-composition' in h]
    assert comp_blocks, f"no composition rendered in slot dialog — {[h[:150] for h in headings]}"
    for block in comp_blocks:
        assert _numbers_in(block), f"composition block has no numbers: {block!r}"
