"""Per-socket gem suggestion by survival ΔeHP.

Scores Midnight gems (``data/gems.yaml``) against a character's per-stat
survivability marginals and recommends the best survival gem for each socket.

The scoring reuses the exact ΔeHP path the gear surfaces already use
(``optimizer/per_dungeon._score_for_school_mix``): a gem's survival value is

    Σ_stat (∂eHP/∂stat × gem_stat)   weighted by the dungeon damage mix

so it is fully spec-general with **no per-gem spec logic**:

  * Haste scores 0 for every spec whose haste marginal is 0 — i.e. every plate
    tank and a non-Elune's-Chosen Guardian — and only contributes when the
    live sim-derived marginals give haste a value (Elune's-Chosen Guardian).
  * Agility scores 0 for plate tanks (their armor/dodge don't scale with it);
    strength carries parry value for plate tanks. Both flow straight from the
    marginals dict the caller passes in.
  * There is no stamina gem in Midnight, so stamina never appears as a
    candidate — the suggester can only move secondary stats, primary stat, and
    the +13 Armor of the Stoic meta.

The Eversong Diamond metas are Unique-Equipped (only one socketable per
character), so the recommended *arrangement* puts the single best meta in one
socket and the best non-unique gem in every other socket — the suggester never
recommends two metas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal, overload

import yaml

from ..io.item_db import fetch_item_socket_count_wowhead
from .per_dungeon import CompositionTerms, _score_for_school_mix, average_composition_terms

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_GEMS_FILE = _DATA_DIR / "gems.yaml"

# Specs whose primary stat is Agility; everyone else is Strength. Matches the
# agi-spec set in optimizer/trinket_db.py (single source of truth would be
# nicer; kept in sync deliberately — see test_gem_suggester).
_AGI_SPECS = frozenset({"brewmaster_monk", "guardian_druid", "vengeance_demon_hunter"})

# Generic fallback damage mix when the caller passes no dungeons — the same
# fixed 0.65/0.35 physical/magic split the vault joint optimizer uses.
_GENERIC_DUNGEON = {"id": "generic", "school_mix": {"physical": 0.65}}


def primary_stat_key(class_spec: str) -> str:
    """Canonical marginal key for a spec's primary stat: ``agility`` for the
    agility tanks (Guardian / Brewmaster / VDH), ``strength`` otherwise."""
    return "agility" if class_spec in _AGI_SPECS else "strength"


@lru_cache(maxsize=1)
def load_gem_catalog() -> list[dict]:
    """All gems from ``data/gems.yaml``. Cached after first load."""
    with _GEMS_FILE.open() as f:
        data = yaml.safe_load(f)
    return data["gems"]


@lru_cache(maxsize=1)
def _gems_by_id() -> dict[int, dict]:
    return {int(g["id"]): g for g in load_gem_catalog() if g.get("id")}


def find_gem_by_id(gem_id: int | None) -> dict | None:
    """O(1) lookup of a raw catalog gem by item id, or ``None`` if unknown."""
    if not gem_id:
        return None
    return _gems_by_id().get(int(gem_id))


def resolve_gem_stats(raw_stats: dict, primary_stat: str) -> dict[str, int]:
    """Resolve a catalog gem's ``stats`` to canonical marginal keys.

    The only translation is ``primary`` → ``agility`` / ``strength`` per spec;
    every other key (``armor_from_gear``, ``*_rating``) is already canonical.
    If a gem somehow carried both ``primary`` and the resolved key, the two are
    summed.
    """
    resolved: dict[str, int] = {}
    for key, amount in raw_stats.items():
        canon = primary_stat if key == "primary" else key
        resolved[canon] = resolved.get(canon, 0) + int(amount)
    return resolved


@dataclass(frozen=True)
class GemCandidate:
    """A catalog gem resolved for a particular spec, ready to score."""

    item_id: int
    name: str
    stats: dict[str, int]  # canonical keys, primary already resolved
    unique_equipped: bool
    ilvl: int = 0


def _to_candidate(g: dict, primary: str) -> GemCandidate:
    """Map one raw catalog gem to a spec-resolved ``GemCandidate``."""
    return GemCandidate(
        item_id=int(g["id"]),
        name=str(g.get("name", f"Gem {g['id']}")),
        stats=resolve_gem_stats(g.get("stats") or {}, primary),
        unique_equipped=bool(g.get("unique_equipped", False)),
        ilvl=int(g.get("ilvl", 0)),
    )


def candidate_gems(class_spec: str, catalog: list[dict] | None = None) -> list[GemCandidate]:
    """Catalog resolved for ``class_spec`` (``primary`` → agility/strength)."""
    primary = primary_stat_key(class_spec)
    raw = catalog if catalog is not None else load_gem_catalog()
    return [_to_candidate(g, primary) for g in raw]


@overload
def gem_survival_value(
    stats: dict[str, int],
    marginals: dict,
    dungeons: list[dict] | None = None,
    *,
    return_terms: Literal[False] = False,
) -> float: ...


@overload
def gem_survival_value(
    stats: dict[str, int],
    marginals: dict,
    dungeons: list[dict] | None = None,
    *,
    return_terms: Literal[True],
) -> tuple[float, CompositionTerms]: ...


def gem_survival_value(
    stats: dict[str, int],
    marginals: dict,
    dungeons: list[dict] | None = None,
    *,
    return_terms: bool = False,
) -> float | tuple[float, CompositionTerms]:
    """ΔeHP-equivalent survival value of a gem's stats, averaged over dungeons.

    ``stats`` must use canonical marginal keys (call ``resolve_gem_stats``
    first for the ``primary`` translation). Uses the identical ΔeHP formula the
    gear surfaces use, so a gem's "+N eHP" is directly comparable to an item's.
    Averages across the supplied dungeons' school mixes; with no dungeons it
    falls back to the generic 0.65/0.35 split.

    With ``return_terms=True``, also returns the per-stat composition
    breakdown (``per_dungeon.CompositionTerms``), averaged the same way as
    the scalar. Default behaviour (scalar-only) is unchanged for existing
    callers.
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
class Socket:
    """One gem socket on the character: which slot, its index within the item,
    and the gem currently in it (``None`` if empty/unknown)."""

    slot: str
    index: int
    gem_id: int | None = None


@dataclass(frozen=True)
class GemSuggestion:
    """The recommendation for a single socket."""

    slot: str
    index: int
    current_gem_id: int | None
    current_name: str | None
    current_known: bool  # False when the equipped gem isn't in the catalog
    current_value: float
    best: GemCandidate | None
    best_value: float
    delta_ehp: float  # best_value − current_value (≥ 0 unless current beats catalog)
    is_meta_socket: bool  # True when this socket is the one reserved for the unique meta
    ranked: list[tuple[GemCandidate, float]] = field(default_factory=list)
    # Per-stat ΔeHP composition of `best_value` (per_dungeon.CompositionTerms),
    # computed once for whichever candidate ends up as `best` — empty dict
    # when `best` is None. Defaulted so existing GemSuggestion(...) call
    # sites (tests, mainly) don't need updating.
    best_terms: dict[str, dict[str, float]] = field(default_factory=dict)


def _is_meta_id(gem_id: int | None, meta_ids: frozenset[int]) -> bool:
    return gem_id is not None and int(gem_id) in meta_ids


def suggest_gems(
    sockets: list[Socket],
    marginals: dict,
    *,
    class_spec: str,
    dungeons: list[dict] | None = None,
    catalog: list[dict] | None = None,
) -> list[GemSuggestion]:
    """Recommend the best survival gem for each socket.

    Args:
        sockets: one ``Socket`` per filled gem socket on the character. (We can
            only reason about sockets we can see — i.e. that currently hold a
            gem; unknown empty sockets aren't suggested for.)
        marginals: per-stat ΔeHP marginals, ``{stat: {"p": .., "m": ..}}`` —
            the live sim-derived marginals (``ehp_marginals`` shape).
        class_spec: drives the ``primary`` → agility/strength resolution.
        dungeons: list of ``{"school_mix": {...}}`` to average over; generic
            0.65/0.35 split if omitted.
        catalog: override the gem catalog (tests); defaults to ``gems.yaml``.

    Returns one ``GemSuggestion`` per input socket, in input order. The unique
    Eversong Diamond meta is recommended in at most one socket.
    """
    if not sockets:
        return []

    primary = primary_stat_key(class_spec)
    cands = candidate_gems(class_spec, catalog)
    meta_ids = frozenset(c.item_id for c in cands if c.unique_equipped)
    metas = [c for c in cands if c.unique_equipped]
    nonmetas = [c for c in cands if not c.unique_equipped]

    val: dict[int, float] = {
        c.item_id: gem_survival_value(c.stats, marginals, dungeons) for c in cands
    }

    def _ranked(pool: list[GemCandidate]) -> list[tuple[GemCandidate, float]]:
        ordered = sorted(pool, key=lambda c: (-val[c.item_id], c.item_id))
        return [(c, val[c.item_id]) for c in ordered]

    # Rank each pool ONCE — identical across all sockets of the same kind.
    ranked_nonmeta = _ranked(nonmetas)
    ranked_all = _ranked(cands)  # meta socket: metas allowed alongside non-metas

    # Resolve each socket's current gem once: its catalog entry (or None for an
    # empty / non-catalog gem) and its survival value (0 if unknown/empty).
    cur_raw: list[dict | None] = [find_gem_by_id(s.gem_id) for s in sockets]
    current_candidate: list[GemCandidate | None] = [
        _to_candidate(c, primary) if c else None for c in cur_raw
    ]
    current_value: list[float] = [
        gem_survival_value(c.stats, marginals, dungeons) if c else 0.0 for c in current_candidate
    ]
    # An empty socket (gem_id None) is "known empty"; a non-catalog gem is unknown.
    current_known: list[bool] = [
        cur_raw[i] is not None or s.gem_id is None for i, s in enumerate(sockets)
    ]

    # Decide whether the unique meta is worth a socket, and if so, which one.
    best_meta_val = max((val[c.item_id] for c in metas), default=None)
    best_nonmeta_val = ranked_nonmeta[0][1] if ranked_nonmeta else None
    meta_worth_it = best_meta_val is not None and (
        best_nonmeta_val is None or best_meta_val > best_nonmeta_val
    )
    meta_socket_idx: int | None = None
    if meta_worth_it:
        held = [i for i, s in enumerate(sockets) if _is_meta_id(s.gem_id, meta_ids)]
        if held:
            # Keep the BEST already-placed meta where it is (zero churn). If two
            # sockets hold metas — only possible from an invalid loadout, since
            # the meta is Unique-Equipped — anchor to the higher-valued one.
            meta_socket_idx = max(held, key=lambda i: current_value[i])
        else:
            # No meta placed yet: put it where it gains the most (lowest current).
            meta_socket_idx = min(range(len(sockets)), key=lambda i: current_value[i])

    suggestions: list[GemSuggestion] = []
    for i, s in enumerate(sockets):
        is_meta_socket = i == meta_socket_idx
        ranked = ranked_all if is_meta_socket else ranked_nonmeta
        pool_best, pool_best_val = ranked[0] if ranked else (None, 0.0)
        # Never recommend a strict downgrade: if the socket's current gem beats
        # every gem we'd suggest, recommend KEEPING it (delta 0). This holds the
        # delta_ehp ≥ 0 invariant — the only way current can beat the pool is a
        # meta stranded in a non-meta socket (an invalid double-meta loadout).
        if current_candidate[i] is not None and current_value[i] >= pool_best_val:
            best: GemCandidate | None = current_candidate[i]
            best_val = current_value[i]
            delta = 0.0
        else:
            best = pool_best
            best_val = pool_best_val
            delta = pool_best_val - current_value[i]
        cur = current_candidate[i]
        # Composition terms for the WINNING candidate only, computed once
        # here (not for the whole ranked pool) — see GemSuggestion.best_terms.
        best_terms: dict[str, dict[str, float]] = {}
        if best is not None:
            _, best_terms = gem_survival_value(best.stats, marginals, dungeons, return_terms=True)
        suggestions.append(
            GemSuggestion(
                slot=s.slot,
                index=s.index,
                current_gem_id=s.gem_id,
                current_name=cur.name if cur else None,
                current_known=current_known[i],
                current_value=current_value[i],
                best=best,
                best_value=best_val,
                delta_ehp=delta,
                is_meta_socket=is_meta_socket,
                ranked=ranked,
                best_terms=best_terms,
            )
        )
    return suggestions


def sockets_from_equipped(equipped: dict) -> list[Socket]:
    """Flatten an equipped dict (slot → item with ``gem_ids``) into one
    ``Socket`` per socket on the item — filled AND empty.

    Handles both dataclass items (``ItemSpec.gem_ids``) and plain dicts
    (``item["gem_ids"]``), in Blizzard paperdoll slot order where the dict
    preserves it. Filled sockets come straight from ``gem_ids``. For empty
    sockets, ``gem_ids`` alone can't tell us they exist — an unfilled socket
    has no entry at all — so we additionally ask Wowhead for the item's own
    socket count (``fetch_item_socket_count_wowhead``, keyed by item_id +
    bonus_ids) and pad with ``Socket(gem_id=None)`` for any sockets beyond what
    ``gem_ids`` already accounts for. A ``None``/failed lookup (offline, item
    not found, no ``item_id`` on the item) leaves behavior unchanged — no
    sockets are synthesized.
    """
    out: list[Socket] = []
    for slot, item in equipped.items():
        if item is None:
            continue
        gem_ids = getattr(item, "gem_ids", None)
        if gem_ids is None and isinstance(item, dict):
            gem_ids = item.get("gem_ids")
        gem_ids = gem_ids or []
        for idx, gid in enumerate(gem_ids):
            if gid:
                out.append(Socket(slot=slot, index=idx, gem_id=int(gid)))

        item_id = getattr(item, "item_id", None)
        if item_id is None and isinstance(item, dict):
            item_id = item.get("item_id")
        if not item_id:
            continue
        bonus_ids = getattr(item, "bonus_ids", None)
        if bonus_ids is None and isinstance(item, dict):
            bonus_ids = item.get("bonus_ids")

        total = fetch_item_socket_count_wowhead(int(item_id), bonus_ids)
        if total is None:
            continue
        filled = sum(1 for gid in gem_ids if gid)
        for idx in range(filled, total):
            out.append(Socket(slot=slot, index=idx, gem_id=None))
    return out
