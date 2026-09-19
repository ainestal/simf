"""Per-slot upgrade-impact scorer — "which item should my Voidcore go on?"

The question every tank asks when an upgrade currency (Voidcore, Gilded
Crest, Wyrm Crest, etc.) lands in their bag: *which equipped piece gains
the most survivability if I bump it by N ilvls?* Same math path as the
vault-row scorer in ``per_dungeon.py`` — closed-form
``ΔeHP = Σ_stat (∂eHP/∂stat × Δstat) × school_weight`` against cached
survivability marginals. Sub-millisecond across an entire equipped set.

The "upgrade" model is intentionally simple: linear stat scaling against
ilvl ratio (``new_stat = old_stat × new_ilvl / old_ilvl``). This is the
same convention used by ``optimizer/trinket_db.py:effective_stat_delta``
and is the right v1 — Blizzard's ``Item-Sparse`` curve is close enough
to linear across the Midnight 12.0.5 endgame band (M+/vault/raid land an
item between ~250 and ~298 ilvl this season) that an explicit exponential
adds complexity without changing rank order. The error lives in the
*absolute* eHP magnitude, not the *ordering* — at a common target ilvl
both items scale by the same ratio, so a candidate-vs-equipped comparison
stays correct. If we discover a real reorder vs in-game tooltips, swap in
the exponential here without touching call sites.

Out of scope for v1: tier-set break/make (handled at the vault-swap
layer, not at the per-item upgrade layer), gem/enchant value shifts
(orthogonal), and trinket re-evaluation (trinkets need the registry
pipeline, not stat scaling). The upgrade panel surfaces a warning when
the top candidate is a trinket so the user knows the ranking may
understate the real impact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, overload

from .per_dungeon import CompositionTerms


@dataclass(frozen=True)
class UpgradeRow:
    """One equipped slot's predicted impact from an ilvl upgrade.

    ``delta_ehp`` is the school-mix-weighted survivability gain in eHP
    units. ``delta_dps_pct`` is the predicted DPS impact (positive = more
    DPS), reported as a fraction (0.003 = +0.3%). Both are derived from
    the same closed-form marginals path the vault scorer uses, so they
    are directly comparable to the rest of the UI.
    """

    slot: str
    item_id: int
    item_name: str | None
    current_ilvl: int
    upgraded_ilvl: int
    delta_ehp: float
    delta_dps_pct: float
    is_trinket: bool
    # True when the requested ilvl_delta would have pushed past the season
    # cap and the upgrade was clamped to the cap. UI uses this to label the
    # row "capped at N" so the player isn't told to spend a Voidcore on a
    # slot where the gain is partial.
    is_capped: bool = False
    # Per-stat ΔeHP composition of `delta_ehp` (per_dungeon.CompositionTerms)
    # — sums exactly to `delta_ehp`, including for `is_trinket` rows: unlike
    # the vault/gear-swap trinket path, this module scores every slot
    # (trinkets included) via the same stat-scaling dot product, never the
    # trinket_db registry (see the module docstring's "Out of scope for v1"
    # note) — so a composition here is honest, just subject to the same
    # "stat scaling only" caveat the UI already renders for trinket rows.
    terms: CompositionTerms = field(default_factory=dict)


def scale_stats(stats: dict[str, int], from_ilvl: int, to_ilvl: int) -> dict[str, int]:
    """Scale a stat dict from one ilvl to another by linear ratio.

    Returns integer-rounded stats so the result reads like a real item
    tooltip (no fractional Stamina). A no-op when ``from_ilvl`` ≤ 0 or
    when the ilvls match — the caller decides what to do with an
    unknown-ilvl item, this function refuses to invent one.
    """
    if from_ilvl <= 0 or to_ilvl <= 0 or from_ilvl == to_ilvl:
        return dict(stats)
    scale = to_ilvl / from_ilvl
    return {k: round(v * scale) for k, v in stats.items()}


def _stat_delta(after: dict[str, int], before: dict[str, int]) -> dict[str, int]:
    keys = set(after) | set(before)
    return {
        k: after.get(k, 0) - before.get(k, 0) for k in keys if after.get(k, 0) != before.get(k, 0)
    }


@overload
def _delta_ehp_school_weighted(
    delta: dict[str, int],
    marginals: dict,
    school_mix: dict[str, float],
    *,
    return_terms: Literal[False] = False,
) -> float: ...


@overload
def _delta_ehp_school_weighted(
    delta: dict[str, int],
    marginals: dict,
    school_mix: dict[str, float],
    *,
    return_terms: Literal[True],
) -> tuple[float, CompositionTerms]: ...


def _delta_ehp_school_weighted(
    delta: dict[str, int],
    marginals: dict,
    school_mix: dict[str, float],
    *,
    return_terms: bool = False,
) -> float | tuple[float, CompositionTerms]:
    """ΔeHP across schools, mirroring ``per_dungeon._score_for_school_mix``.

    Marginals have shape ``{stat: {"p": phys_ehp_per_unit, "m": magic_ehp_per_unit}}``.

    With ``return_terms=True``, also returns the per-stat composition
    breakdown (see ``per_dungeon.CompositionTerms``) — identical shape and
    invariant (terms sum to the scalar) as ``_score_for_school_mix``. Default
    behaviour (scalar-only) is unchanged for existing callers.
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


def _aggregate_school_mix(dungeons: list[dict]) -> dict[str, float]:
    """Average school_mix across the selected dungeons (equal weight)."""
    if not dungeons:
        return {"physical": 1.0}
    phys = 0.0
    n = 0
    for d in dungeons:
        sm = d.get("school_mix") or {}
        phys += float(sm.get("physical", 0.0))
        n += 1
    if n == 0:
        return {"physical": 1.0}
    return {"physical": phys / n}


def _delta_dps_fraction(
    delta: dict[str, int],
    dps_stat_weights: dict[str, float],
    base_secondary_pool: float,
) -> float:
    """Translate a stat delta into a DPS fraction via the configured weights.

    ``dps_stat_weights`` is the same dict surfaced in ``constants.yaml``
    (haste 1.40, crit 1.10, mastery 1.05, vers 0.80). We approximate the
    DPS impact as ``Σ weight_i × Δrating_i / base_pool`` where
    ``base_pool`` is the player's total secondary-stat rating pool. Same
    approximation the existing vault scorer uses for ΔDPS — order-of-
    magnitude correct, sufficient for ranking, transparently labelled
    "approx" in the UI.
    """
    if base_secondary_pool <= 0:
        return 0.0
    weight_keys = {
        "haste_rating": "haste",
        "crit_rating": "crit",
        "mastery_rating": "mastery",
        "versatility_rating": "vers",
    }
    total = 0.0
    for stat_key, weight_name in weight_keys.items():
        w = float(dps_stat_weights.get(weight_name, 0.0))
        total += w * float(delta.get(stat_key, 0))
    return total / base_secondary_pool


def compute_upgrade_impact(
    equipped: dict,
    item_stats: dict[str, dict[str, int]],
    item_ilvls: dict[str, int],
    item_names: dict[str, str | None],
    marginals: dict,
    dungeons: list[dict],
    dps_stat_weights: dict[str, float],
    base_secondary_pool: float,
    ilvl_delta: int,
    slot_ceilings: dict[str, int | None] | None = None,
) -> list[UpgradeRow]:
    """Score every equipped slot's impact from a uniform +N ilvl bump.

    Args:
        equipped: ``slot → ItemSpec`` (or dict) — used only for ``item_id``
            attribution so the row can link back to the right tooltip.
        item_stats: ``slot → stat-dict`` resolved from the network/cache.
            Slots missing from this dict are silently skipped (we won't
            invent stats for a parse failure).
        item_ilvls: ``slot → int`` current ilvl. Slots with ``None`` or 0
            ilvl are skipped (we won't extrapolate without a base).
        item_names: ``slot → name`` for display (may be None).
        marginals: survivability marginals from ``_marginals_for(char)``.
        dungeons: selected dungeon dicts (with ``school_mix``).
        dps_stat_weights: ``constants.yaml.dps_stat_weights``.
        base_secondary_pool: total secondary rating on the character.
        ilvl_delta: positive integer — how many ilvls the upgrade adds.
        slot_ceilings: ``slot → reachable ilvl ceiling`` (or ``None`` for
            "no known ceiling for this slot"), e.g. from
            ``ui.helpers.upgrade_compare.category_ceiling`` — armor and
            weapon/trinket categories reach DIFFERENT ceilings in Midnight
            12.0.5 (armor caps where its crest track maxes out; only
            weapons/trinkets can go further via Voidcore), so this must be
            keyed per slot, never a single flat number. A slot already at
            or above its own ceiling is excluded; a slot that would cross
            it is clamped to it and flagged ``is_capped=True``.

    Returns:
        Rows in descending ``delta_ehp`` order. Empty list if no slot is
        eligible (no stats, no ilvls, ``ilvl_delta`` ≤ 0, or every slot is
        already at its own reachable ceiling).
    """
    if ilvl_delta <= 0:
        return []
    school_mix = _aggregate_school_mix(dungeons)
    rows: list[UpgradeRow] = []
    trinket_slots = {"trinket1", "trinket2"}
    for slot, item in equipped.items():
        stats = item_stats.get(slot)
        if not stats:
            continue
        current = item_ilvls.get(slot)
        if not current or current <= 0:
            continue
        ceiling = (slot_ceilings or {}).get(slot)
        if ceiling is not None and current >= ceiling:
            # Already at this slot's own reachable ceiling — no upgrade to
            # project (an armor piece at its crest-track max can't take a
            # Voidcore; it just has nowhere further to go).
            continue
        upgraded = current + ilvl_delta
        is_capped = False
        if ceiling is not None and upgraded > ceiling:
            upgraded = ceiling
            is_capped = True
        upgraded_stats = scale_stats(stats, current, upgraded)
        delta = _stat_delta(upgraded_stats, stats)
        if not delta:
            continue
        d_ehp, d_ehp_terms = _delta_ehp_school_weighted(
            delta, marginals, school_mix, return_terms=True
        )
        d_dps = _delta_dps_fraction(delta, dps_stat_weights, base_secondary_pool)
        item_id = int(
            getattr(item, "item_id", 0) or (item.get("item_id", 0) if isinstance(item, dict) else 0)
        )
        rows.append(
            UpgradeRow(
                slot=slot,
                item_id=item_id,
                item_name=item_names.get(slot),
                current_ilvl=current,
                upgraded_ilvl=upgraded,
                delta_ehp=d_ehp,
                delta_dps_pct=d_dps,
                is_trinket=slot in trinket_slots,
                is_capped=is_capped,
                terms=d_ehp_terms,
            )
        )
    rows.sort(key=lambda r: -r.delta_ehp)
    return rows


@dataclass(frozen=True)
class CurvePoint:
    """One point on a single piece's upgrade curve.

    ``delta_ehp`` is the school-mix-weighted survivability delta at this
    ilvl, measured against whatever baseline the caller asked for
    (``ehp_curve`` docstring). ``is_reachable`` is False when this ilvl
    sits above the piece's reachable ceiling — the UI greys those points
    and never presents them as a real option, since a piece can't be
    upgraded past its track / season cap.
    """

    ilvl: int
    delta_ehp: float
    is_reachable: bool = True


def ehp_curve(
    item_stats: dict[str, int],
    item_base_ilvl: int,
    ilvl_points: list[int],
    marginals: dict,
    dungeons: list[dict],
    *,
    baseline_stats: dict[str, int] | None = None,
    ceiling_ilvl: int | None = None,
) -> list[CurvePoint]:
    """ΔeHP for one piece scaled to each ilvl in ``ilvl_points``.

    Two modes, picked by ``baseline_stats``:

      - ``baseline_stats`` given (e.g. the equipped piece this candidate
        would replace) → each point is *candidate@ilvl − equipped*. The
        ilvl where the curve crosses zero is the break-even: "upgrade
        this vault piece to here and it overtakes what I have."
      - ``baseline_stats=None`` → each point is *piece@ilvl −
        piece@base_ilvl*, i.e. the survivability the player *gains* by
        pouring crests into this exact piece. Anchored at 0 at the base
        ilvl. This is the "what's the impact of crests on this piece?"
        leveling curve.

    ``ceiling_ilvl`` flags points the piece cannot actually reach
    (``is_reachable=False``) without dropping them — the caller decides
    how to render the unreachable tail. Same closed-form, school-weighted
    marginals path as ``compute_upgrade_impact``; no Monte Carlo, no
    network. Returns ``[]`` when the base ilvl or stats are unusable.
    """
    if item_base_ilvl <= 0 or not item_stats:
        return []
    school_mix = _aggregate_school_mix(dungeons)
    base = baseline_stats if baseline_stats is not None else item_stats
    points: list[CurvePoint] = []
    for target in ilvl_points:
        scaled = scale_stats(item_stats, item_base_ilvl, target)
        delta = _stat_delta(scaled, base)
        d_ehp = _delta_ehp_school_weighted(delta, marginals, school_mix)
        reachable = ceiling_ilvl is None or target <= ceiling_ilvl
        points.append(CurvePoint(ilvl=target, delta_ehp=d_ehp, is_reachable=reachable))
    return points
