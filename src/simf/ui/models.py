"""simf UI — L0 data/model helpers.

Pure data structures and model-shaping helpers for the gear surfaces. No
Streamlit, no session state — everything here is a function of its arguments
(plus the engine/optimizer modules it imports). Extracted from ``app.py`` so
the recommender's per-slot pick model has its own import-light home and can be
unit-tested without spinning up the UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from simf.optimizer.per_dungeon import CompositionTerms, DungeonScore
from simf.optimizer.tier_sets import swap_breaks_threshold
from simf.ui.helpers.gear_list import SLOT_LABELS, SlotRow


@dataclass(frozen=True)
class _SlotPick:
    slot: str
    item: object | None  # recommended item (None = empty slot, no equipped + no alt)
    is_swap: bool  # True when recommended item ≠ currently equipped
    delta_ehp: float  # 0.0 when no swap
    delta_dps: float
    composite: float
    has_warning: bool  # trinket-stats-only flag
    n_alternatives: int = 0  # candidates in bag+vault for this slot
    # False = delta_ehp is pure passive-stat arithmetic for an unmodeled
    # proc/on-use trinket (the registry had no proc/use model for the
    # candidate). The renderer suppresses the number in that case rather
    # than showing false precision — two different unmodeled proc trinkets
    # would otherwise display an identical clean figure. Default True so
    # every non-trinket slot and every registry-modeled trinket shows its
    # number as before.
    ehp_modeled: bool = True
    # When this recommended swap would drop the player below an active tier-set
    # threshold, the label of the bonus it breaks (e.g. "4pc Night Ender's
    # Vesture"), else None. The ΔeHP does NOT model the set bonus, so a "+X eHP"
    # swap that breaks a 4pc is usually a net survival LOSS the number can't see
    # — flag it on the card instead of silently recommending it. (User #3.)
    breaks_set: str | None = None
    # `Alternative.source` carried through ("bag" | "vault" | "m+ {abbrev}").
    # The slot dialog already showed this per-alternative; the paperdoll's
    # top-level swap card didn't, so an unowned M+ loot-table drop rendered
    # identically to a real owned-item swap under the same "N slots
    # recommend a swap" headline — a player would click Trial and see
    # nothing change (round-1 multi-agent review, 2026-07-05). None for the
    # equipped baseline pick, which has no single source.
    source: str | None = None
    # Per-stat ΔeHP composition of `delta_ehp` (per_dungeon.CompositionTerms)
    # — sums exactly to `delta_ehp`. Empty whenever `via_trinket_registry` is
    # True (nothing honest to decompose — see that field) or the pick isn't
    # a swap at all.
    terms: CompositionTerms = field(default_factory=dict)
    # True when this pick's ΔeHP came from the trinket-effect registry
    # (`optimizer/trinket_db.py`, via `per_dungeon.trinket_swap_per_dungeon`)
    # rather than the stat-marginal dot product every other pick uses. The
    # renderer shows "valued via trinket effect registry" instead of a
    # fabricated stat composition when this is True.
    via_trinket_registry: bool = False
    # False only for a trinket candidate the registry can't score
    # (`ehp_modeled=False`) while the EQUIPPED trinket it would replace is
    # itself registry-known — comparing them on stats alone silently drops
    # the equipped item's real proc credit, so an unknown candidate can look
    # like a stats "upgrade" purely because the equipped trinket's own raw
    # stats are weak (its value lives in the proc, not the stats). Default
    # True everywhere else, INCLUDING the symmetric case where neither side
    # is registry-known — that comparison is honest on both sides and stays
    # eligible to win, unchanged (2026-06-17 fix, see
    # test_unmodeled_proc_trinket_chip_suppresses_false_precision_ehp).
    # Never promoted to the recommended pick (`_per_slot_picks`); still
    # visible in `ranked_by_slot` for Browse/the upgrade panel (F-005
    # follow-on, 2026-07-20 — a known-proc trinket kept losing post-trial to
    # an unknown candidate purely on stats).
    eligible_for_recommendation: bool = True
    # Per-dungeon ΔeHP breakdown for this pick (`Alternative.per_dungeon`,
    # or `trinket_swap_per_dungeon`'s registry-scored equivalent for a
    # trinket swap — same override symmetry `terms`/`via_trinket_registry`
    # already use). Empty for the equipped baseline (nothing to break
    # down) and for a pick with no dungeons scored. Feeds the Gear tab's
    # consolidated "per-dungeon breakdown for your swaps" section
    # (`recommend.py::_render_gear_per_dungeon_breakdown`) — added
    # 2026-07-27 to close a real Vault/Gear parity gap: Vault's per-offer
    # cards and the slot-browse dialog both showed this, the Gear tab's
    # own paperdoll cards never did.
    per_dungeon: list[DungeonScore] = field(default_factory=list)


# Slots that come in interchangeable pairs — a single candidate item is scored
# for both, so the same item can independently win both (you can only wear one).
_PAIRED_SLOTS: list[tuple[str, str]] = [("trinket1", "trinket2"), ("finger1", "finger2")]


def _dedupe_paired_picks(
    picks: dict[str, _SlotPick],
    ranked_by_slot: dict[str, list[_SlotPick]],
    baseline_by_slot: dict[str, _SlotPick],
) -> None:
    """Resolve a candidate winning BOTH slots of a paired group (trinkets,
    rings). Each slot is scored independently, so an M+ chase trinket or a
    strong ring can be the top swap for both — but you can only equip one.
    Recommending it twice proposes an impossible loadout AND counts its ΔeHP
    twice in the headline total (the demo recommended Solar Core Igniter's ~42k
    ΔeHP on BOTH trinket slots). Keep the shared item on the slot where it
    scores higher; for the other slot re-pick the next-best DISTINCT candidate,
    falling back to the equipped baseline when none beats it. Mutates ``picks``
    in place."""
    for s1, s2 in _PAIRED_SLOTS:
        p1, p2 = picks.get(s1), picks.get(s2)
        if p1 is None or p2 is None or not (p1.is_swap and p2.is_swap):
            continue
        id1 = getattr(p1.item, "item_id", None)
        id2 = getattr(p2.item, "item_id", None)
        if id1 is None or id1 != id2:
            continue
        # Keep the shared item on its stronger slot; re-pick for the weaker one.
        _, drop_slot = (s1, s2) if p1.composite >= p2.composite else (s2, s1)
        baseline = baseline_by_slot[drop_slot]
        picks[drop_slot] = next(
            (
                c
                for c in ranked_by_slot.get(drop_slot, [])
                if getattr(c.item, "item_id", None) != id1 and c.composite > baseline.composite
            ),
            baseline,
        )


def _is_meaningful_swap(best: _SlotPick | None, baseline: _SlotPick, ehp_threshold: float) -> bool:
    """Whether a slot should flag a swap on the default paperdoll.

    True only when the best candidate wins on the composite (slider-weighted)
    score AND its survival gain (ΔeHP) clears the meaningful-upgrade bar. A
    marginal vers re-shuffle (any tiny +ΔeHP) thus stays a quiet "Best you own"
    instead of lighting up the slot — the noise the user flagged. Sub-bar
    candidates remain in ``ranked_by_slot`` so the Browse control + upgrade
    panel still show the full max-vers sweep. ``ehp_threshold == 0`` disables
    the gate (any composite win flags), preserving the pre-2026-06-30 behavior.
    """
    return (
        best is not None and best.composite > baseline.composite and best.delta_ehp >= ehp_threshold
    )


def _flag_set_breaks(picks: dict[str, _SlotPick], equipped: dict, class_spec: str) -> None:
    """Mark each recommended swap that would drop the player below an active
    tier-set threshold (sets `breaks_set` to e.g. "4pc Night Ender's Vesture").

    The recommender's ΔeHP doesn't model the 2pc/4pc set bonus, so a swap that
    breaks it is usually a net survival LOSS the number can't see — flag it
    (don't fabricate the set-bonus eHP). Mutates `picks` in place; reuses the
    same `swap_breaks_threshold` detector the slot dialog warns with."""
    for slot_key, pick in list(picks.items()):
        if not (pick.is_swap and pick.item is not None):
            continue
        broken = swap_breaks_threshold(equipped, class_spec, slot_key, pick.item)
        if broken is not None:
            picks[slot_key] = replace(
                pick, breaks_set=f"{broken.active_threshold}pc {broken.set.name}"
            )


def _card_for(
    slot: str, rows_by_slot: dict, picks: dict[str, _SlotPick]
) -> tuple[SlotRow, _SlotPick]:
    """Resolve the (row, pick) for a paperdoll slot, synthesising empty records
    for cosmetic slots (shirt/tabard) the data layer doesn't produce — so the
    grid renders them as quiet empty placeholders instead of raising KeyError."""
    row = rows_by_slot.get(slot)
    if row is None:
        row = SlotRow(
            slot=slot,
            slot_label=SLOT_LABELS.get(slot, slot.replace("_", " ").title()),
            item=None,
            name=None,
            ilvl=None,
        )
    pick = picks.get(slot)
    if pick is None:
        pick = _SlotPick(
            slot=slot,
            item=None,
            is_swap=False,
            delta_ehp=0.0,
            delta_dps=0.0,
            composite=0.0,
            has_warning=False,
            n_alternatives=0,
        )
    return row, pick
