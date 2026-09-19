"""Composition-terms honesty invariant for the ΔeHP dot product.

Every gem/enchant/gear-swap "ΔeHP" number in this codebase is computed as a
linear dot product: Σ_stat (marginal[stat] × delta[stat]), blended across
physical/magic school weights. This suite exercises the optional
``return_terms=True`` path on every function that runs that dot product
(``per_dungeon._score_for_school_mix``, ``item_upgrade._delta_ehp_school_weighted``,
``gem_suggester.gem_survival_value``, ``enchant_suggester.enchant_survival_value``)
and asserts the one thing that must hold by construction: summing the
per-stat ``"blended"`` terms reproduces the existing scalar exactly (within
float tolerance) — same arithmetic, just not summed yet.

Also confirms existing scalar-only callers are completely unaffected: the
sibling test files for these four modules are run unmodified as part of this
workstream's verification (see the PR description), not duplicated here.
"""

from __future__ import annotations

import pytest

from simf.optimizer.enchant_suggester import EnchantSlot, enchant_survival_value, suggest_enchants
from simf.optimizer.gem_suggester import Socket, gem_survival_value, suggest_gems
from simf.optimizer.item_upgrade import _delta_ehp_school_weighted
from simf.optimizer.per_dungeon import _score_for_school_mix, average_composition_terms

# Fixture "character": a physical-heavy plate tank whose marginals cover a
# flat stat (stamina), a secondary rating (versatility), and a sim-derived
# stat that's zero for most specs but real for e.g. an Elune's-Chosen
# Guardian (haste) — the three categories the task calls out.
MARGINALS = {
    "stamina": {"p": 30.0, "m": 22.0},
    "versatility_rating": {"p": 5.0, "m": 4.0},
    "haste_rating": {"p": 12.0, "m": 3.0},
    "armor_from_gear": {"p": 8.0, "m": 0.0},
}

# A handful of deltas covering: single-stat, multi-stat, and a delta that
# includes a stat the marginals dict is silent on (defaults to 0 either side).
DELTAS = [
    {"stamina": 500},
    {"versatility_rating": 120},
    {"haste_rating": 200},
    {"stamina": 500, "versatility_rating": 120, "haste_rating": 200},
    {"stamina": -300, "armor_from_gear": 150},
]

SCHOOL_MIXES = [
    {"physical": 1.0},
    {"physical": 0.0},
    {"physical": 0.65},
]


def _terms_blended_sum(terms: dict[str, dict[str, float]]) -> float:
    return sum(t["blended"] for t in terms.values())


# ── per_dungeon._score_for_school_mix ───────────────────────────────────────


@pytest.mark.parametrize("delta", DELTAS)
@pytest.mark.parametrize("school_mix", SCHOOL_MIXES)
def test_score_for_school_mix_terms_sum_to_scalar(delta, school_mix):
    scalar = _score_for_school_mix(delta, MARGINALS, school_mix)
    scalar_again, terms = _score_for_school_mix(delta, MARGINALS, school_mix, return_terms=True)
    assert scalar_again == pytest.approx(scalar)
    assert _terms_blended_sum(terms) == pytest.approx(scalar)


def test_score_for_school_mix_terms_cover_every_delta_key():
    delta = {"stamina": 500, "versatility_rating": 120, "haste_rating": 200}
    _, terms = _score_for_school_mix(delta, MARGINALS, {"physical": 0.65}, return_terms=True)
    assert set(terms) == set(delta)


def test_score_for_school_mix_default_return_unaffected():
    """The existing scalar-only call path (return_terms defaulted/omitted)
    returns a bare float, not a tuple — existing callers are untouched."""
    delta = {"stamina": 500}
    result = _score_for_school_mix(delta, MARGINALS, {"physical": 0.65})
    assert isinstance(result, float)


# ── item_upgrade._delta_ehp_school_weighted ─────────────────────────────────


@pytest.mark.parametrize("delta", DELTAS)
@pytest.mark.parametrize("school_mix", SCHOOL_MIXES)
def test_delta_ehp_school_weighted_terms_sum_to_scalar(delta, school_mix):
    scalar = _delta_ehp_school_weighted(delta, MARGINALS, school_mix)
    scalar_again, terms = _delta_ehp_school_weighted(
        delta, MARGINALS, school_mix, return_terms=True
    )
    assert scalar_again == pytest.approx(scalar)
    assert _terms_blended_sum(terms) == pytest.approx(scalar)


def test_delta_ehp_school_weighted_matches_per_dungeon_shape():
    """item_upgrade's helper mirrors per_dungeon's exactly — same terms for
    the same inputs, not just the same scalar."""
    delta = {"stamina": 500, "haste_rating": 200}
    school_mix = {"physical": 0.65}
    _, terms_a = _score_for_school_mix(delta, MARGINALS, school_mix, return_terms=True)
    _, terms_b = _delta_ehp_school_weighted(delta, MARGINALS, school_mix, return_terms=True)
    assert set(terms_a) == set(terms_b)
    for stat in terms_a:
        assert terms_a[stat] == pytest.approx(terms_b[stat])


# ── average_composition_terms (shared helper) ───────────────────────────────


def test_average_composition_terms_matches_manual_average():
    delta = {"stamina": 500, "haste_rating": 200}
    mixes = [{"physical": 1.0}, {"physical": 0.0}]
    pairs = [_score_for_school_mix(delta, MARGINALS, mix, return_terms=True) for mix in mixes]
    avg_scalar, avg_terms = average_composition_terms(pairs)
    manual_scalar = sum(s for s, _ in pairs) / len(pairs)
    assert avg_scalar == pytest.approx(manual_scalar)
    assert _terms_blended_sum(avg_terms) == pytest.approx(avg_scalar)
    for stat in delta:
        manual_p = sum(t[stat]["p"] for _, t in pairs) / len(pairs)
        manual_m = sum(t[stat]["m"] for _, t in pairs) / len(pairs)
        assert avg_terms[stat]["p"] == pytest.approx(manual_p)
        assert avg_terms[stat]["m"] == pytest.approx(manual_m)


def test_average_composition_terms_empty_input():
    assert average_composition_terms([]) == (0.0, {})


# ── gem_suggester.gem_survival_value ────────────────────────────────────────


DUNGEONS = [
    {"id": "wr", "abbrev": "WR", "school_mix": {"physical": 0.90}},
    {"id": "np", "abbrev": "NP", "school_mix": {"physical": 0.10}},
]


@pytest.mark.parametrize("delta", DELTAS)
def test_gem_survival_value_terms_sum_to_scalar_multi_dungeon(delta):
    scalar = gem_survival_value(delta, MARGINALS, DUNGEONS)
    scalar_again, terms = gem_survival_value(delta, MARGINALS, DUNGEONS, return_terms=True)
    assert scalar_again == pytest.approx(scalar)
    assert _terms_blended_sum(terms) == pytest.approx(scalar)


def test_gem_survival_value_terms_sum_to_scalar_no_dungeons():
    """Falls back to the generic 0.65/0.35 split; terms must still sum."""
    delta = {"stamina": 500, "versatility_rating": 120}
    scalar = gem_survival_value(delta, MARGINALS, None)
    scalar_again, terms = gem_survival_value(delta, MARGINALS, None, return_terms=True)
    assert scalar_again == pytest.approx(scalar)
    assert _terms_blended_sum(terms) == pytest.approx(scalar)


def test_suggest_gems_best_terms_sum_to_best_value():
    """The winning candidate's `best_terms` (threaded through GemSuggestion)
    sums to `best_value` — the same invariant, now at the suggestion layer."""
    catalog = [
        {
            "id": 1,
            "name": "Versatile Lapis",
            "stats": {"versatility_rating": 17},
            "unique_equipped": False,
        },
        {
            "id": 2,
            "name": "Quick Peridot",
            "stats": {"haste_rating": 17},
            "unique_equipped": False,
        },
    ]
    sockets = [Socket(slot="finger1", index=0, gem_id=None)]
    [suggestion] = suggest_gems(
        sockets,
        MARGINALS,
        class_spec="protection_warrior",
        dungeons=DUNGEONS,
        catalog=catalog,
    )
    assert suggestion.best is not None
    assert suggestion.best_terms  # non-empty: a real winner was chosen
    assert _terms_blended_sum(suggestion.best_terms) == pytest.approx(suggestion.best_value)


def test_suggest_gems_empty_pool_gives_empty_best_terms():
    """No candidates → no winner → best_terms stays the empty default."""
    sockets = [Socket(slot="finger1", index=0, gem_id=None)]
    [suggestion] = suggest_gems(
        sockets,
        MARGINALS,
        class_spec="protection_warrior",
        dungeons=DUNGEONS,
        catalog=[],
    )
    assert suggestion.best is None
    assert suggestion.best_terms == {}


# ── enchant_suggester.enchant_survival_value ────────────────────────────────


@pytest.mark.parametrize("delta", DELTAS)
def test_enchant_survival_value_terms_sum_to_scalar_multi_dungeon(delta):
    scalar = enchant_survival_value(delta, MARGINALS, DUNGEONS)
    scalar_again, terms = enchant_survival_value(delta, MARGINALS, DUNGEONS, return_terms=True)
    assert scalar_again == pytest.approx(scalar)
    assert _terms_blended_sum(terms) == pytest.approx(scalar)


def test_suggest_enchants_best_terms_sum_to_best_value():
    catalog = [
        {
            "id": 101,
            "slot": "chest",
            "name": "Mark of the Worldsoul",
            "stats": {"stamina": 200},
        },
        {
            "id": 102,
            "slot": "chest",
            "name": "Enchant Chest - Haste",
            "stats": {"haste_rating": 50},
        },
    ]
    slots = [EnchantSlot(slot="chest", enchant_id=None)]
    [suggestion] = suggest_enchants(
        slots,
        MARGINALS,
        class_spec="protection_warrior",
        dungeons=DUNGEONS,
        catalog=catalog,
    )
    assert suggestion.best is not None
    assert suggestion.best_terms
    assert _terms_blended_sum(suggestion.best_terms) == pytest.approx(suggestion.best_value)


def test_suggest_enchants_empty_pool_gives_empty_best_terms():
    slots = [EnchantSlot(slot="chest", enchant_id=None)]
    [suggestion] = suggest_enchants(
        slots,
        MARGINALS,
        class_spec="protection_warrior",
        dungeons=DUNGEONS,
        catalog=[],
    )
    assert suggestion.best is None
    assert suggestion.best_terms == {}
