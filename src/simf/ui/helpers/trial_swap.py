"""Trial-swap state for the slot-modal terminal action.

When a user clicks "Try this" on an alternative inside the slot dialog,
simf records an *overlay* swap rather than mutating the parsed-from-SimC
equipped dict. The verdict/gear/slot views all read a merged view via
``apply_trials``, so a single click changes what every surface shows; a
single click on "Reset" restores the original gear set.

A trial can take three forms:
  - **Item swap** — replace the whole item at a slot (e.g. "try the
    vault Trinket B"). Stored in ``swaps[slot] = item``.
  - **Enchant override** — keep the equipped item but swap the enchant
    only (e.g. "try Tooltip Frost vs Power"). Stored in
    ``enchant_overrides[slot] = enchant_id``.
  - **Gem override** — keep the equipped item but swap one socket
    (e.g. "try a Crit gem in socket 0"). Stored in
    ``gem_overrides[slot] = [gem_id_0, gem_id_1, ...]``.

Item swaps win over enchant/gem overrides on the same slot — if you
trial-swap the whole item, the previous enchant/gem on that slot is
irrelevant. ``apply_trials`` enforces this precedence.

This module is pure (no streamlit imports) so the swap semantics can be
unit-tested without booting an app.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from dataclasses import replace as dc_replace


@dataclass(frozen=True)
class TrialState:
    """Snapshot of pending trials across items, gems, and enchants.

    Phase 2 extension (2026-05-20) — gem + enchant overrides join the
    full-item swaps as first-class trial primitives so a user can ask
    "what if I re-gemmed haste→crit" without re-importing a modified
    SimC string. The verdict reacts to the merged result; reset reverts
    everything.
    """

    swaps: dict[str, object] = field(default_factory=dict)
    enchant_overrides: dict[str, int] = field(default_factory=dict)
    gem_overrides: dict[str, list[int]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Defensive copies so a caller's later mutation of the dicts/lists
        # it passed in can't reach into the frozen state.
        object.__setattr__(self, "swaps", dict(self.swaps))
        object.__setattr__(self, "enchant_overrides", dict(self.enchant_overrides))
        object.__setattr__(
            self, "gem_overrides", {k: list(v) for k, v in self.gem_overrides.items()}
        )

    @property
    def is_active(self) -> bool:
        return bool(self.swaps or self.enchant_overrides or self.gem_overrides)

    @property
    def affected_slots(self) -> frozenset[str]:
        """Slots that have ANY kind of trial on them — for the banner."""
        return (
            frozenset(self.swaps)
            | frozenset(self.enchant_overrides)
            | frozenset(self.gem_overrides)
        )

    def with_swap(self, slot: str, item: object) -> TrialState:
        next_swaps = dict(self.swaps)
        next_swaps[slot] = item
        return dc_replace(self, swaps=next_swaps)

    def with_enchant_override(self, slot: str, enchant_id: int) -> TrialState:
        next_enchants = dict(self.enchant_overrides)
        next_enchants[slot] = int(enchant_id)
        return dc_replace(self, enchant_overrides=next_enchants)

    def with_gem_override(self, slot: str, gem_ids: list[int]) -> TrialState:
        next_gems = {k: list(v) for k, v in self.gem_overrides.items()}
        next_gems[slot] = [int(g) for g in gem_ids]
        return dc_replace(self, gem_overrides=next_gems)

    def cleared(self) -> TrialState:
        return TrialState()

    def without_slot(self, slot: str) -> TrialState:
        """Revert ALL trials on a single slot — item swap + enchant + gem.

        Used by the per-slot chip in the banner: "× Helm" clears whatever
        the user was trialing on the helm, regardless of the trial type.
        No-op if ``slot`` was never trialed.
        """
        if slot not in self.affected_slots:
            return self
        return dc_replace(
            self,
            swaps={k: v for k, v in self.swaps.items() if k != slot},
            enchant_overrides={k: v for k, v in self.enchant_overrides.items() if k != slot},
            gem_overrides={k: v for k, v in self.gem_overrides.items() if k != slot},
        )


def apply_trials(equipped: dict, trial: TrialState) -> dict:
    """Return a new dict with trial overrides applied to baseline equipped.

    Precedence: full-item swap wins over enchant/gem override on the same
    slot. Enchant + gem overrides on a slot WITHOUT a full-item swap clone
    the equipped item (via dataclass.replace when possible, dict copy as
    fallback) and patch the relevant fields.

    Always returns a fresh dict — callers can mutate the result without
    leaking back into the baseline.
    """
    merged = dict(equipped)
    merged.update(trial.swaps)

    # Apply enchant + gem overrides to items NOT already replaced by a
    # full-item swap (a whole-item swap supersedes any partial trial on
    # the same slot).
    partial_slots = (set(trial.enchant_overrides) | set(trial.gem_overrides)) - set(trial.swaps)
    for slot in partial_slots:
        item = merged.get(slot)
        if item is None:
            # No baseline item at this slot — skip; nothing to override.
            continue
        patched_item = _patch_item(
            item,
            enchant_id=trial.enchant_overrides.get(slot),
            gem_ids=trial.gem_overrides.get(slot),
        )
        merged[slot] = patched_item
    return merged


def _patch_item(item: object, *, enchant_id: int | None, gem_ids: list[int] | None) -> object:
    """Return a copy of ``item`` with enchant_id / gem_ids overridden.

    Handles both dataclass items (the SimC parser's ItemSpec) and plain
    dicts (which some tests + the demo path use). Returns the original
    object only if neither override applies — defensive against callers
    that pass shapes neither dataclass nor mapping.
    """
    if enchant_id is None and gem_ids is None:
        return item
    # Dataclass shape: prefer dataclasses.replace so we don't reach into
    # private fields. Caller-side `__dataclass_fields__` check survives
    # frozen dataclasses too.
    if hasattr(item, "__dataclass_fields__"):
        kwargs: dict = {}
        if enchant_id is not None and "enchant_id" in item.__dataclass_fields__:
            kwargs["enchant_id"] = int(enchant_id)
        if gem_ids is not None and "gem_ids" in item.__dataclass_fields__:
            kwargs["gem_ids"] = list(gem_ids)
        if not kwargs:
            return item
        return dc_replace(item, **kwargs)
    # Dict shape — common in tests + demo flows.
    if isinstance(item, dict):
        patched = dict(item)
        if enchant_id is not None:
            patched["enchant_id"] = int(enchant_id)
        if gem_ids is not None:
            patched["gem_ids"] = list(gem_ids)
        return patched
    # Unknown shape — pass through.
    return item
