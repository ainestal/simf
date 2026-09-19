"""Trial-swap state — pure helper for the slot-modal terminal action.

When a user clicks "Try this" on an alternative inside the slot dialog, we
record an overlay swap rather than mutating the parsed-from-SimC equipped
dict. This keeps the original gear set intact for `Reset`, and lets every
caller (vault verdict, gear list, slot dialog) see a single merged view via
`apply_trials`.

Pure module — no streamlit imports. The app layer reads/writes
`session_state["_trial_swaps"]` and feeds it through these helpers.
"""

from __future__ import annotations

from dataclasses import dataclass

from simf.ui.helpers.trial_swap import TrialState, apply_trials


@dataclass
class _FakeItem:
    """Stand-in for ItemSpec — same duck type the helpers care about."""

    item_id: int
    name: str
    ilvl: int = 658


# ─── TrialState ────────────────────────────────────────────────────────────────


def test_trial_state_empty_is_inactive():
    assert TrialState({}).is_active is False


def test_trial_state_with_swap_is_active():
    item = _FakeItem(item_id=1, name="Foo")
    assert TrialState({"head": item}).is_active is True


def test_trial_state_with_swap_immutable_to_caller():
    """TrialState's dict shouldn't be mutable from outside — caller-side
    accidental mutation must not silently change app state."""
    item = _FakeItem(item_id=1, name="Foo")
    raw = {"head": item}
    ts = TrialState(raw)
    raw["head"] = _FakeItem(item_id=99, name="Surprise")
    assert ts.swaps["head"].item_id == 1


def test_trial_state_with_swap_returns_new_state():
    base = TrialState({})
    item = _FakeItem(item_id=1, name="Foo")
    next_state = base.with_swap("head", item)
    assert next_state.swaps == {"head": item}
    # Original is untouched.
    assert base.swaps == {}


def test_trial_state_with_swap_replaces_existing_slot():
    a = _FakeItem(item_id=1, name="A")
    b = _FakeItem(item_id=2, name="B")
    ts = TrialState({"head": a}).with_swap("head", b)
    assert ts.swaps["head"].item_id == 2
    assert len(ts.swaps) == 1


def test_trial_state_cleared_returns_empty():
    item = _FakeItem(item_id=1, name="Foo")
    ts = TrialState({"head": item, "hands": item})
    assert ts.cleared().swaps == {}
    assert ts.cleared().is_active is False


def test_trial_state_without_slot_drops_only_that_slot():
    """Per-slot revert: a tank stacking rings + trinket should be able to
    revert just the trinket without nuking the ring swaps."""
    a = _FakeItem(item_id=1, name="A")
    b = _FakeItem(item_id=2, name="B")
    c = _FakeItem(item_id=3, name="C")
    ts = TrialState({"finger1": a, "finger2": b, "trinket1": c})
    next_state = ts.without_slot("trinket1")
    assert set(next_state.swaps) == {"finger1", "finger2"}
    assert next_state.swaps["finger1"].item_id == 1
    # Original is untouched.
    assert set(ts.swaps) == {"finger1", "finger2", "trinket1"}


def test_trial_state_without_unknown_slot_is_noop():
    """Reverting a slot that was never trialed must not raise."""
    item = _FakeItem(item_id=1, name="Foo")
    ts = TrialState({"head": item})
    assert ts.without_slot("legs").swaps == {"head": item}


# ─── apply_trials ──────────────────────────────────────────────────────────────


def test_apply_trials_empty_is_passthrough():
    a = _FakeItem(item_id=1, name="A")
    equipped = {"head": a}
    out = apply_trials(equipped, TrialState({}))
    assert out == equipped
    # Different object identity expected — caller mutations of `out` must
    # not leak back into the baseline equipped dict.
    out["head"] = _FakeItem(item_id=99, name="x")
    assert equipped["head"].item_id == 1


def test_apply_trials_overlays_swap():
    old = _FakeItem(item_id=1, name="OldHelm")
    new = _FakeItem(item_id=2, name="NewHelm")
    out = apply_trials({"head": old}, TrialState({"head": new}))
    assert out["head"].item_id == 2


def test_apply_trials_adds_slot_missing_in_baseline():
    """Swap into an empty slot is valid — the user is filling an unequipped slot
    with a bag/vault candidate."""
    new = _FakeItem(item_id=2, name="NewBoot")
    out = apply_trials({}, TrialState({"feet": new}))
    assert out["feet"].item_id == 2


def test_apply_trials_preserves_non_swapped_slots():
    helm = _FakeItem(item_id=1, name="Helm")
    glove = _FakeItem(item_id=2, name="Glove")
    new_helm = _FakeItem(item_id=3, name="NewHelm")
    out = apply_trials(
        {"head": helm, "hands": glove},
        TrialState({"head": new_helm}),
    )
    assert out["head"].item_id == 3
    assert out["hands"].item_id == 2


def test_apply_trials_does_not_mutate_baseline():
    helm = _FakeItem(item_id=1, name="Helm")
    new_helm = _FakeItem(item_id=2, name="NewHelm")
    baseline = {"head": helm}
    apply_trials(baseline, TrialState({"head": new_helm}))
    assert baseline["head"].item_id == 1


# ─── Gem + enchant overrides (Phase 2 extension, 2026-05-20) ──────────────


@dataclass
class _GemmableItem:
    """ItemSpec-shaped dataclass — has enchant_id + gem_ids so the
    override merge has somewhere to land."""

    item_id: int
    name: str
    enchant_id: int | None = None
    gem_ids: list[int] | None = None
    ilvl: int = 658

    def __post_init__(self):
        if self.gem_ids is None:
            self.gem_ids = []


def test_trial_state_with_enchant_override():
    """Setting an enchant trial doesn't touch the item-swap channel —
    the user is keeping their equipped item but trying a different
    enchant."""
    ts = TrialState().with_enchant_override("head", 7000)
    assert ts.enchant_overrides == {"head": 7000}
    assert ts.swaps == {}
    assert ts.is_active is True


def test_trial_state_with_gem_override():
    ts = TrialState().with_gem_override("head", [213_491, 213_492])
    assert ts.gem_overrides == {"head": [213_491, 213_492]}
    assert ts.swaps == {}
    assert ts.is_active is True


def test_trial_state_affected_slots_unions_all_channels():
    """The banner needs to know which slots the user is trialing
    *anything* on, regardless of trial type."""
    ts = (
        TrialState()
        .with_swap("head", _FakeItem(1, "A"))
        .with_enchant_override("hands", 7100)
        .with_gem_override("trinket1", [12345])
    )
    assert ts.affected_slots == frozenset({"head", "hands", "trinket1"})


def test_trial_state_without_slot_drops_all_trial_types():
    """Per-slot revert must clear item-swap + enchant + gem all in one
    move so the user doesn't have to revert three things."""
    ts = (
        TrialState()
        .with_swap("head", _FakeItem(1, "A"))
        .with_enchant_override("head", 7100)
        .with_gem_override("head", [12345])
    )
    reverted = ts.without_slot("head")
    assert reverted.swaps == {}
    assert reverted.enchant_overrides == {}
    assert reverted.gem_overrides == {}
    assert reverted.is_active is False


def test_trial_state_overrides_are_immutable_to_caller():
    """A caller mutating the list they passed in must not reach into
    the frozen state — same defensive-copy guarantee as `swaps`."""
    gems = [12345, 12346]
    ts = TrialState().with_gem_override("head", gems)
    gems.append(99_999)
    assert ts.gem_overrides["head"] == [12345, 12346]


def test_apply_trials_patches_enchant_on_existing_item():
    """Enchant trial keeps the baseline item but replaces enchant_id."""
    helm = _GemmableItem(item_id=1, name="Helm", enchant_id=5000)
    ts = TrialState().with_enchant_override("head", 7777)
    out = apply_trials({"head": helm}, ts)
    assert out["head"].item_id == 1  # same item
    assert out["head"].enchant_id == 7777  # new enchant


def test_apply_trials_patches_gems_on_existing_item():
    """Gem trial keeps the baseline item but replaces gem_ids."""
    helm = _GemmableItem(item_id=1, name="Helm", gem_ids=[100])
    ts = TrialState().with_gem_override("head", [200, 201])
    out = apply_trials({"head": helm}, ts)
    assert out["head"].item_id == 1
    assert out["head"].gem_ids == [200, 201]


def test_apply_trials_item_swap_supersedes_enchant_override():
    """If both an item swap and an enchant override apply to the same
    slot, the full item wins (its own enchant_id is authoritative)."""
    old = _GemmableItem(item_id=1, name="Old", enchant_id=5000)
    new = _GemmableItem(item_id=2, name="New", enchant_id=6000)
    ts = TrialState().with_swap("head", new).with_enchant_override("head", 9999)
    out = apply_trials({"head": old}, ts)
    # Item swap takes precedence — the override's 9999 is irrelevant.
    assert out["head"].item_id == 2
    assert out["head"].enchant_id == 6000


def test_apply_trials_enchant_override_on_empty_slot_is_noop():
    """Defensive — an enchant override on a slot the user has nothing
    equipped at can't patch anything; quietly skip rather than crash."""
    ts = TrialState().with_enchant_override("head", 7777)
    out = apply_trials({}, ts)
    assert out == {}


def test_apply_trials_does_not_mutate_baseline_item_on_override():
    """Patching an enchant must produce a new item, not mutate the
    baseline — same guarantee `apply_trials` makes for the dict
    itself."""
    helm = _GemmableItem(item_id=1, name="Helm", enchant_id=5000, gem_ids=[100])
    baseline = {"head": helm}
    ts = TrialState().with_enchant_override("head", 7777).with_gem_override("head", [200])
    apply_trials(baseline, ts)
    # Baseline item is unchanged.
    assert baseline["head"].enchant_id == 5000
    assert baseline["head"].gem_ids == [100]


def test_apply_trials_overrides_on_dict_item_shape():
    """Some tests + the demo path use plain dicts. The override patcher
    handles them too."""
    helm = {"item_id": 1, "enchant_id": 5000, "gem_ids": [100]}
    ts = TrialState().with_enchant_override("head", 7777).with_gem_override("head", [200])
    out = apply_trials({"head": helm}, ts)
    assert out["head"]["enchant_id"] == 7777
    assert out["head"]["gem_ids"] == [200]
    # Baseline dict unchanged.
    assert helm["enchant_id"] == 5000
