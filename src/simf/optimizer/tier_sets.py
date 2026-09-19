"""Tier-set tracker — counts equipped pieces of a Midnight S1 tier set and
reports the currently active threshold (2pc or 4pc).

Brutoh feedback (2026-05-16): "Without tier-set tracking I still verify in
WoW before clicking — a trial-swap that breaks 4pc is the difference
between '+660 eHP weapon' and '+400 eHP shoulders that preserve my 4pc.'"

The registry is hand-curated in `data/tier_sets.yaml`. Each set declares
its spec and the item ids that count toward it. Item-id-based detection
is deliberately simple — Catalyst-converted tokens reuse the same id, so
this is more robust than parsing item names.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_TIER_SETS_FILE = _DATA_DIR / "tier_sets.yaml"


@dataclass(frozen=True)
class TierSet:
    id: str
    name: str
    spec: str
    item_ids: frozenset[int]
    bonus_2pc: str = ""
    bonus_4pc: str = ""


@dataclass(frozen=True)
class SetStatus:
    """How many pieces of a given set are equipped, and which bonus is live."""

    set: TierSet
    pieces: int
    active_threshold: int  # highest threshold satisfied; 0 when below 2pc
    active_bonus: str = ""


@lru_cache(maxsize=1)
def load_tier_sets() -> list[TierSet]:
    """Load + cache the tier-set registry. Returns empty list if the yaml
    file is missing — caller behaves as if no sets are defined."""
    if not _TIER_SETS_FILE.exists():
        return []
    with _TIER_SETS_FILE.open() as f:
        data = yaml.safe_load(f) or {}
    return [
        TierSet(
            id=s["id"],
            name=s["name"],
            spec=s["spec"],
            item_ids=frozenset(int(i) for i in s.get("item_ids", [])),
            bonus_2pc=s.get("bonus_2pc", ""),
            bonus_4pc=s.get("bonus_4pc", ""),
        )
        for s in data.get("sets", [])
    ]


def _count_pieces(equipped: dict, set_ids: frozenset[int]) -> int:
    return sum(
        1
        for item in equipped.values()
        if item is not None and getattr(item, "item_id", 0) in set_ids
    )


def _active_threshold(pieces: int) -> int:
    """2pc / 4pc tiers — >4 still caps at 4 (Blizzard doesn't ship 6pc)."""
    if pieces >= 4:
        return 4
    if pieces >= 2:
        return 2
    return 0


def equipped_set_status(equipped: dict, class_spec: str) -> list[SetStatus]:
    """SetStatus per tier set that this spec can wear, when at least one
    piece is equipped. UI typically filters to `active_threshold >= 2`."""
    out: list[SetStatus] = []
    for tier in load_tier_sets():
        if tier.spec != class_spec:
            continue
        n = _count_pieces(equipped, tier.item_ids)
        if n <= 0:
            continue
        threshold = _active_threshold(n)
        bonus = ""
        if threshold == 4:
            bonus = tier.bonus_4pc
        elif threshold == 2:
            bonus = tier.bonus_2pc
        out.append(SetStatus(set=tier, pieces=n, active_threshold=threshold, active_bonus=bonus))
    return out


def tier_status_by_item_id(equipped: dict, class_spec: str) -> dict[int, SetStatus]:
    """Every equipped tier-set item's id mapped to its ``SetStatus``, for a
    per-card lookup (``item_html._slot_card_html``'s tier badge).

    Brutoh feedback (2026-07-28): the header strip's aggregate badge
    (``load._tier_sets_badge_html``) names the set and its pc-count once for
    the whole page, but says nothing about WHICH equipped pieces are the ones
    that count — a reader has to recognize "Night Ender's Breastplate" as a
    set piece by name alone, checking one card at a time against the header.
    This answers that per-card, straight from the same ``equipped_set_status``
    the header badge already computes — no separate counting logic to drift
    out of sync."""
    out: dict[int, SetStatus] = {}
    for status in equipped_set_status(equipped, class_spec):
        for item_id in status.set.item_ids:
            out[item_id] = status
    return out


def swap_breaks_threshold(
    equipped: dict,
    class_spec: str,
    swap_slot: str,
    swap_item,
) -> SetStatus | None:
    """When applying `swap_item` to `swap_slot` drops the user below an
    active threshold, return the pre-swap SetStatus so the UI can warn.
    None when no threshold is broken."""
    before = {s.set.id: s for s in equipped_set_status(equipped, class_spec)}
    after_equipped = dict(equipped)
    after_equipped[swap_slot] = swap_item
    after = {s.set.id: s for s in equipped_set_status(after_equipped, class_spec)}
    for set_id, pre in before.items():
        if pre.active_threshold <= 0:
            continue
        post = after.get(set_id)
        post_threshold = post.active_threshold if post else 0
        if post_threshold < pre.active_threshold:
            return pre
    return None


def swap_completes_threshold(
    equipped: dict,
    class_spec: str,
    swap_slot: str,
    swap_item,
) -> SetStatus | None:
    """Inverse of :func:`swap_breaks_threshold`: when applying ``swap_item``
    to ``swap_slot`` *raises* the user to a higher active set threshold,
    return the post-swap SetStatus so the UI can flag the gained bonus.
    None when no new threshold is reached.

    Why this matters for upgrade-normalized comparison: vault rewards are
    very often tier pieces. A stats-only ΔeHP comparison undersells a vault
    piece that would complete a 2pc/4pc — the set bonus is worth far more
    than the raw secondary delta the scorer sees. ``swap_breaks_threshold``
    already warns when a swap *loses* a bonus; this is the symmetric note
    for *gaining* one, so a normalized comparison doesn't make the player
    reject a real upgrade.
    """
    before = {s.set.id: s for s in equipped_set_status(equipped, class_spec)}
    after_equipped = dict(equipped)
    after_equipped[swap_slot] = swap_item
    after = {s.set.id: s for s in equipped_set_status(after_equipped, class_spec)}
    for set_id, post in after.items():
        if post.active_threshold <= 0:
            continue
        pre = before.get(set_id)
        pre_threshold = pre.active_threshold if pre else 0
        if post.active_threshold > pre_threshold:
            return post
    return None
