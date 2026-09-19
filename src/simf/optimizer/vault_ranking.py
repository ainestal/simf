"""Vault ranking for the v0.9 verdict card.

Wraps `per_dungeon.score_item_across_dungeons` over the user's actual vault
choices and returns rows pre-sorted best-first, with each row carrying the
per-dungeon breakdown and the verdict sentence. The first row is the
"Take the X" winner.

Distinct from `vault_joint_optimizer.py` (which pairs a vault pick with a
bag swap). This module ranks vault picks on their own merits.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .item_upgrade import scale_stats
from .per_dungeon import (
    CompositionTerms,
    DungeonScore,
    average_composition_terms,
    coverage_sentence,
    score_item_across_dungeons,
    trinket_swap_per_dungeon,
    verdict_sentence,
    weakest_dungeon_line,
)

SLOT_LABELS = {
    "head": "Helm",
    "neck": "Neck",
    "shoulder": "Shoulders",
    "back": "Cloak",
    "chest": "Chest",
    "wrist": "Bracers",
    "hands": "Gloves",
    "waist": "Belt",
    "legs": "Legs",
    "feet": "Boots",
    "finger1": "Ring 1",
    "finger2": "Ring 2",
    "trinket1": "Trinket 1",
    "trinket2": "Trinket 2",
    "main_hand": "Main hand",
    "off_hand": "Off hand",
}

# Paired slots: an offer labelled finger1/trinket1 can replace EITHER of the
# two equipped pieces. Lives here (not alternatives.py) because alternatives
# already imports from this module — the reverse import would be circular;
# alternatives re-exports both names so existing import sites keep working.
PAIRED_SLOTS: dict[str, set[str]] = {
    "finger1": {"finger1", "finger2"},
    "finger2": {"finger1", "finger2"},
    "trinket1": {"trinket1", "trinket2"},
    "trinket2": {"trinket1", "trinket2"},
}


def equivalent_slots(slot: str) -> set[str]:
    """The set of slot names that can supply an alternative for `slot`.
    For rings/trinkets that's both paired slots; otherwise just the slot itself."""
    return PAIRED_SLOTS.get(slot, {slot})


@dataclass
class VaultRow:
    item: object  # ItemSpec or compatible
    slot: str
    per_dungeon: list[DungeonScore]  # scores across the FULL `dungeons` catalog
    avg_delta_ehp: float  # filtered avg (over selected_dungeon_ids, or all)
    verdict_sentence: str  # filtered phrasing — mentions only selected dungeons
    delta_dps: float = 0.0
    new_stats: dict[str, int] | None = None
    all_avg_delta_ehp: float = 0.0  # full-catalog avg, regardless of filter
    weakest_line: str = ""  # filtered "Weakest: <dungeon> -<delta> eHP." or ""
    coverage_line: str = ""  # filtered "Wins all 7 prog dungeons by …" or ""
    selected_dungeon_count: int = 0  # |selected ids ∩ dungeons|
    total_dungeon_count: int = 0  # |dungeons|
    # The ilvl this row's stats + ΔeHP were normalized to (upgrade-comparison
    # mode). None = scored as-dropped at the item's drop ilvl. When set, the
    # UI renders "ilvl X (up from <drop>)" and the displayed ``new_stats`` are
    # the scaled values, so the player sees the piece at the compared level.
    shown_at_ilvl: int | None = None
    # ── track-aware verdict (2026-06-10) ────────────────────────────────────
    # DEAD: the player already owns this item_id (equipped or bag) at an ilvl
    # >= the offer's reachable ceiling — upgrading the offer can never beat
    # the copy in hand. Excluded from the headline winner, rendered demoted.
    dead: bool = False
    # Owned same item_id BELOW the offer's reachable ceiling — the offer can
    # walk higher than the owned copy sits today. We can't read the owned
    # copy's own track ceiling without a bonus-id rank table, so the UI
    # frames this from what IS known: "you own this at iX; this reaches iY."
    ceiling_upgrade: bool = False
    owned_max_ilvl: int | None = None  # highest owned copy (equipped or bag)
    offer_ceiling: int | None = None  # reachable ceiling for the offer's slot
    # Paired slots only (rings/trinkets): the equipped slot the most
    # favorable swap replaces, and the incumbent itself (None = slot empty).
    replaces_slot: str | None = None
    replaces_item: object | None = None
    # Trinket rows only. True when this row's avg_delta_ehp is stats-only
    # (raw passive stats) rather than proc/on-use-aware — either because no
    # `char` was passed in, or the trinket_db registry doesn't know one or
    # both sides. False for every non-trinket slot (nothing to warn about)
    # AND for a trinket row where both sides resolved through the registry.
    trinket_warning: bool = False
    # Per-stat ΔeHP composition of `avg_delta_ehp` (per_dungeon.
    # CompositionTerms) — empty whenever `via_trinket_registry` is True
    # (the registry path's ΔeHP isn't a stat-marginal dot product at all,
    # so there's nothing honest to decompose; the UI renders "valued via
    # trinket effect registry" instead — see `via_trinket_registry`).
    # Otherwise sums exactly to `avg_delta_ehp`.
    terms: CompositionTerms = field(default_factory=dict)
    # True when this row's ΔeHP came from `trinket_db`'s proc/on-use-aware
    # registry rather than the stat-marginal dot product every other row
    # (and every stats-only-fallback trinket row) uses. The UI must not
    # attempt a stat composition on a registry-valued number — it isn't
    # built from stats at all.
    via_trinket_registry: bool = False


def compute_delta_dps(
    new_stats: dict[str, int] | None,
    equipped_stats: dict[str, int] | None,
    dps_weights: dict[str, float],
) -> float:
    """Σ (Δrating × weight) / 1000 across the secondaries in ``dps_weights``.

    Returns 0.0 if ``new_stats`` is missing — without item data we cannot
    score the swap, and a non-zero answer would mislead.
    """
    if not new_stats:
        return 0.0
    eq = equipped_stats or {}
    return sum(
        (new_stats.get(stat, 0) - eq.get(stat, 0)) * weight / 1000.0
        for stat, weight in dps_weights.items()
    )


def _normalized(
    stats: dict[str, int] | None,
    from_ilvl: int | None,
    target_ilvl: int | None,
) -> dict[str, int] | None:
    """Scale ``stats`` to ``target_ilvl`` for an upgrade-normalized comparison.

    No-op (returns stats unchanged) when no target was requested, the item has
    no base ilvl to scale from, or stats are missing — same convention as the
    slot-dialog ranker, so the two surfaces agree number-for-number.
    """
    if not stats or not target_ilvl or not from_ilvl:
        return stats
    return scale_stats(stats, from_ilvl, target_ilvl)


def _owned_max_ilvl(
    item_id: int | None,
    equipped: dict[str, object] | None,
    bag: dict[str, list] | None,
) -> int | None:
    """Highest ilvl among ALL owned copies of ``item_id`` — equipped pieces
    plus every bag entry.

    Max over copies matters: a low-ilvl bag spare must not mask a maxed
    equipped copy (real case: Bifurcation Band equipped at 289 with a 266
    copy still in the bag). Returns None when the item isn't owned or no
    copy carries a usable ilvl.
    """
    if not item_id:
        return None
    specs = list((equipped or {}).values())
    for items in (bag or {}).values():
        specs.extend(items)
    best: int | None = None
    for owned in specs:
        if getattr(owned, "item_id", None) != item_id:
            continue
        ilvl = getattr(owned, "ilvl", None)
        if ilvl and (best is None or int(ilvl) > best):
            best = int(ilvl)
    return best


@dataclass
class _IncumbentScore:
    """One candidate baseline for an offer: the swap scored against a single
    equipped incumbent (paired slots produce up to two of these)."""

    slot: str
    spec: object | None  # the equipped incumbent (None = slot empty)
    equipped_stats: dict[str, int] | None
    new_stats: dict[str, int] | None
    target: int | None
    scores: list[DungeonScore]
    filtered_scores: list[DungeonScore]
    filtered_avg: float
    trinket_warning: bool = False
    # See VaultRow.terms / VaultRow.via_trinket_registry — carried through
    # unchanged from whichever incumbent this candidate scored against.
    terms: CompositionTerms = field(default_factory=dict)
    via_trinket_registry: bool = False


def rank_vault_items(
    vault_items: list,
    equipped: dict[str, object],
    marginals: dict,
    dungeons: list[dict],
    item_stats_fn,
    dps_weights: dict[str, float] | None = None,
    selected_dungeon_ids: list[str] | None = None,
    target_ilvl_fn=None,
    *,
    bag: dict[str, list] | None = None,
    owned_equipped: dict[str, object] | None = None,
    ceiling_fn=None,
    char=None,
) -> list[VaultRow]:
    """Rank this week's vault choices best-first.

    Args:
        vault_items: list of ItemSpec (or shape-compatible objects with `slot`).
        equipped: dict[slot, ItemSpec] — currently-equipped baseline.
        marginals: ehp_marginals(char) output.
        dungeons: list of dicts with `id`, `abbrev`, `school_mix` — the
            FULL catalog. Per-row `per_dungeon` carries scores for all of them.
        item_stats_fn: callable(item_spec) -> dict[str, int] | None.
        dps_weights: optional Σ(Δrating × weight)/1000 weights.
        selected_dungeon_ids: subset of dungeon ids to use for the row's
            ``avg_delta_ehp`` and ``verdict_sentence``. None or all-of-dungeons
            means treat every dungeon as selected — backward-compatible.
        bag: dict[slot, list[ItemSpec]] — bag inventory, used (with
            ``owned_equipped``) for dead-pick / ceiling-upgrade ownership
            detection only. None keeps detection off the bag.
        owned_equipped: the gear set ownership detection reads. Defaults to
            ``equipped``; the app passes the parsed-from-SimC baseline so a
            trial overlay can't make an offer look "owned" mid-trial.
        ceiling_fn: callable(slot) -> int | None — the offer's reachable ilvl
            ceiling (the app builds this from ``category_ceiling`` with the
            account watermark clamp). None disables dead/ceiling detection.
        char: the player's Character. Trinket rows only — when provided,
            trinket-vs-trinket comparisons route through
            ``optimizer/trinket_db.py``'s proc/on-use-aware model (same as
            the Gear tab's cross-slot ranker) instead of raw passive stats.
            None (default) keeps every trinket row stats-only, matching the
            pre-2026-07-08 behavior exactly — a caller that doesn't have a
            Character handy loses trinket-effect awareness but nothing else
            breaks.

    Returns:
        VaultRows with live picks first (descending *filtered* avg ΔeHP),
        dead picks demoted to the end. Items without a `slot` value are
        skipped silently.
    """
    weights = dps_weights or {}
    catalog_ids = {d["id"] for d in dungeons}
    if selected_dungeon_ids:
        selected_set = {d for d in selected_dungeon_ids if d in catalog_ids}
    else:
        selected_set = catalog_ids
    rows: list[VaultRow] = []
    for spec in vault_items:
        slot = getattr(spec, "slot", None)
        if not slot:
            continue
        item_ilvl = getattr(spec, "ilvl", None)
        new_stats_raw = item_stats_fn(spec)
        # Paired slots (rings/trinkets): an offer labelled trinket1 can
        # replace EITHER equipped trinket. Score the swap against each
        # incumbent and keep the most favorable — highest *filtered* avg
        # ΔeHP, the headline metric — so the row reflects the swap the
        # player would actually make. Unpaired slots iterate once, which is
        # bit-identical to the old single-incumbent path. The normalize
        # target is resolved per incumbent slot (target_ilvl_fn(cand_slot))
        # so "Match my gear" matches the gear actually being replaced.
        partner_slots = equivalent_slots(slot)
        candidate_slots = [slot, *sorted(s for s in partner_slots if s != slot)]

        is_trinket_offer = slot in ("trinket1", "trinket2")

        def _score_vs(
            cand_slot,
            gear,
            *,
            _raw=new_stats_raw,
            _ilvl=item_ilvl,
            _spec=spec,
            _is_trinket=is_trinket_offer,
        ):
            equipped_spec = gear.get(cand_slot)
            equipped_ilvl = getattr(equipped_spec, "ilvl", None) if equipped_spec else None
            # Upgrade-normalized comparison: rescale BOTH the equipped
            # baseline and the vault candidate to a common target so the
            # ΔeHP reflects itemization, not the ilvl gap. target → None
            # keeps the as-dropped path (existing callers untouched).
            target = target_ilvl_fn(cand_slot) if target_ilvl_fn else None
            equipped_stats = _normalized(
                item_stats_fn(equipped_spec) if equipped_spec else None, equipped_ilvl, target
            )
            new_stats = _normalized(_raw, _ilvl, target)

            # Trinkets: prefer the proc/on-use-aware registry over raw passive
            # stats, same as the Gear tab's cross-slot ranker
            # (recommend.py:trinket_swap_per_dungeon) — closes the gap where
            # Vault scored trinkets on passive stats only. Note this path
            # does NOT go through the ilvl-normalized `target` above;
            # `trinket_ehp_contribution` scores off `char.ilvl` directly,
            # same limitation the Gear tab already accepts, so the two
            # surfaces stay consistent with each other rather than one
            # silently gaining ilvl-awareness the other lacks.
            registry = (
                trinket_swap_per_dungeon(equipped_spec, _spec, char, dungeons)
                if char is not None and _is_trinket
                else None
            )
            via_registry = False
            terms_by_dungeon: list[CompositionTerms] = []
            if registry is not None:
                scores, both_known = registry
                trinket_warn = not both_known
                via_registry = True
            else:
                scores, terms_by_dungeon = score_item_across_dungeons(
                    new_stats=new_stats,
                    equipped_stats=equipped_stats,
                    marginals=marginals,
                    dungeons=dungeons,
                    return_terms=True,
                )
                trinket_warn = _is_trinket

            filtered_scores = [s for s in scores if s.dungeon_id in selected_set] or scores
            filtered_avg = (
                sum(s.delta_ehp for s in filtered_scores) / len(filtered_scores)
                if filtered_scores
                else 0.0
            )
            # The registry path's ΔeHP isn't a stat-marginal dot product at
            # all — nothing honest to decompose (see VaultRow.terms). For the
            # stat path, average the SAME dungeon subset `filtered_avg` used
            # (falling back to the full set the same way), so the composition
            # sums exactly to `filtered_avg`.
            if via_registry:
                filtered_terms: CompositionTerms = {}
            else:
                paired = list(zip(scores, terms_by_dungeon, strict=True))
                filtered_pairs = [
                    (s.delta_ehp, t) for s, t in paired if s.dungeon_id in selected_set
                ] or [(s.delta_ehp, t) for s, t in paired]
                _, filtered_terms = average_composition_terms(filtered_pairs)
            return _IncumbentScore(
                slot=cand_slot,
                spec=equipped_spec,
                equipped_stats=equipped_stats,
                new_stats=new_stats,
                target=target,
                scores=scores,
                filtered_scores=filtered_scores,
                filtered_avg=filtered_avg,
                trinket_warning=trinket_warn,
                terms=filtered_terms,
                via_trinket_registry=via_registry,
            )

        # Incumbent SELECTION reads the same stable gear set as ownership
        # (owned_equipped): under an active trial the overlaid ``equipped``
        # changes every rerun, and selecting on it makes replaces_slot flip
        # to the OTHER paired incumbent right after trialing an offer that
        # beats both — the caption then names the wrong piece (caught in the
        # 2026-06-10 pre-merge adversarial review). Display scores below
        # still track the live ``equipped`` so trial deltas stay live.
        selection_gear = owned_equipped if owned_equipped is not None else equipped
        best: _IncumbentScore | None = None
        for cand_slot in candidate_slots:
            cand = _score_vs(cand_slot, selection_gear)
            # Strictly-greater keeps the offered slot on ties (deterministic).
            if best is None or cand.filtered_avg > best.filtered_avg:
                best = cand
        # candidate_slots always includes at least the offer's own slot, so the
        # loop always assigns. The assert documents that invariant for the type
        # checker and turns a never-expected empty-slots regression into a clear
        # failure instead of an opaque AttributeError downstream.
        assert best is not None  # noqa: S101 — internal invariant, not user input
        # Caption identity (which real piece this swap gives up) is frozen
        # from the stable set; the recompute below only refreshes numbers.
        stable_slot = best.slot
        stable_spec = best.spec
        if owned_equipped is not None:
            best = _score_vs(stable_slot, equipped)
        shown_at = best.target if (best.target and item_ilvl and new_stats_raw) else None
        all_avg = sum(s.delta_ehp for s in best.scores) / len(best.scores) if best.scores else 0.0
        # Ownership: highest owned copy of this item_id across the player's
        # real gear + bag. Dead = owned at (or above) everything this offer
        # could ever be upgraded to. Ceiling unknown → both flags stay off.
        offer_ceiling = ceiling_fn(slot) if ceiling_fn else None
        owned_max = _owned_max_ilvl(
            getattr(spec, "item_id", None),
            owned_equipped if owned_equipped is not None else equipped,
            bag,
        )
        is_paired = len(partner_slots) > 1
        rows.append(
            VaultRow(
                item=spec,
                slot=slot,
                per_dungeon=best.scores,
                avg_delta_ehp=best.filtered_avg,
                verdict_sentence=verdict_sentence(best.filtered_scores),
                weakest_line=weakest_dungeon_line(best.filtered_scores),
                coverage_line=coverage_sentence(best.filtered_scores),
                delta_dps=compute_delta_dps(best.new_stats, best.equipped_stats, weights),
                new_stats=best.new_stats,
                all_avg_delta_ehp=all_avg,
                selected_dungeon_count=len(best.filtered_scores),
                total_dungeon_count=len(best.scores),
                shown_at_ilvl=shown_at,
                dead=bool(owned_max and offer_ceiling and owned_max >= offer_ceiling),
                ceiling_upgrade=bool(owned_max and offer_ceiling and owned_max < offer_ceiling),
                owned_max_ilvl=owned_max,
                offer_ceiling=offer_ceiling,
                replaces_slot=stable_slot if is_paired else None,
                replaces_item=stable_spec if is_paired else None,
                trinket_warning=best.trinket_warning,
                terms=best.terms,
                via_trinket_registry=best.via_trinket_registry,
            )
        )
    # Live picks best-first; dead picks demoted below every live one. With
    # no dead detection wired (default args) this is the old sort exactly.
    rows.sort(key=lambda r: (r.dead, -r.avg_delta_ehp))
    return rows


def dead_count_sentence(rows: list[VaultRow]) -> str:
    """'2 of 3 offers duplicate items you already own at their ceiling.'

    Empty string when no offer is dead — callers append it to the verdict
    card only when it carries information.
    """
    dead = sum(1 for r in rows if r.dead)
    if not dead:
        return ""
    total = len(rows)
    if dead == 1:
        return f"1 of {total} offers duplicates an item you already own at its ceiling."
    return f"{dead} of {total} offers duplicate items you already own at their ceiling."


def headline_for_winner(row: VaultRow) -> str:
    """Verdict-card headline. Names the item first so two same-slot picks
    (e.g. two main-hand vault choices) are distinguishable at a glance.

    Resolution order:
        1. Item name → ``Take <name>.`` (Crown of the Worldforge, Aldrachi Warblades…)
        2. Slot label fallback → ``Take the <label>.`` (older SimC exports
           that didn't include the item-name comment)
        3. Last resort → ``Take this upgrade.`` (no name, no known slot)
    """
    name = (getattr(row.item, "name", None) or "").strip()
    if name:
        return f"Take {name}."
    label = SLOT_LABELS.get(row.slot)
    if label:
        return f"Take the {label}."
    return "Take this upgrade."
