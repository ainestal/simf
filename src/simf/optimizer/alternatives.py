"""Slot-alternative ranker for the v0.9.1 slot-click dialog.

Given a slot and the user's bag + vault inventory, returns every candidate
item that could go in that slot — ranked by per-dungeon ΔeHP against what
the user currently has equipped. Handles paired slots (finger1/finger2,
trinket1/trinket2) by merging the bag/vault entries for the partner slot.

Any item already equipped in the slot — or, for rings/trinkets, in its
paired slot — is excluded from the alternatives list, even if a copy
appears in the bag dict (some SimC exports double-list, and a lower-ilvl
spare of a trinket you already wear is never a useful swap).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from .item_upgrade import scale_stats
from .per_dungeon import (
    CompositionTerms,
    DungeonScore,
    average_composition_terms,
    score_item_across_dungeons,
)

# PAIRED_SLOTS / equivalent_slots moved to vault_ranking (2026-06-10) so the
# vault ranker can baseline a trinket1 offer against either equipped trinket
# without a circular import (this module already imports vault_ranking).
# Re-exported here because app.py + the loot pipeline import them from this
# module — the redundant-alias form keeps the re-export explicit for ruff.
from .vault_ranking import PAIRED_SLOTS as PAIRED_SLOTS  # isort: skip
from .vault_ranking import compute_delta_dps
from .vault_ranking import equivalent_slots as equivalent_slots


@dataclass
class Alternative:
    item: object  # ItemSpec or compatible
    source: str  # "bag" | "vault"
    per_dungeon: list[DungeonScore]
    avg_delta_ehp: float
    # ΔDPS surfaced alongside ΔeHP so the slot dialog shows the
    # offensive-side trade. Same convention as VaultRow.delta_dps:
    # Σ (Δrating × weight) / 1000 against constants.yaml.dps_stat_weights.
    # 0.0 when dps_weights / item_stats aren't available — caller decides
    # whether to render an "≈0" placeholder or omit the number.
    delta_dps: float = 0.0
    # The ilvl this candidate's stats were *scaled to* before scoring, when
    # the caller asked for an upgrade-normalized comparison (target_ilvl).
    # None means the row is scored as-dropped (today's default) — the UI
    # then reads ``item.ilvl`` for display. A non-None value lets the UI
    # render "shown at ilvl X (up from item.ilvl)" plus the reachability
    # caveat, since a *normalized-up* candidate may not actually reach X on
    # its own upgrade track.
    shown_at_ilvl: int | None = None
    # The stats the row was *scored* with — scaled to ``shown_at_ilvl`` when a
    # target was requested, else the item's as-dropped stats. The UI displays
    # THIS (not a re-fetch), so the shown stat line matches the ilvl label and
    # the ΔeHP. None when item stats couldn't be resolved.
    new_stats: dict[str, int] | None = None
    # Per-stat ΔeHP composition of ``avg_delta_ehp`` (per_dungeon.
    # CompositionTerms), averaged across ``dungeons`` the same way the
    # scalar is — Σ terms[stat]["blended"] == avg_delta_ehp exactly, since
    # both come from the identical per-dungeon arithmetic. Empty dict when
    # there's nothing to score (e.g. no stats resolved). Defaulted so
    # existing ``Alternative(...)`` call sites (tests) don't need updating.
    terms: CompositionTerms = field(default_factory=dict)


def _gather(items_by_slot: dict[str, list], wanted_slots: set[str]) -> Iterable:
    for slot in wanted_slots:
        yield from items_by_slot.get(slot, [])


def _normalized_stats(
    stats: dict[str, int] | None,
    from_ilvl: int | None,
    target_ilvl: int | None,
) -> dict[str, int] | None:
    """Scale ``stats`` to ``target_ilvl`` when an upgrade-normalized
    comparison is requested and we know the item's base ilvl.

    Returns the stats unchanged (today's behaviour) whenever:
      - no ``target_ilvl`` was requested,
      - the item carries no base ilvl to scale *from*, or
      - stats are missing.

    The scaling is applied to the *resolved* stat dict, so the
    network/cache key (``item_id, bonus_ids``) is untouched — we never
    refetch an item at a different ilvl, we rescale what Wowhead already
    gave us. Linear ratio is preserved across stats, so at a common
    target ilvl the candidate-vs-equipped *ranking* is exact; only the
    absolute eHP magnitude carries the linear-curve approximation.
    """
    if not stats or not target_ilvl or not from_ilvl:
        return stats
    return scale_stats(stats, from_ilvl, target_ilvl)


def alternatives_for_slot(
    slot: str,
    equipped: dict[str, object],
    bag: dict[str, list],
    vault: dict[str, list],
    marginals: dict,
    dungeons: list[dict],
    item_stats_fn,
    *,
    extra_sources: list[tuple[str, object]] | None = None,
    dps_weights: dict[str, float] | None = None,
    target_ilvl: int | None = None,
    exclude_two_handed: bool = False,
    item_is_two_handed_fn=None,
) -> list[Alternative]:
    """Rank every candidate item for `slot`, best-first by avg ΔeHP.

    ``exclude_two_handed`` (paired with ``item_is_two_handed_fn``) drops any
    candidate that Wowhead reports as a Two-Hand weapon. Shield specs
    (Protection Warrior/Paladin) can't equip one without unequipping their
    off-hand shield — a defensive loss this slot-scoped ΔeHP compare never
    sees, since it only touches the weapon slot's own stats. An unpaired 2H
    weapon's raw stat budget is much larger than a 1H weapon's, so without
    this filter a 2H candidate always wins the comparison and gets
    recommended as an "upgrade" that would actually strip the character's
    shield. Callers that pass ``exclude_two_handed=True`` must also pass
    ``item_is_two_handed_fn``; a ``None`` verdict from it (fetch failed /
    offline) fails open — the candidate stays in, matching every other
    Wowhead-lookup fallback in this codebase.

    ``extra_sources`` is an optional list of ``(source_label, item)``
    pairs the caller wants to score alongside bag + vault candidates.
    Each item is treated exactly like a bag/vault entry except its
    ``Alternative.source`` is set to the caller-provided label — used
    today by the slot dialog to surface M+ dungeon loot tagged with a
    human-readable dungeon abbreviation (``"m+ AA"``).

    ``dps_weights`` is the same dict surfaced in ``constants.yaml``
    (haste / crit / mastery / vers). When provided, each Alternative
    carries a ``delta_dps`` field — Σ (Δrating × weight) / 1000 — so
    the slot dialog can show ΔDPS alongside ΔeHP. Omitted (default)
    keeps ``delta_dps=0.0`` for callers that don't care.

    ``target_ilvl`` enables an *upgrade-normalized* comparison: both the
    equipped baseline AND every candidate are rescaled to ``target_ilvl``
    before scoring, so the player compares *itemization* rather than the
    raw item-level gap. This is the answer to "everything in my vault is
    below my equipped ilvl, so it all reads as a downgrade — but is the
    vault piece's stat spread actually better if I upgrade it?" Default
    ``None`` preserves today's as-dropped behaviour exactly (and keeps
    ``_per_slot_picks`` + existing tests untouched). Candidates with no
    known base ilvl are scored unscaled and left with ``shown_at_ilvl
    is None`` so the UI can flag them.
    """
    wanted = equivalent_slots(slot)
    current = equipped.get(slot)
    # Exclude every item already worn in this slot OR its paired slot (rings/
    # trinkets pull candidates from both). A copy of a piece you already wear
    # is never useful swap advice: a lower-ilvl spare is strictly worse, and a
    # higher-ilvl copy is "upgrade the one you wear" — the upgrade panel's job,
    # not a swap. For non-paired slots ``wanted == {slot}``, so this stays
    # identical to the long-standing single-slot exclusion. (Fix: opening
    # Trinket 2 used to offer a bag copy of the Solarflare Prism already worn
    # in Trinket 1, since only the opened slot's item was excluded.)
    equipped_ids: set[int] = set()
    for _eq_slot in wanted:
        _eq = equipped.get(_eq_slot)
        _eq_id = getattr(_eq, "item_id", None) if _eq is not None else None
        if _eq_id is not None:
            equipped_ids.add(_eq_id)
    current_ilvl = getattr(current, "ilvl", None) if current is not None else None
    current_stats_raw = item_stats_fn(current) if current else None
    # Normalize the equipped baseline to the same target so the ΔeHP is a
    # like-for-like itemization comparison, not an ilvl-gap comparison.
    current_stats = _normalized_stats(current_stats_raw, current_ilvl, target_ilvl)

    alts: list[Alternative] = []
    # Track every item_id we've already scored so M+ loot doesn't duplicate
    # the same item the user already has in their bag/vault. Bag wins over
    # vault wins over M+ (caller order), matching player intuition: a piece
    # you literally own ranks above a chase target.
    scored_ids: set[int] = set()

    def _score(item, source: str) -> None:
        cand_id = getattr(item, "item_id", None)
        # Skip anything already worn in this slot or its paired slot (see
        # ``equipped_ids``). Also dedupes bag/vault/loot double-listings.
        if cand_id is not None and cand_id in equipped_ids:
            return
        item_id = cand_id or 0
        if item_id and item_id in scored_ids:
            return
        if (
            exclude_two_handed
            and item_is_two_handed_fn is not None
            and item_is_two_handed_fn(item) is True
        ):
            return
        if item_id:
            scored_ids.add(item_id)
        cand_ilvl = getattr(item, "ilvl", None)
        new_stats_raw = item_stats_fn(item)
        new_stats = _normalized_stats(new_stats_raw, cand_ilvl, target_ilvl)
        # shown_at_ilvl is set only when scaling actually happened — i.e. a
        # target was requested AND we had a base ilvl AND stats to scale.
        shown_at = target_ilvl if (target_ilvl and cand_ilvl and new_stats_raw) else None
        scores, terms_by_dungeon = score_item_across_dungeons(
            new_stats=new_stats,
            equipped_stats=current_stats,
            marginals=marginals,
            dungeons=dungeons,
            return_terms=True,
        )
        avg = sum(s.delta_ehp for s in scores) / len(scores) if scores else 0.0
        # Composition averaged over the SAME per-dungeon scores as `avg`
        # above (identical arithmetic, just not summed yet) — only the
        # terms half of the pair is used; `avg` itself stays computed the
        # existing way so no existing caller can see a float-divergence risk.
        _, terms = average_composition_terms(
            [(s.delta_ehp, t) for s, t in zip(scores, terms_by_dungeon, strict=True)]
        )
        d_dps = compute_delta_dps(new_stats, current_stats, dps_weights) if dps_weights else 0.0
        alts.append(
            Alternative(
                item=item,
                source=source,
                per_dungeon=scores,
                avg_delta_ehp=avg,
                delta_dps=d_dps,
                shown_at_ilvl=shown_at,
                new_stats=new_stats,
                terms=terms,
            )
        )

    for item in _gather(bag, wanted):
        _score(item, "bag")
    for item in _gather(vault, wanted):
        _score(item, "vault")
    if extra_sources:
        # Cross-source dedup is handled inside ``_score`` via ``scored_ids``,
        # so bag wins over vault wins over M+ on the same item_id. Caller
        # decides the label.
        for source_label, item in extra_sources:
            _score(item, source_label)

    alts.sort(key=lambda a: -a.avg_delta_ehp)
    return alts
