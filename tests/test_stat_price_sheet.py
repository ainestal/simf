"""Tests for the stat price sheet (`ui/helpers/stat_price_sheet.py`) — the
collapsed "how the engine prices your stats" expander on the Gear tab.

Four layers, cheapest first:
  1. `compute_stat_price_rows` — pure, no Streamlit: one row per
     `PERTURBED_STATS` entry, CI carried through only when actually
     present in the input `ci` dict (never fabricated), a structural
     `NoCiReason` attached whenever it isn't, and a `mechanism` gloss
     threaded through from `optimizer.stat_mechanisms.mechanism_tag`.
  2. `compute_school_mix_blend` — pure, unchanged by the 2026-07-10
     readability restructure (still feeds the nested "for the skeptical"
     disclosure's live blend percentages).
  3. `_render_price_sheet_body` driven directly via
     `AppTest.from_function` with a synthetic marginals/CI dict — fast and
     deterministic, no character/sim required, mirrors
     `test_run_identity_surface.py`'s `_script()` pattern. Asserts on the
     new render ORDER (eHP gloss first) and the hand-built HTML table
     (this module no longer uses `st.dataframe` — see the module
     docstring's "2026-07-10 readability restructure" section for why).
  4. A full-app smoke (mirrors `test_enchant_panel_app.py`'s
     `_warrior_state()` fixture) confirming the `gear_surface.py` wiring
     renders the new expander without breaking the existing Gear-tab
     AppTest flow.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from simf.core.survivability_weights import PERTURBED_STATS
from simf.optimizer.per_dungeon import _score_for_school_mix
from simf.optimizer.stat_mechanisms import NOT_MODELED_GLOSS
from simf.ui.helpers.ehp_gloss import EHP_GLOSS
from simf.ui.helpers.stat_price_sheet import (
    NoCiReason,
    compute_school_mix_blend,
    compute_stat_price_rows,
)
from simf.ui.marginals import marginal_noise_basis_label

APP_PATH = Path(__file__).parent.parent / "src" / "simf" / "ui" / "app.py"


# ─── compute_stat_price_rows — pure unit tests ─────────────────────────────


def _fake_marginals() -> dict:
    return {
        stat: {"p": float(i + 1) * 10, "m": float(i + 1) * 5}
        for i, stat in enumerate(PERTURBED_STATS)
    }


def test_one_row_per_perturbed_stat_no_ci():
    marginals = _fake_marginals()
    rows = compute_stat_price_rows(marginals, None)
    assert [r.stat for r in rows] == list(PERTURBED_STATS)
    assert len(rows) == len(PERTURBED_STATS)
    # No CI dict at all -> every cell is None, never a fabricated bound.
    assert all(r.ci_p is None and r.ci_m is None for r in rows)
    # No class_spec passed -> no mechanism catalogue to consult, so every
    # row's mechanism stays None rather than guessing.
    assert all(r.mechanism is None for r in rows)


def test_ci_carried_through_only_for_real_entries():
    marginals = _fake_marginals()
    ci = {stat: {"p": None, "m": None} for stat in PERTURBED_STATS}
    ci["stamina"]["p"] = (65.0, 75.0)
    # armor_from_gear's magic side is physics-pinned to 0 with no CI —
    # leave it None to confirm a per-cell (not just per-stat) None survives.
    ci["armor_from_gear"]["p"] = (18.0, 24.0)

    rows = compute_stat_price_rows(marginals, ci)
    by_stat = {r.stat: r for r in rows}

    assert by_stat["stamina"].ci_p == (65.0, 75.0)
    assert by_stat["stamina"].ci_m is None
    assert by_stat["armor_from_gear"].ci_p == (18.0, 24.0)
    assert by_stat["armor_from_gear"].ci_m is None
    # Every stat with no CI entry at all keeps both sides None.
    assert by_stat["haste_rating"].ci_p is None
    assert by_stat["haste_rating"].ci_m is None


def test_no_ci_reason_distinguishes_exact_anchor_physics_pin_and_unmeasured():
    """The correctness fix this restructure ships: a bare CI-less cell used
    to render identically whether it was Stamina's exact closed-form
    anchor, armor's physics-pinned magic column, or a genuinely-unmeasured
    stat. All three must now carry a DIFFERENT `NoCiReason`."""
    marginals = _fake_marginals()
    ci = {stat: {"p": None, "m": None} for stat in PERTURBED_STATS}
    ci["stamina"]["p"] = (65.0, 75.0)  # a real range needs no reason at all

    rows = compute_stat_price_rows(marginals, ci)
    by_stat = {r.stat: r for r in rows}

    # Stamina: physical side has a real range (no reason needed); magic
    # side has none because it's the exact closed-form anchor.
    assert by_stat["stamina"].no_ci_reason_p is None
    assert by_stat["stamina"].no_ci_reason_m == NoCiReason.EXACT_ANCHOR

    # Armor's magic column is None for a totally different reason —
    # physics, not "we didn't get to it."
    assert by_stat["armor_from_gear"].no_ci_reason_m == NoCiReason.PHYSICS_PIN
    # Armor's physical column has no CI in this fixture either, and it
    # is NOT stamina and NOT the armor/magic pin, so it must read as
    # genuinely unmeasured, not silently inherit either special case.
    assert by_stat["armor_from_gear"].no_ci_reason_p == NoCiReason.NOT_MEASURED

    # Every other stat/school with no CI reads as "not measured this
    # session" — the only one of the three that means lower confidence.
    assert by_stat["haste_rating"].no_ci_reason_p == NoCiReason.NOT_MEASURED
    assert by_stat["haste_rating"].no_ci_reason_m == NoCiReason.NOT_MEASURED
    assert by_stat["crit_rating"].no_ci_reason_p == NoCiReason.NOT_MEASURED


def test_missing_stat_in_marginals_defaults_to_zero_not_a_crash():
    # A stat absent from `marginals` (e.g. an old cached dict from before a
    # new stat was added) should default to 0.0, not raise.
    marginals = {"stamina": {"p": 70.0, "m": 28.0}}
    rows = compute_stat_price_rows(marginals, None)
    by_stat = {r.stat: r for r in rows}
    assert by_stat["stamina"].ehp_p == 70.0
    assert by_stat["haste_rating"].ehp_p == 0.0
    assert by_stat["haste_rating"].ehp_m == 0.0


def test_mechanism_threads_through_for_a_real_priced_stat():
    marginals = {"stamina": {"p": 70.0, "m": 28.0}}
    rows = compute_stat_price_rows(marginals, None, class_spec="protection_warrior")
    by_stat = {r.stat: r for r in rows}
    assert by_stat["stamina"].mechanism == (
        "Stamina raises your maximum health directly, so you can take more "
        "total damage before you die."
    )


def test_mechanism_is_the_not_modeled_sentinel_for_a_structurally_unpriced_stat():
    """Agility has NO survival credit anywhere in the Warrior model — its
    `stat_mechanisms.yaml` entry is the literal sentinel, returned
    UNCONDITIONALLY regardless of the (always-zero) marginal. This is what
    disambiguates a real +0 (a mechanism that's just gated off this build)
    from a stat that structurally can never be priced at all — the same
    column resolves both, per the brief."""
    marginals = {"agility": {"p": 0.0, "m": 0.0}}
    rows = compute_stat_price_rows(marginals, None, class_spec="protection_warrior")
    by_stat = {r.stat: r for r in rows}
    assert by_stat["agility"].mechanism == NOT_MODELED_GLOSS


def test_mechanism_is_none_when_a_real_mechanism_is_gated_off_by_zero_marginal():
    # Crit has a REAL (non-sentinel) mechanism for Warrior (Brutal
    # Vitality), but it's None here because the live marginal is zero for
    # this build — never silently claim a mechanism that isn't
    # contributing today.
    marginals = {"crit_rating": {"p": 0.0, "m": 0.0}}
    rows = compute_stat_price_rows(marginals, None, class_spec="protection_warrior")
    by_stat = {r.stat: r for r in rows}
    assert by_stat["crit_rating"].mechanism is None


# ─── compute_school_mix_blend — pure unit tests ────────────────────────────


def test_school_mix_blend_averages_physical_fraction_equally_per_dungeon():
    dungeons = [
        {"id": "a", "school_mix": {"physical": 0.8}},
        {"id": "b", "school_mix": {"physical": 0.4}},
    ]
    phys, mag = compute_school_mix_blend(dungeons)
    assert phys == pytest.approx(0.6)
    assert mag == pytest.approx(0.4)


def test_school_mix_blend_empty_dungeon_list_returns_none():
    assert compute_school_mix_blend([]) is None


def test_school_mix_blend_missing_school_mix_defaults_to_zero_physical():
    # A dungeon dict with no `school_mix` key at all (or an empty one)
    # counts as 0% physical / 100% magic for that dungeon — the same
    # `.get("physical", 0.0)` default `_score_for_school_mix` uses, so a
    # dungeon missing the key never silently drops out of the average.
    dungeons = [{"id": "a"}, {"id": "b", "school_mix": {"physical": 1.0}}]
    phys, mag = compute_school_mix_blend(dungeons)
    assert phys == pytest.approx(0.5)
    assert mag == pytest.approx(0.5)


def test_school_mix_blend_matches_per_dungeon_average():
    """Proves the disclosure's reproducibility claim is literally true:
    blending a swap's price-sheet numbers by the AVERAGED school_mix
    fraction gives the exact same ΔeHP a caller gets from averaging the
    REAL per-dungeon blended scores (`_score_for_school_mix`, what
    `score_item_across_dungeons`/`average_composition_terms` actually
    compute for every gear/gem/enchant card). A naive "just average the
    two displayed eHP/point numbers with equal 50/50 weight"
    implementation — the mistake this test is designed to catch — would
    pass the two simpler tests above but fail this one whenever the
    dungeon mix isn't already 50/50.
    """
    marginals = {
        "stamina": {"p": 30.0, "m": 22.0},
        "versatility_rating": {"p": 12.0, "m": 9.0},
    }
    delta = {"stamina": 400, "versatility_rating": -150}
    dungeons = [
        {"id": "a", "school_mix": {"physical": 0.9}},
        {"id": "b", "school_mix": {"physical": 0.3}},
        {"id": "c", "school_mix": {"physical": 0.55}},
    ]

    real_avg = sum(
        _score_for_school_mix(delta, marginals, d["school_mix"]) for d in dungeons
    ) / len(dungeons)

    phys_pct, mag_pct = compute_school_mix_blend(dungeons)
    d_phys = sum(marginals[k]["p"] * v for k, v in delta.items())
    d_mag = sum(marginals[k]["m"] * v for k, v in delta.items())
    reconstructed = phys_pct * d_phys + mag_pct * d_mag

    assert reconstructed == pytest.approx(real_avg)


# ─── _render_price_sheet_body — direct AppTest.from_function ──────────────


def _script(
    marginals: dict,
    ci: dict | None,
    fallback_reason: str | None = None,
    dungeons: list[dict] | None = None,
    class_spec: str | None = None,
) -> None:
    from simf.ui.helpers.stat_price_sheet import _render_price_sheet_body

    _render_price_sheet_body(marginals, ci, fallback_reason, dungeons, class_spec)


def _run_body(
    marginals: dict,
    ci: dict | None,
    fallback_reason: str | None = None,
    dungeons: list[dict] | None = None,
    class_spec: str | None = None,
) -> AppTest:
    # `AppTest.from_function` re-execs the function's SOURCE in an isolated
    # module — it does NOT capture an enclosing closure, so the
    # marginals/ci/fallback_reason/dungeons/class_spec arguments must travel
    # through the documented `kwargs=` channel, not a closed-over local.
    at = AppTest.from_function(
        _script,
        kwargs={
            "marginals": marginals,
            "ci": ci,
            "fallback_reason": fallback_reason,
            "dungeons": dungeons,
            "class_spec": class_spec,
        },
    )
    at.run()
    assert not at.exception, f"Unhandled exception: {at.exception}"
    return at


def test_expander_contains_one_html_table_with_every_priced_stat():
    at = _run_body(_fake_marginals(), None)
    expanders = list(at.expander)
    assert len(expanders) == 1
    markdown_blocks = [m.value for m in at.markdown]
    assert len(markdown_blocks) == 1
    table_html = markdown_blocks[0]
    assert "<table" in table_html
    for label in (
        "Stamina",
        "Armor",
        "Haste",
        "Crit",
        "Mastery",
        "Versatility",
        "Strength",
        "Agility",
    ):
        assert label in table_html
    # Column headers name the units clearly, with the CI unit factored out
    # of every cell into its own adjacent column.
    assert "eHP / point — physical" in table_html
    assert "Range (95%) — physical" in table_html
    assert "eHP / point — magic" in table_html
    assert "Range (95%) — magic" in table_html
    assert "Why it helps" in table_html


def test_point_estimate_cell_has_no_redundant_ehp_unit():
    # Stamina's fake physical marginal is 10.0 -> "+10", never "+10 eHP"
    # now that "eHP" lives in the column header instead.
    at = _run_body(_fake_marginals(), None)
    table_html = at.markdown[0].value
    assert "+10 eHP" not in table_html
    assert ">+10<" in table_html


def test_range_cell_shows_absolute_bounds_when_a_real_ci_is_present():
    marginals = _fake_marginals()
    ci = {stat: {"p": None, "m": None} for stat in PERTURBED_STATS}
    ci["stamina"]["p"] = (65.0, 75.0)
    at = _run_body(marginals, ci)
    table_html = at.markdown[0].value
    # Absolute endpoints, not a relative "±" summary — needed so two
    # different stats' ranges can be visually compared for overlap without
    # the reader doing arithmetic (the CI legend's explicit action clause).
    assert "+65 to +75" in table_html


def test_no_ci_reasons_render_distinctly_in_the_table():
    """The three structural no-CI cases must never look the same — the
    highest-confidence correctness finding from the readability workshop."""
    marginals = _fake_marginals()
    at = _run_body(marginals, None)
    table_html = at.markdown[0].value
    # Stamina: exact closed-form anchor, both schools (no CI dict at all).
    assert "exact — anchor" in table_html
    # Armor's magic column: a physics pin, not "we didn't measure it."
    assert "n/a — armor doesn't reduce magic" in table_html
    # Every other CI-less cell: genuinely unmeasured this session.
    assert "not measured this session" in table_html


def test_mechanism_column_renders_glosses_for_a_real_spec():
    marginals = {"stamina": {"p": 70.0, "m": 28.0}, "agility": {"p": 0.0, "m": 0.0}}
    at = _run_body(marginals, None, class_spec="protection_warrior")
    table_html = at.markdown[0].value
    assert "Stamina raises your maximum health directly" in table_html
    assert NOT_MODELED_GLOSS in table_html


def test_render_order_ehp_gloss_is_the_first_caption():
    """Step 1 of the restructure: the eHP definition must render BEFORE
    the table (and before every other caption), since "eHP" is the unit on
    every column header and cell."""
    at = _run_body(_fake_marginals(), None)
    captions = [c.value for c in at.caption]
    assert captions[0] == EHP_GLOSS


def test_ehp_gloss_is_spec_aware_in_price_sheet():
    """Regression test: the price sheet used to always embed the plain
    `EHP_GLOSS` constant, which named Protection Warrior's own always-on
    passive ("Defensive Stance") and excluded cooldowns ("Shield Block/
    Shield Wall") no matter which spec's Gear tab it rendered on. Passing
    `class_spec="guardian_druid"` must now render Guardian's own examples
    and must NOT leak the Warrior-only wording."""
    at = _run_body(_fake_marginals(), None, class_spec="guardian_druid")
    captions = " ".join(c.value for c in at.caption)
    assert "Thick Hide" in captions
    assert "Ironfur" in captions
    assert "Defensive Stance" not in captions
    assert "Shield Block" not in captions


def test_bridge_caption_resolves_the_gloss_vs_table_contradiction():
    """Step 8: moving the eHP gloss to the top makes a real contradiction
    more visible (the gloss excludes per-hit rolls/cooldowns; Mastery/
    Haste/Crit price nonzero BECAUSE they work through those same rolls/
    cooldowns) — a bridging sentence must be present to resolve it, and it
    must appear right after the gloss, before the table."""
    at = _run_body(_fake_marginals(), None)
    captions = [c.value for c in at.caption]
    assert captions[0] == EHP_GLOSS
    assert "eHP-EQUIVALENTS" in captions[1]
    assert "Stamina anchor" in captions[1]


def test_intro_lede_is_short_and_points_at_the_calibration_chip():
    at = _run_body(_fake_marginals(), None)
    captions = " ".join(c.value for c in at.caption)
    assert "exchange rate" in captions
    # Points at the calibration chip by position, not a renamable label —
    # the "Model calibration" badge row this used to name was deleted
    # 2026-07-17 (folded into the run-config strip's single chip, see
    # app.py:1859-1861), so a caption naming it was a dead pointer.
    assert "calibration chip at the top of the page" in captions
    # The old ~180-word intro's arithmetic recipe must NOT still be loose
    # in the always-shown captions — it's been demoted into the nested
    # disclosure inside the table markdown.
    assert "physical price ×" not in captions


def test_ci_legend_hits_the_four_beats():
    at = _run_body(_fake_marginals(), None)
    captions = " ".join(c.value for c in at.caption)
    # Number: what "Range (95%)" actually is.
    assert "sim's own sampling noise" in captions
    assert "19 times out of 20" in captions
    # Confidence: the plain-English heuristic for wide vs. narrow.
    assert "noisier price (trust it less)" in captions
    assert "firmer" in captions
    # Cause + scope: this is sampling noise ONLY, distinct from model error.
    # Points at the calibration popover by position/function, not a
    # renamable "trust banner" label.
    assert "model-error figure in that same calibration popover" in captions
    # Action: what to do when two stats' ranges overlap.
    assert "treat their prices as tied" in captions


def test_fallback_reason_adds_a_closed_form_caption():
    at_no_fallback = _run_body(_fake_marginals(), None, fallback_reason=None)
    captions_clean = " ".join(c.value for c in at_no_fallback.caption)
    assert "closed-form formula" not in captions_clean

    at_fallback = _run_body(_fake_marginals(), None, fallback_reason="sim threw ValueError: x")
    captions_fallback = " ".join(c.value for c in at_fallback.caption)
    assert "closed-form formula" in captions_fallback


def test_basis_caption_reuses_marginal_noise_basis_label_verbatim():
    """The reproducibility-basis line must reuse `marginal_noise_basis_
    label()` verbatim, never re-derive/re-hardcode the iteration/resample/
    seed numbers it names — a caller quoting its own copy could silently
    drift from what was actually computed."""
    at = _run_body(_fake_marginals(), None)
    captions = " ".join(c.value for c in at.caption)
    assert marginal_noise_basis_label() in captions


def test_math_disclosure_is_nested_not_a_second_top_level_expander():
    # Streamlit forbids nesting a real st.expander inside another, so the
    # skeptical-math disclosure must be a plain <details> living inside the
    # SAME markdown blob as the table, not a second st.expander element.
    at = _run_body(_fake_marginals(), None)
    assert len(list(at.expander)) == 1
    table_html = at.markdown[0].value
    assert "<details" in table_html
    assert "Show the exact arithmetic" in table_html


def test_math_disclosure_names_school_mix_weight_when_dungeons_given():
    dungeons = [
        {"id": "a", "school_mix": {"physical": 0.8}},
        {"id": "b", "school_mix": {"physical": 0.4}},
    ]
    at = _run_body(_fake_marginals(), None, dungeons=dungeons)
    table_html = at.markdown[0].value
    assert "60% physical" in table_html
    assert "40% magic" in table_html
    # The trinket carve-out must always ride along with the reproducibility
    # claim — trinkets never reproduce from this table at all.
    assert "trinket" in table_html.lower()
    assert "proc/on-use effect registry" in table_html


def test_math_disclosure_degrades_without_crashing_when_no_dungeons_given():
    # `dungeons=None` (the default) must not crash and must not fabricate a
    # blend percentage it doesn't actually have.
    at = _run_body(_fake_marginals(), None)
    table_html = at.markdown[0].value
    assert "% physical" not in table_html
    assert "blend the two eHP" in table_html


# ─── gear_surface.py wiring — full-app smoke ───────────────────────────────


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


def test_gear_tab_renders_price_sheet_expander_without_breaking(app):
    app.session_state["char_data"] = _warrior_state()
    app.session_state["simc_equipped"] = {}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {}
    app.session_state["view"] = "gear"

    app.run()

    assert not app.exception, f"Unhandled exception: {app.exception}"
    expander_labels = [e.label for e in app.expander]
    assert "How the engine prices your stats" in expander_labels
    # The rest of the Gear tab still rendered — pick one thing every other
    # gear test asserts on (the paperdoll's dark-sheet wrapper) so a
    # regression that silently blanked the tab would fail here too.
    markdown_blocks = [md.value for md in app.markdown]
    assert any("gear-sheet-grid" in v or "gear-col" in v for v in markdown_blocks), (
        "Gear paperdoll grid missing — gear_surface.py wiring may have broken the tab"
    )
