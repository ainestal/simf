"""Per-dungeon ΔeHP scoring for vault rows and slot-click panels.

Built on the analytical eHP marginals path used by the vault joint optimizer:
ΔeHP = Σ_stat (∂eHP/∂stat × delta_stat) × school_weight. No Monte Carlo sim
needed — sub-millisecond even across 9 vault items × 7 dungeons.

The verdict_sentence helper turns a list of DungeonScore into the three-line
headline the v0.9 plan calls for:

    "Take the Helm. Wins WR/FG (+420 eHP), sidegrade Algeth'ar."

Distinct from `vault_joint_optimizer.py`, which is about pairing vault picks
with bag swaps. This module scores a single item across dungeons.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, overload

SIDEGRADE_RATIO = 0.10  # worst-dungeon ΔeHP within 10% of best → flag as sidegrade

# Shape of the optional per-stat composition breakdown `_score_for_school_mix`
# (and its siblings in item_upgrade/gem_suggester/enchant_suggester) can
# return alongside the existing scalar. One entry per stat key present in the
# input delta: "p"/"m" are that stat's raw physical/magic contribution (before
# the school_mix weighting), "blended" is what actually lands in the scalar
# total for that stat. Σ terms[stat]["blended"] == the scalar, by construction
# (same arithmetic, just not summed yet) — see test_score_composition_terms.py.
CompositionTerms = dict[str, dict[str, float]]

# Display rounding for user-visible ΔeHP numbers. Monte Carlo wobble of
# tens of eHP across reruns of the same item ("+820 / +660 / +711 for the
# same headline") looked like a precision claim that the underlying score
# couldn't support (ui-critic #4, 2026-05-16). Bucket the displayed
# headline numbers to the nearest 50 so the answer sentence stays stable
# inside one variance band. The raw scores stored on `DungeonScore.delta_ehp`
# and `VaultRow.avg_delta_ehp` are untouched — tests check those.
_DISPLAY_BUCKET_EHP = 50


def _bucket_ehp(value: float) -> int:
    """Round ``value`` toward zero-anchored buckets of ``_DISPLAY_BUCKET_EHP``.

    Preserves sign and clamps positives away from 0 so a +20 eHP swap doesn't
    read as "+0 eHP" (worse than the wobble it's trying to mute). Symmetric
    for negatives so the weakest-dungeon callout doesn't collapse a real
    -30 eHP loss to 0.
    """
    if value == 0:
        return 0
    sign = 1 if value > 0 else -1
    magnitude = round(abs(value) / _DISPLAY_BUCKET_EHP) * _DISPLAY_BUCKET_EHP
    if magnitude == 0:
        magnitude = _DISPLAY_BUCKET_EHP  # don't collapse a real ±N into "0"
    return sign * magnitude


@dataclass(frozen=True)
class DungeonScore:
    dungeon_id: str
    abbrev: str
    delta_ehp: float
    name: str = ""
    school_mix: dict[str, float] = field(default_factory=dict)


def _stat_delta(new: dict[str, int] | None, equipped: dict[str, int] | None) -> dict[str, int]:
    n = new or {}
    e = equipped or {}
    keys = set(n) | set(e)
    return {k: n.get(k, 0) - e.get(k, 0) for k in keys if n.get(k, 0) != e.get(k, 0)}


@overload
def _score_for_school_mix(
    delta: dict[str, int],
    marginals: dict,
    school_mix: dict[str, float],
    *,
    return_terms: Literal[False] = False,
) -> float: ...


@overload
def _score_for_school_mix(
    delta: dict[str, int],
    marginals: dict,
    school_mix: dict[str, float],
    *,
    return_terms: Literal[True],
) -> tuple[float, CompositionTerms]: ...


def _score_for_school_mix(
    delta: dict[str, int],
    marginals: dict,
    school_mix: dict[str, float],
    *,
    return_terms: bool = False,
) -> float | tuple[float, CompositionTerms]:
    """ΔeHP for one school mix — school_mix-weighted dot product of ``delta``
    against ``marginals``.

    With ``return_terms=True``, also returns the per-stat breakdown that
    composes the scalar (see ``CompositionTerms``): ``terms[stat]["blended"]``
    summed over every stat equals the returned scalar exactly, since it's the
    identical arithmetic just not summed yet. Default behaviour (scalar-only)
    is byte-for-byte unchanged for existing callers.
    """
    phys = float(school_mix.get("physical", 0.0))
    mag = 1.0 - phys
    if not return_terms:
        d_phys = sum(marginals.get(k, {"p": 0.0})["p"] * v for k, v in delta.items())
        d_mag = sum(marginals.get(k, {"m": 0.0})["m"] * v for k, v in delta.items())
        return phys * d_phys + mag * d_mag

    terms: CompositionTerms = {}
    total = 0.0
    for k, v in delta.items():
        p_contrib = marginals.get(k, {"p": 0.0})["p"] * v
        m_contrib = marginals.get(k, {"m": 0.0})["m"] * v
        blended = phys * p_contrib + mag * m_contrib
        terms[k] = {"p": p_contrib, "m": m_contrib, "blended": blended}
        total += blended
    return total, terms


_ZERO_TERM = {"p": 0.0, "m": 0.0, "blended": 0.0}


def average_composition_terms(
    pairs: list[tuple[float, CompositionTerms]],
) -> tuple[float, CompositionTerms]:
    """Average a list of ``(scalar, terms)`` pairs — one per dungeon — into a
    single ``(scalar, terms)`` pair.

    Shared by callers (gem/enchant suggesters) that average
    ``_score_for_school_mix``'s per-dungeon output across a set of dungeons'
    school mixes. Averaging is linear, so the terms-sum-to-scalar invariant
    still holds on the averaged output.
    """
    if not pairs:
        return 0.0, {}
    n = len(pairs)
    avg_score = sum(score for score, _ in pairs) / n
    stat_keys: set[str] = set()
    for _, terms in pairs:
        stat_keys.update(terms.keys())
    avg_terms: CompositionTerms = {}
    for k in stat_keys:
        avg_terms[k] = {
            field_name: sum(terms.get(k, _ZERO_TERM)[field_name] for _, terms in pairs) / n
            for field_name in ("p", "m", "blended")
        }
    return avg_score, avg_terms


@overload
def score_item_across_dungeons(
    new_stats: dict[str, int] | None,
    equipped_stats: dict[str, int] | None,
    marginals: dict,
    dungeons: list[dict],
    *,
    return_terms: Literal[False] = False,
) -> list[DungeonScore]: ...


@overload
def score_item_across_dungeons(
    new_stats: dict[str, int] | None,
    equipped_stats: dict[str, int] | None,
    marginals: dict,
    dungeons: list[dict],
    *,
    return_terms: Literal[True],
) -> tuple[list[DungeonScore], list[CompositionTerms]]: ...


def score_item_across_dungeons(
    new_stats: dict[str, int] | None,
    equipped_stats: dict[str, int] | None,
    marginals: dict,
    dungeons: list[dict],
    *,
    return_terms: bool = False,
) -> list[DungeonScore] | tuple[list[DungeonScore], list[CompositionTerms]]:
    """Score a single item swap (new replaces equipped in same slot) across
    every dungeon. Returns scores in input dungeon order.

    Args:
        new_stats: candidate item's stats, e.g. {"stamina": 1000, "armor": 200}.
        equipped_stats: currently-equipped baseline (None or {} → zero).
        marginals: ehp_marginals(char) output — {stat: {"p": ..., "m": ...}}.
        dungeons: list of {"id": ..., "abbrev": ..., "school_mix": {...}}.

    With ``return_terms=True``, also returns one ``CompositionTerms`` per
    dungeon (same order as the returned scores) — ``terms[i]`` sums exactly
    to ``scores[i].delta_ehp``, since it's the identical arithmetic just not
    summed yet (see ``_score_for_school_mix``). Default behaviour
    (scores-only) is byte-for-byte unchanged for existing callers.
    """
    delta = _stat_delta(new_stats, equipped_stats)
    if not return_terms:
        return [
            DungeonScore(
                dungeon_id=d["id"],
                abbrev=d.get("abbrev", d["id"]),
                delta_ehp=_score_for_school_mix(delta, marginals, d.get("school_mix") or {}),
                name=d.get("name") or d.get("abbrev", d["id"]),
                school_mix=dict(d.get("school_mix") or {}),
            )
            for d in dungeons
        ]

    scores: list[DungeonScore] = []
    terms_by_dungeon: list[CompositionTerms] = []
    for d in dungeons:
        score, terms = _score_for_school_mix(
            delta, marginals, d.get("school_mix") or {}, return_terms=True
        )
        scores.append(
            DungeonScore(
                dungeon_id=d["id"],
                abbrev=d.get("abbrev", d["id"]),
                delta_ehp=score,
                name=d.get("name") or d.get("abbrev", d["id"]),
                school_mix=dict(d.get("school_mix") or {}),
            )
        )
        terms_by_dungeon.append(terms)
    return scores, terms_by_dungeon


def trinket_swap_per_dungeon(
    equipped_spec,
    candidate_spec,
    char,
    dungeons: list[dict],
) -> tuple[list[DungeonScore], bool] | None:
    """Per-dungeon ΔeHP for a trinket swap using `optimizer/trinket_db`'s
    on-use / proc-aware contribution model rather than raw passive stats.

    Returns ``(scores, both_known)`` or ``None`` when neither item is in
    the registry (caller should fall back to stats-only with ⚠️).

    `both_known` is True only when BOTH the equipped trinket and the
    candidate are in the registry — the comparison is fully modeled and
    the UI can drop its stats-only warning.
    """
    from .trinket_db import find_trinket_by_id, trinket_ehp_contribution

    eq_t = find_trinket_by_id(getattr(equipped_spec, "item_id", 0)) if equipped_spec else None
    cand_t = find_trinket_by_id(getattr(candidate_spec, "item_id", 0))
    if cand_t is None:
        return None  # candidate unknown — stats-only fallback

    if eq_t is not None:
        eq = trinket_ehp_contribution(eq_t, char)
        eq_phys, eq_mag = eq["delta_ehp_physical"], eq["delta_ehp_magic"]
    else:
        # Equipped slot empty or unknown — compare candidate vs no-trinket.
        eq_phys, eq_mag = 0.0, 0.0
    cand = trinket_ehp_contribution(cand_t, char)
    d_phys = cand["delta_ehp_physical"] - eq_phys
    d_mag = cand["delta_ehp_magic"] - eq_mag

    scores: list[DungeonScore] = []
    for d in dungeons:
        sm = d.get("school_mix") or {}
        phys = float(sm.get("physical", 0.0))
        mag = 1.0 - phys
        scores.append(
            DungeonScore(
                dungeon_id=d["id"],
                abbrev=d.get("abbrev", d["id"]),
                delta_ehp=phys * d_phys + mag * d_mag,
                name=d.get("name") or d.get("abbrev", d["id"]),
                school_mix=dict(sm),
            )
        )
    return scores, eq_t is not None


def weakest_dungeon_line(scores: list[DungeonScore]) -> str:
    """One-line callout for the dungeon where the swap is weakest. Empty
    string when there's nothing to flag (the swap helps everywhere, or no
    scores).

    Brutoh feedback (2026-05-16): "the verdict tells me where it wins,
    but I'm prog'ing the dungeon it might lose in — show me the worst
    case without expanding."
    """
    if not scores:
        return ""
    worst = min(scores, key=lambda s: s.delta_ehp)
    if worst.delta_ehp >= 0:
        return ""  # nothing to flag — every dungeon is an upgrade
    return f"Weakest: {worst.abbrev} {_bucket_ehp(worst.delta_ehp):,} eHP."


def coverage_sentence(scores: list[DungeonScore]) -> str:
    """One-line summary across the user's prog dungeons.

    Renders as a sub-headline under the main verdict (".Wins WR (+420),
    Floodgate (+380)"). Where the main verdict names the top two
    dungeons, the coverage line tells the user how many dungeons the
    swap wins overall and gives the eHP range.

    Examples:
        "Wins all 7 prog dungeons by +456 to +820 eHP."
        "Wins 5 of 7 prog dungeons (+120 to +820 eHP) · sidegrades 2."
        "" — when the verdict already covers everything (1 or 2 dungeons).

    engaged_tank ask (2026-05-16): "the per-dungeon expander shows Cleaver
    wins all 7, but the verdict only names two. One extra line under the
    verdict that names how many it wins carries the entire decision
    without a click."
    """
    n = len(scores)
    if n < 3:
        # The main verdict already names both dungeons. Nothing to add.
        return ""
    positive = [s for s in scores if s.delta_ehp > 0]
    wins = len(positive)
    if wins == 0:
        return ""
    deltas = sorted(s.delta_ehp for s in positive)
    lo, hi = _bucket_ehp(deltas[0]), _bucket_ehp(deltas[-1])
    if wins == n:
        return f"Wins all {n} prog dungeons by +{lo:,} to +{hi:,} eHP."
    losses = n - wins
    return f"Wins {wins} of {n} prog dungeons (+{lo:,} to +{hi:,} eHP) · sidegrades {losses}."


def verdict_sentence(scores: list[DungeonScore]) -> str:
    """Three-line verdict headline from per-dungeon scores.

    Returns one sentence. Example outputs:
        "Wins WR (+420 eHP), Floodgate (+380), sidegrade Algeth'ar."
        "+250 eHP across all dungeons."
        "No upgrade — equivalent to current."
    """
    if not scores:
        return "No upgrade — equivalent to current."

    by_delta = sorted(scores, key=lambda s: -s.delta_ehp)
    best = by_delta[0]
    worst = by_delta[-1]

    if best.delta_ehp <= 0:
        return "No upgrade — equivalent to current."

    is_sidegrade = worst.delta_ehp <= max(best.delta_ehp * SIDEGRADE_RATIO, 0.0)

    if len(scores) == 1 or not is_sidegrade:
        # Uniform-ish: top two dungeons in winning order
        top = by_delta[:2]
        named = ", ".join(f"{s.abbrev} (+{_bucket_ehp(s.delta_ehp):,} eHP)" for s in top)
        return f"Wins {named}."

    # Mixed: winners + flagged sidegrade
    winners = [s for s in by_delta if s.delta_ehp > best.delta_ehp * SIDEGRADE_RATIO][:2]
    winners_text = ", ".join(f"{s.abbrev} (+{_bucket_ehp(s.delta_ehp):,} eHP)" for s in winners)
    return f"Wins {winners_text}, sidegrade {worst.abbrev}."
