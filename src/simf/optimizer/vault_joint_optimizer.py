"""Vault × Bag joint loadout optimization.

For each vault pick, find the single best bag-item swap (if any) that pairs
with it, scored across one or more user-selected dungeons using each dungeon's
school_mix to weight physical-vs-magic eHP gain.

This answers brutoh's actual Tuesday-morning question:
    "Take vault helm AND equip bag ring → +412 eHP at Workshop,
     +180 at Necrotic, +220 avg"

The existing Vault tab scores items independently and dungeon-agnostic
(fixed 0.65/0.35 phys/magic split). This module is dungeon-aware and joint.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Avoid hard import dependency on UI / item_db at module load — passed in.


@dataclass
class JointLoadout:
    """One vault pick + optionally one bag swap, scored per dungeon."""

    vault_spec: object  # ItemSpec
    bag_swap: object | None = None  # ItemSpec or None (no bag swap)
    bag_swap_slot: str | None = None  # which equipped slot the bag swap replaces
    delta_stats: dict[str, int] = field(default_factory=dict)
    score_per_dungeon: dict[str, float] = field(default_factory=dict)  # dungeon_id -> ΔeHP

    @property
    def avg_score(self) -> float:
        if not self.score_per_dungeon:
            return 0.0
        return sum(self.score_per_dungeon.values()) / len(self.score_per_dungeon)


def _stat_delta(
    equipped_stats: dict[str, int] | None, new_stats: dict[str, int] | None
) -> dict[str, int]:
    """Compute (new - equipped). Returns only non-zero entries."""
    e = equipped_stats or {}
    n = new_stats or {}
    keys = set(e) | set(n)
    return {k: n.get(k, 0) - e.get(k, 0) for k in keys if n.get(k, 0) != e.get(k, 0)}


def _score_delta_for_dungeon(
    delta: dict[str, int], marginals: dict, school_mix: dict[str, float]
) -> float:
    """Convert a stat delta into ΔeHP weighted by the dungeon's school distribution.

    school_mix is e.g. {"physical": 0.65, "shadow": 0.25, "nature": 0.10}.
    Everything non-physical is treated as 'magic' eHP for marginal-lookup purposes —
    marginals only distinguish phys vs magic.
    """
    phys_frac = float(school_mix.get("physical", 0.0))
    magic_frac = 1.0 - phys_frac  # everything not phys → magic for eHP marginal purposes

    d_phys = sum(marginals.get(k, {"p": 0.0})["p"] * v for k, v in delta.items())
    d_mag = sum(marginals.get(k, {"m": 0.0})["m"] * v for k, v in delta.items())
    return phys_frac * d_phys + magic_frac * d_mag


def enumerate_joint_loadouts(
    vault_items: list,
    bag_items_by_slot: dict[str, list],
    equipped: dict[str, object],
    selected_dungeons: list[dict],
    marginals: dict,
    item_stats_fn,
) -> list[JointLoadout]:
    """Enumerate (vault_pick × optional bag_swap) loadouts.

    Args:
        vault_items: list of ItemSpec — this week's 3 vault choices.
        bag_items_by_slot: dict[slot, list[ItemSpec]] — bag items grouped by slot.
        equipped: dict[slot, ItemSpec] — currently equipped baseline.
        selected_dungeons: list of dungeon dicts (must have id + school_mix).
            Empty = use a single generic 0.65/0.35 split.
        marginals: ehp_marginals(char) output.
        item_stats_fn: callable(item_spec) -> dict[str, int] of stats (or None).

    Returns:
        One JointLoadout per vault pick. For each vault pick, the joint
        loadout includes the SINGLE BEST bag swap pairing it with (if any
        swap improves the average score across selected dungeons over the
        vault-only baseline). Sorted by avg_score descending.
    """
    if not selected_dungeons:
        # No dungeons selected → score against a generic profile (fixed split).
        selected_dungeons = [{"id": "generic", "school_mix": {"physical": 0.65}}]

    results: list[JointLoadout] = []

    for vault_spec in vault_items:
        vault_slot = getattr(vault_spec, "slot", None)
        if not vault_slot:
            continue

        # --- Baseline: equip vault item, no bag swap ---
        vault_stats = item_stats_fn(vault_spec) or {}
        eq_in_vault_slot = equipped.get(vault_slot)
        eq_vault_stats = item_stats_fn(eq_in_vault_slot) if eq_in_vault_slot else {}
        base_delta = _stat_delta(eq_vault_stats, vault_stats)
        base_score_per_d = {
            d["id"]: _score_delta_for_dungeon(base_delta, marginals, d.get("school_mix") or {})
            for d in selected_dungeons
        }
        base_loadout = JointLoadout(
            vault_spec=vault_spec,
            delta_stats=base_delta,
            score_per_dungeon=base_score_per_d,
        )

        # --- Try every candidate bag swap (excluding bag items in the vault's slot;
        # the vault pick already fills that slot) ---
        best = base_loadout
        for slot, bag_list in bag_items_by_slot.items():
            if slot == vault_slot:
                continue
            for bag_spec in bag_list:
                bag_stats = item_stats_fn(bag_spec) or {}
                eq_in_slot = equipped.get(slot)
                eq_slot_stats = item_stats_fn(eq_in_slot) if eq_in_slot else {}
                bag_delta = _stat_delta(eq_slot_stats, bag_stats)
                # Combined delta = vault delta + bag delta (independent slots)
                combined_delta: dict[str, int] = dict(base_delta)
                for k, v in bag_delta.items():
                    combined_delta[k] = combined_delta.get(k, 0) + v
                combined_score = {
                    d["id"]: _score_delta_for_dungeon(
                        combined_delta, marginals, d.get("school_mix") or {}
                    )
                    for d in selected_dungeons
                }
                candidate = JointLoadout(
                    vault_spec=vault_spec,
                    bag_swap=bag_spec,
                    bag_swap_slot=slot,
                    delta_stats=combined_delta,
                    score_per_dungeon=combined_score,
                )
                if candidate.avg_score > best.avg_score:
                    best = candidate
        results.append(best)

    results.sort(key=lambda r: -r.avg_score)
    return results
