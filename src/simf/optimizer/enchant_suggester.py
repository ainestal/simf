"""Per-slot enchant suggestion by survival ΔeHP.

Scores Midnight permanent enchants (``data/enchants.yaml``) against a
character's per-stat survivability marginals and recommends the best survival
enchant for each enchantable slot. Sibling to ``optimizer/gem_suggester`` —
same ΔeHP path (``optimizer/per_dungeon._score_for_school_mix``), same
``primary`` → agility/strength resolution (reused directly from
``gem_suggester``, not duplicated), same "never recommend a downgrade"
keep-current guard.

The one real difference from gems: several Midnight enchants (head, shoulder,
every weapon enchant, part of the feet options) grant Avoidance, Leech, Speed,
or a combat proc — none of which are in simf's survivability model. Rather
than special-case those slots, every candidate is scored through the same
generic path; when a slot's entire catalog pool scores at or near zero, the
suggestion is flagged ``modeled=False`` instead of presenting an arbitrary
tie-break as a confident "best" pick. This is data-driven, not hardcoded — a
slot automatically stops being "not modeled" the day a real stat is added for
it (e.g. if Avoidance ever gets an eHP term).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal, overload

import yaml

from .gem_suggester import primary_stat_key, resolve_gem_stats
from .per_dungeon import CompositionTerms, _score_for_school_mix, average_composition_terms

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_ENCHANTS_FILE = _DATA_DIR / "enchants.yaml"

# Generic fallback damage mix when the caller passes no dungeons — matches
# gem_suggester's convention.
_GENERIC_DUNGEON = {"id": "generic", "school_mix": {"physical": 0.65}}

# Dual-wield tank specs: their off-hand is a genuine second one-handed
# weapon, so it takes the same weapon-enchant pool as main_hand. Every other
# spec's off-hand is a shield (or absent) — confirmed live 2026-06-01: zero
# `enchant_id` ever appears on a shield across 6+ real gear snapshots.
_DUAL_WIELD_SPECS = frozenset({"brewmaster_monk", "vengeance_demon_hunter"})

# simf slot name -> catalog category. `back`/`wrist`/`neck` are absent on
# purpose (no enchant exists for any of the three in Midnight 12.0.5);
# `off_hand` is added per-spec by `enchantable_slots`.
_SLOT_CATEGORY = {
    "head": "head",
    "shoulder": "shoulder",
    "chest": "chest",
    "legs": "legs",
    "feet": "feet",
    "finger1": "finger",
    "finger2": "finger",
    "main_hand": "weapon",
    "off_hand": "weapon",
}

# Slots CONFIRMED to have no permanent enchant in Midnight 12.0.5 — a real,
# researched finding, not a data gap. back/wrist: removed as a mechanic
# outright (see data/enchants.yaml's header). neck: the WoD/MoP-era
# neck-enchant mechanic was removed starting Legion (2016) and has never
# returned — Wowhead's raw recipe DB still lists "Enchant Neck" items, but
# their `reqskill` ids resolve to that ~2015 WoD content, not anything live;
# wow-professions.com's current Midnight Enchanting guide has zero "neck"
# mentions; and a real Guardian Druid's live `/simc` export
# (examples/anonguardian1-simc.txt, a heavily-upgraded 289/298-track neck) carries no
# `enchant_id=` at all. waist/hands: confirmed 2026-07-05 — two independent
# current-patch community guides (wow-professions.com, Method.gg) both give
# the same exhaustive 6-slot Enchanting-profession list (head/shoulder/
# chest/rings/boots/weapon) and never mention either slot, and every real
# Brutoh `/simc` export across multiple months carries zero `enchant_id=` on
# `waist=`/`hands=` (see data/enchants.yaml's header for the full citation).
# These slots still render a row in the UI — just with an honest "no enchant
# exists" caption instead of silence.
_NO_ENCHANT_SLOTS = frozenset({"back", "wrist", "neck", "waist", "hands"})

# Below this ΔeHP, every candidate in a slot's pool is worth ~0 survival —
# not "these are equally good," but "this slot's enchants aren't priced by
# the model yet" (Avoidance/Leech/Speed/procs). See module docstring.
_NOT_MODELED_EPSILON_EHP = 1.0


def enchantable_slots(class_spec: str) -> frozenset[str]:
    """simf slot names that can carry a permanent enchant for this spec."""
    slots = set(_SLOT_CATEGORY) - {"off_hand"}
    if class_spec in _DUAL_WIELD_SPECS:
        slots.add("off_hand")
    return frozenset(slots)


@lru_cache(maxsize=1)
def load_enchant_catalog() -> list[dict]:
    """All enchants from ``data/enchants.yaml``. Cached after first load."""
    with _ENCHANTS_FILE.open() as f:
        data = yaml.safe_load(f)
    return data["enchants"]


@lru_cache(maxsize=1)
def _enchants_by_id() -> dict[int, dict]:
    return {int(e["id"]): e for e in load_enchant_catalog() if e.get("id")}


def find_enchant_by_id(enchant_id: int | None) -> dict | None:
    """O(1) lookup of a raw catalog enchant by its numeric id, or ``None``
    when unknown/unconfirmed (many catalog entries have no id yet — see
    ``data/enchants.yaml``'s header)."""
    if not enchant_id:
        return None
    return _enchants_by_id().get(int(enchant_id))


@dataclass(frozen=True)
class EnchantCandidate:
    """A catalog enchant resolved for a particular spec, ready to score."""

    enchant_id: int | None
    name: str
    stats: dict[str, int]  # canonical keys, primary already resolved
    # Wowhead's "on-use" spell id (data/enchants.yaml's header) — a real,
    # live Wowhead page for this enchant, but NEVER usable as `enchant_id`
    # (different id namespace; can't match a live character's equipped
    # enchant). Purely a link target for the UI's hover tooltip.
    wowhead_spell_id: int | None = None


def _to_candidate(e: dict, primary: str) -> EnchantCandidate:
    return EnchantCandidate(
        enchant_id=int(e["id"]) if e.get("id") else None,
        name=str(e.get("name", "Unknown enchant")),
        stats=resolve_gem_stats(e.get("stats") or {}, primary),
        wowhead_spell_id=int(e["wowhead_spell_id"]) if e.get("wowhead_spell_id") else None,
    )


def candidate_enchants(
    category: str, class_spec: str, catalog: list[dict] | None = None
) -> list[EnchantCandidate]:
    """Catalog resolved for one slot category (``chest``/``finger``/...)."""
    primary = primary_stat_key(class_spec)
    raw = catalog if catalog is not None else load_enchant_catalog()
    return [_to_candidate(e, primary) for e in raw if e.get("slot") == category]


@overload
def enchant_survival_value(
    stats: dict[str, int],
    marginals: dict,
    dungeons: list[dict] | None = None,
    *,
    return_terms: Literal[False] = False,
) -> float: ...


@overload
def enchant_survival_value(
    stats: dict[str, int],
    marginals: dict,
    dungeons: list[dict] | None = None,
    *,
    return_terms: Literal[True],
) -> tuple[float, CompositionTerms]: ...


def enchant_survival_value(
    stats: dict[str, int],
    marginals: dict,
    dungeons: list[dict] | None = None,
    *,
    return_terms: bool = False,
) -> float | tuple[float, CompositionTerms]:
    """ΔeHP-equivalent survival value of an enchant's stats — identical
    formula to ``gem_suggester.gem_survival_value``, so an enchant's "+N eHP"
    is directly comparable to a gem's or an item's.

    With ``return_terms=True``, also returns the per-stat composition
    breakdown (``per_dungeon.CompositionTerms``), averaged across dungeons
    the same way as the scalar. Default behaviour (scalar-only) is unchanged
    for existing callers.
    """
    dlist = dungeons or [_GENERIC_DUNGEON]
    if not return_terms:
        scores = [_score_for_school_mix(stats, marginals, d.get("school_mix") or {}) for d in dlist]
        return sum(scores) / len(scores) if scores else 0.0

    pairs = [
        _score_for_school_mix(stats, marginals, d.get("school_mix") or {}, return_terms=True)
        for d in dlist
    ]
    return average_composition_terms(pairs)


@dataclass(frozen=True)
class EnchantSlot:
    """One enchantable slot on the character and its current enchant (``None``
    if empty/unrecognized)."""

    slot: str
    enchant_id: int | None = None


@dataclass(frozen=True)
class EnchantSuggestion:
    """The recommendation for a single slot."""

    slot: str
    current_enchant_id: int | None
    current_name: str | None
    current_known: bool  # False when the equipped enchant isn't in the catalog
    current_value: float
    # Wowhead link target for the CURRENT enchant's name (None when empty/
    # unrecognized, or the catalog entry has no wowhead_spell_id yet — see
    # EnchantCandidate).
    current_wowhead_spell_id: int | None
    best: EnchantCandidate | None
    best_value: float
    delta_ehp: float  # best_value − current_value (≥ 0 unless current beats catalog)
    modeled: bool  # False when every candidate in this slot's pool scores ~0
    # Only ever True alongside modeled=False — explains WHY a slot has no
    # pick, distinct from "cataloged but every option scores near-zero" (see
    # _SLOT_CATEGORY's comment block and the module docstring):
    no_enchant_exists: bool = (
        False  # True for back/wrist/neck/waist/hands: confirmed no permanent enchant in Midnight
    )
    ranked: list[tuple[EnchantCandidate, float]] = field(default_factory=list)
    # Per-stat ΔeHP composition of `best_value` (per_dungeon.CompositionTerms),
    # computed once for whichever candidate ends up as `best` — empty dict
    # when `best` is None. Defaulted so existing EnchantSuggestion(...) call
    # sites don't need updating. Sibling to GemSuggestion.best_terms — a
    # later phase-2 workstream renders this identically for gem/enchant/gear
    # rows.
    best_terms: dict[str, dict[str, float]] = field(default_factory=dict)


def suggest_enchants(
    slots: list[EnchantSlot],
    marginals: dict,
    *,
    class_spec: str,
    dungeons: list[dict] | None = None,
    catalog: list[dict] | None = None,
) -> list[EnchantSuggestion]:
    """Recommend the best survival enchant for each slot.

    Args:
        slots: one ``EnchantSlot`` per enchantable slot the character has an
            item in (see ``enchant_slots_from_equipped``).
        marginals: per-stat ΔeHP marginals, ``{stat: {"p": .., "m": ..}}``.
        class_spec: drives the ``primary`` → agility/strength resolution.
        dungeons: list of ``{"school_mix": {...}}`` to average over; generic
            0.65/0.35 split if omitted.
        catalog: override the enchant catalog (tests); defaults to
            ``enchants.yaml``.

    Returns one ``EnchantSuggestion`` per input slot, in input order.
    """
    if not slots:
        return []

    primary = primary_stat_key(class_spec)
    ranked_cache: dict[str, list[tuple[EnchantCandidate, float]]] = {}

    def _ranked(category: str) -> list[tuple[EnchantCandidate, float]]:
        if category not in ranked_cache:
            cands = candidate_enchants(category, class_spec, catalog)
            scored = [(c, enchant_survival_value(c.stats, marginals, dungeons)) for c in cands]
            # Rank by value; tiebreak by id only when both sides have a real
            # one (unconfirmed-id candidates would otherwise collide/compare
            # unstably against each other under `None`).
            scored.sort(
                key=lambda pair: (
                    -pair[1],
                    pair[0].enchant_id if pair[0].enchant_id is not None else 2**31,
                )
            )
            ranked_cache[category] = scored
        return ranked_cache[category]

    # Resolve each slot's current enchant once: its catalog entry (or None
    # for an empty / non-catalog enchant) and its survival value (0 if
    # unknown/empty).
    cur_raw: list[dict | None] = [find_enchant_by_id(s.enchant_id) for s in slots]
    current_candidate: list[EnchantCandidate | None] = [
        _to_candidate(e, primary) if e else None for e in cur_raw
    ]
    current_value: list[float] = [
        enchant_survival_value(c.stats, marginals, dungeons) if c else 0.0
        for c in current_candidate
    ]
    current_known: list[bool] = [
        cur_raw[i] is not None or s.enchant_id is None for i, s in enumerate(slots)
    ]

    suggestions: list[EnchantSuggestion] = []
    for i, s in enumerate(slots):
        category = _SLOT_CATEGORY.get(s.slot)
        ranked = _ranked(category) if category else []
        pool_best, pool_best_val = ranked[0] if ranked else (None, 0.0)
        # Never recommend a strict downgrade: if the slot's current enchant
        # beats every catalog candidate, keep it (delta 0) rather than churn
        # for zero (or negative-looking) gain.
        if current_candidate[i] is not None and current_value[i] >= pool_best_val:
            best: EnchantCandidate | None = current_candidate[i]
            best_val = current_value[i]
            delta = 0.0
        else:
            best = pool_best
            best_val = pool_best_val
            delta = pool_best_val - current_value[i]
        cur = current_candidate[i]
        # Composition terms for the WINNING candidate only, computed once
        # here (not for the whole ranked pool) — see EnchantSuggestion.best_terms.
        best_terms: dict[str, dict[str, float]] = {}
        if best is not None:
            _, best_terms = enchant_survival_value(
                best.stats, marginals, dungeons, return_terms=True
            )
        suggestions.append(
            EnchantSuggestion(
                slot=s.slot,
                current_enchant_id=s.enchant_id,
                current_name=cur.name if cur else None,
                current_known=current_known[i],
                current_value=current_value[i],
                current_wowhead_spell_id=cur.wowhead_spell_id if cur else None,
                best=best,
                best_value=best_val,
                delta_ehp=delta,
                modeled=pool_best_val >= _NOT_MODELED_EPSILON_EHP,
                no_enchant_exists=s.slot in _NO_ENCHANT_SLOTS,
                ranked=ranked,
                best_terms=best_terms,
            )
        )
    return suggestions


def enchant_slots_from_equipped(equipped: dict, class_spec: str) -> list[EnchantSlot]:
    """Flatten an equipped dict (slot → item with ``enchant_id``) into one
    ``EnchantSlot`` per slot that (a) has an item equipped and (b) can carry a
    permanent enchant for this spec (``enchantable_slots``), OR is a slot the
    UI should still render an honest caption for even though it can never
    hold one (``_NO_ENCHANT_SLOTS``: back/wrist/neck/waist/hands). Structural
    non-slots that never render an enchant line at all — trinkets, a shield
    off_hand — are excluded by staying out of both sets.

    Handles both dataclass items (``ItemSpec.enchant_id``) and plain dicts
    (``item["enchant_id"]``), matching ``gem_suggester.sockets_from_equipped``.
    """
    allowed = enchantable_slots(class_spec) | _NO_ENCHANT_SLOTS
    out: list[EnchantSlot] = []
    for slot, item in equipped.items():
        if item is None or slot not in allowed:
            continue
        eid = getattr(item, "enchant_id", None)
        if eid is None and isinstance(item, dict):
            eid = item.get("enchant_id")
        out.append(EnchantSlot(slot=slot, enchant_id=int(eid) if eid else None))
    return out
