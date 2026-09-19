"""Round-1 review fixes for the eHP-transparency composition line:

1. Largest-remainder rounding — ``composition_parts``'s displayed whole-eHP
   figures must sum EXACTLY to the ΔeHP total the card headline shows, even
   when independently ``{:,.0f}``-formatting each part's own ``blended``
   float would not (a real arithmetic bug, not just a cosmetic one).
2. Leading-term-vs-total reconciliation — when the single largest displayed
   term's own magnitude exceeds the true net total AND some other part is
   folded into the collapsed "+N more" disclosure, ``composition_html`` must
   surface a one-line reconciliation instead of letting the leading number
   read as a silent contradiction of the headline.
3. Mechanism-gloss reframing — the versatility gloss (every spec) and the
   Blood DK mastery gloss must scope their claim to simf's own model
   ("in simf's model: ...") rather than asserting it as a flat in-game fact.
4. Trinket copy — ``TRINKET_REGISTRY_NOTE`` must read in plain player
   language, not name the internal "registry" implementation detail.
"""

from __future__ import annotations

import re

import pytest
import yaml

from simf.optimizer.per_dungeon import CompositionTerms
from simf.optimizer.stat_mechanisms import _STAT_MECHANISMS_FILE, mechanism_tag
from simf.ui.helpers.stat_composition import (
    TRINKET_REGISTRY_NOTE,
    CompositionPart,
    _largest_remainder_round,
    composition_html,
    composition_parts,
)

PW = "protection_warrior"


def _terms(**stat_blended: float) -> CompositionTerms:
    return {stat: {"p": v, "m": 0.0, "blended": v} for stat, v in stat_blended.items()}


def _pw_marginals(**overrides: float) -> dict:
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


def _numbers_with_sign(text: str) -> list[float]:
    """Every ``+``/``-``-prefixed number in ``text`` — the same extraction
    the existing sum-invariant test in test_swap_card_composition_render.py
    uses. A reconciliation phrase must never contribute to this list (see
    ``test_reconciliation_note_text_has_no_sign_prefixed_number_of_its_own``
    below) or it would double-count against the real per-stat parts."""
    return [float(n.replace(",", "")) for n in re.findall(r"[+-][\d,]+(?:\.\d+)?", text)]


# ─── 1. Largest-remainder rounding ─────────────────────────────────────────


def test_largest_remainder_round_matches_target_exactly():
    result = _largest_remainder_round([100.4], 101)
    assert sum(result) == 101


def test_largest_remainder_round_handles_negative_values():
    # target below the naive per-value round() sum -> must subtract, not add
    result = _largest_remainder_round([905.5, 337.5, -120.5, 57.5], 1180)
    assert sum(result) == 1180
    assert len(result) == 4


def test_naive_independent_rounding_would_have_disagreed_with_the_true_total():
    """The concrete round-1 bug, reproduced directly: formatting each part's
    own ``blended`` float with Python's ``{:,.0f}`` (round-half-to-even,
    applied independently per value) does NOT sum to the true ΔeHP total on
    this fixture — the exact failure mode largest-remainder apportionment
    exists to close."""
    terms = _terms(
        stamina=905.5, versatility_rating=337.5, armor_from_gear=-120.5, haste_rating=57.5
    )
    true_total = sum(v["blended"] for v in terms.values())
    assert true_total == pytest.approx(1180.0)

    naive_sum = sum(round(v["blended"]) for v in terms.values())
    assert naive_sum != true_total, (
        "fixture must reproduce the naive-rounding bug (906+338-120+58=1182 != 1180)"
    )

    parts = composition_parts(terms, PW, _pw_marginals())
    assert sum(p.rounded for p in parts) == round(true_total)


def test_composition_parts_rounded_sums_exactly_even_when_a_stat_is_dropped():
    """A stat whose OWN blended value rounds to 0 gets dropped entirely (see
    ``composition_parts``'s existing "no +0 noise" rule) — but its sliver of
    the true total must still land on one of the DISPLAYED parts, or the
    displayed set would fall short of the real headline number."""
    terms = _terms(stamina=900.4, haste_rating=0.4)  # haste drops (rounds to 0)
    true_total = sum(v["blended"] for v in terms.values())  # 900.8
    parts = composition_parts(terms, PW, _pw_marginals())
    assert [p.stat for p in parts] == ["stamina"]
    # Naive: round(900.4) == 900, which is short of round(900.8) == 901 by
    # exactly the dropped haste sliver.
    assert round(900.4) != round(true_total)
    assert parts[0].rounded == round(true_total)


def test_composition_html_numbers_sum_exactly_to_total_with_fractional_parts():
    terms = _terms(
        stamina=905.5, versatility_rating=337.5, armor_from_gear=-120.5, haste_rating=57.5
    )
    true_total = sum(v["blended"] for v in terms.values())
    html = composition_html(terms, PW, _pw_marginals())
    html_no_count = re.sub(r"<summary>\+\d+ more</summary>", "<summary></summary>", html)
    numbers = _numbers_with_sign(html_no_count)
    assert numbers
    assert sum(numbers) == pytest.approx(true_total)


# ─── 2. Leading-term-vs-total reconciliation ───────────────────────────────


def test_reconciliation_note_appears_when_leading_term_dwarfs_the_total():
    """4 stats, max_inline=3 (default): the 3 largest-by-|blended| stats show
    inline, the 4th folds into "+N more". The single leading inline term
    (stamina, +9,200) is individually LARGER than the true net total
    (+8,700) once the folded haste term is counted — the exact "+8,500
    Stamina next to +500 eHP"-shaped complaint from round-1 review. The
    fix must surface a reconciliation instead of leaving that number to look
    like a silent contradiction of the card's own headline."""
    terms = _terms(
        stamina=9200.0,
        versatility_rating=9000.0,
        armor_from_gear=-8000.0,
        haste_rating=-1500.0,
    )
    true_total = sum(v["blended"] for v in terms.values())
    assert true_total == pytest.approx(8700.0)

    html = composition_html(terms, PW, _pw_marginals())
    assert '<details class="gear-card-composition-more">' in html
    assert "Haste" in html  # the offsetting term is still reachable behind the fold
    assert "gear-card-composition-net" in html, (
        f"no reconciliation note rendered for a leading term that dwarfs the total: {html!r}"
    )
    assert "8,700" in html
    # The reconciliation phrase must not itself look like one more
    # sign-prefixed per-stat delta (see test_reconciliation_note_text_has_no_
    # sign_prefixed_number_of_its_own) — cheap sanity check inline too.
    net_line = re.search(r'<div class="gear-card-composition-net[^"]*">(.*?)</div>', html)
    assert net_line
    assert not re.search(r"[+-][\d,]", net_line.group(1))


def test_reconciliation_note_reuses_a_legible_existing_class_not_an_unstyled_one():
    """This module can't touch app.py's CSS. A brand-new class with no
    matching rule anywhere was verified LIVE (computed-style inspection
    against the running dark gear-card sheet) to render at ~1:1 contrast
    against the card background — functionally invisible. The note's div
    must carry "gear-card-composition-more" alongside its own semantic
    class so it inherits that class's already-correct (and dark-theme-
    overridden) muted color in every surface this markup renders on."""
    terms = _terms(
        stamina=9200.0,
        versatility_rating=9000.0,
        armor_from_gear=-8000.0,
        haste_rating=-1500.0,
    )
    html = composition_html(terms, PW, _pw_marginals())
    assert (
        'class="gear-card-composition-net gear-card-composition-more"' in html
        or 'class="gear-card-composition-more gear-card-composition-net"' in html
    )


def test_reconciliation_note_absent_when_leading_term_does_not_dwarf_total():
    """Same "3 inline + N more" shape, but the leading term (stamina, 900)
    is already smaller than the true total (1,770) — nothing is being
    silently hidden in a way that contradicts the headline, so no note."""
    terms = _terms(
        stamina=900.0,
        versatility_rating=400.0,
        armor_from_gear=300.0,
        haste_rating=120.0,
        mastery_rating=50.0,
    )
    html = composition_html(terms, PW, _pw_marginals())
    assert '<details class="gear-card-composition-more">' in html
    assert "gear-card-composition-net" not in html


def test_reconciliation_note_absent_when_everything_is_already_inline():
    """Only 2 stats, both fit inline (no "+N more" at all) — even though one
    term's magnitude exceeds the total, everything needed to verify the net
    is already visible without expanding anything, so no extra note."""
    terms = _terms(stamina=9000.0, armor_from_gear=-8500.0)
    html = composition_html(terms, PW, _pw_marginals())
    assert "<details" not in html
    assert "gear-card-composition-net" not in html


def test_reconciliation_note_text_has_no_sign_prefixed_number_of_its_own():
    """The note must read as prose, never as one more competing delta next
    to the per-stat parts it explains — otherwise a naive number-sum check
    over the whole HTML blob (as done elsewhere) would double-count it."""
    terms = _terms(
        stamina=9200.0,
        versatility_rating=9000.0,
        armor_from_gear=-8000.0,
        haste_rating=-1500.0,
    )
    html = composition_html(terms, PW, _pw_marginals())
    net_line = re.search(r'<div class="gear-card-composition-net[^"]*">(.*?)</div>', html)
    assert net_line
    assert _numbers_with_sign(net_line.group(1)) == []


def test_reconciliation_note_negative_total_reads_as_a_loss():
    terms = _terms(
        stamina=1000.0,
        versatility_rating=900.0,
        armor_from_gear=-9500.0,  # leading by |blended|
        haste_rating=-50.0,
    )
    true_total = sum(v["blended"] for v in terms.values())
    assert true_total < 0
    html = composition_html(terms, PW, _pw_marginals())
    assert "gear-card-composition-net" in html
    assert "loss" in html.lower()


# ─── 3. Mechanism-gloss reframing ──────────────────────────────────────────


def _load_raw() -> dict:
    with _STAT_MECHANISMS_FILE.open() as f:
        return yaml.safe_load(f)


@pytest.mark.parametrize(
    "class_spec",
    [
        "protection_warrior",
        "protection_paladin",
        "blood_death_knight",
        "vengeance_demon_hunter",
        "brewmaster_monk",
        "guardian_druid",
    ],
)
def test_versatility_gloss_scopes_the_claim_to_simfs_model(class_spec):
    gloss = _load_raw()[class_spec]["versatility_rating"]["gloss"]
    assert "simf" in gloss.lower(), (
        f"{class_spec} versatility gloss states model behavior as a flat fact: {gloss!r}"
    )


def test_blood_dk_mastery_gloss_scopes_the_claim_to_simfs_model():
    gloss = _load_raw()["blood_death_knight"]["mastery_rating"]["gloss"]
    assert "simf" in gloss.lower(), (
        f"Blood DK mastery gloss states model behavior as a flat fact: {gloss!r}"
    )


def test_reframed_glosses_still_gate_on_the_live_marginal():
    """The wording changed; the gating logic (mechanism_tag) must not have —
    still None on a zero marginal, still the real gloss on a nonzero one."""
    nonzero = mechanism_tag(
        "protection_warrior", "versatility_rating", {"versatility_rating": {"p": 6.0, "m": 0.0}}
    )
    zero = mechanism_tag(
        "protection_warrior", "versatility_rating", {"versatility_rating": {"p": 0.0, "m": 0.0}}
    )
    assert nonzero is not None and "simf" in nonzero.lower()
    assert zero is None

    dk_nonzero = mechanism_tag(
        "blood_death_knight", "mastery_rating", {"mastery_rating": {"p": 5.0, "m": 0.0}}
    )
    dk_zero = mechanism_tag(
        "blood_death_knight", "mastery_rating", {"mastery_rating": {"p": 0.0, "m": 0.0}}
    )
    assert dk_nonzero is not None and "simf" in dk_nonzero.lower()
    assert dk_zero is None


# ─── 4. Trinket copy ────────────────────────────────────────────────────────


def test_trinket_registry_note_reads_as_plain_player_language():
    lowered = TRINKET_REGISTRY_NOTE.lower()
    assert "registry" not in lowered, (
        f"TRINKET_REGISTRY_NOTE names an internal implementation detail: {TRINKET_REGISTRY_NOTE!r}"
    )
    assert "on-use" in lowered or "proc" in lowered
    assert "stat" in lowered


def test_composition_part_carries_a_rounded_field():
    # Cheap contract check: CompositionPart now has a `rounded` int, not just
    # the raw `blended` float — every consumer must format this, not
    # `blended` directly, for the sum-to-total invariant to hold.
    part = CompositionPart(
        stat="stamina", display_name="Stamina", blended=100.4, rounded=100, mechanism=None
    )
    assert isinstance(part.rounded, int)
