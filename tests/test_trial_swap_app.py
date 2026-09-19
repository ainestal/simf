"""AppTest integration for the slot-modal terminal action.

These tests pin the contract between the trial-swap state model and the
Streamlit app: the verdict, gear list, and slot dialog all read a merged
equipped view; the "Reset trial" affordance restores the baseline.

Streamlit dialogs are tricky to assert against in AppTest — buttons inside
``@st.dialog`` render but their inclusion in ``app.button`` depends on the
dialog being open at the moment of the run. We assert the parts we can:
state propagation, reset button, banner visibility.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).parent.parent / "src" / "simf" / "ui" / "app.py"


def _baseline_char_state() -> dict:
    return {
        "name": "TestTank",
        "race": "human",
        "class_spec": "protection_warrior",
        "talents": "kiratank-defensive",
        "strength": 2000,
        "stamina": 32000,
        "armor_from_gear": 5000,
        "haste_rating": 2000,
        "crit_rating": 1200,
        "mastery_rating": 1500,
        "versatility_rating": 300,
    }


@pytest.fixture
def app() -> AppTest:
    # 10s default_timeout was tight enough that the trial-swap suite
    # flaked under xdist load on Pi-class hardware after the app surface
    # grew (dungeon-loot pipeline, upgrade panel, ladder). The same tests
    # pass in ~18s solo; 60s absorbs worst-case Pi load (load average 6+
    # was still reproducing 4/7 fails at 30s).
    return AppTest.from_file(str(APP_PATH), default_timeout=60)


def test_trial_swap_state_survives_rerun(app):
    """A trial swap in session_state must persist across reruns and be
    visible to downstream consumers via the merged equipped view."""
    from simf.io.simc_import import ItemSpec

    helm = ItemSpec(slot="head", item_id=100, name="OldHelm", ilvl=658)
    vault_helm = ItemSpec(slot="head", item_id=200, name="VaultHelm", ilvl=665)

    app.session_state["char_data"] = _baseline_char_state()
    app.session_state["simc_equipped"] = {"head": helm}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {"head": [vault_helm]}
    app.session_state["_trial_swaps"] = {"head": vault_helm}
    app.session_state["view"] = "gear"

    app.run()
    assert not app.exception, f"Unhandled exception: {app.exception}"
    assert "_trial_swaps" in app.session_state
    assert app.session_state["_trial_swaps"]["head"].item_id == 200


def test_trial_banner_visible_when_swap_active(app):
    """When a trial is active, the gear surface must show a "Trial active"
    banner with a Reset affordance — otherwise the swap is silent and the
    user has no way to discover or revert it."""
    from simf.io.simc_import import ItemSpec

    helm = ItemSpec(slot="head", item_id=100, name="OldHelm", ilvl=658)
    vault_helm = ItemSpec(slot="head", item_id=200, name="VaultHelm", ilvl=665)

    app.session_state["char_data"] = _baseline_char_state()
    app.session_state["simc_equipped"] = {"head": helm}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {"head": [vault_helm]}
    app.session_state["_trial_swaps"] = {"head": vault_helm}
    app.session_state["view"] = "gear"

    app.run()
    assert not app.exception
    body = "\n".join(str(m.value) for m in app.markdown)
    assert "trial" in body.lower() or "trying" in body.lower()
    # And a "Reset" button must exist somewhere on the page.
    button_labels = [b.label for b in app.button]
    assert any("reset" in (label or "").lower() for label in button_labels)


def test_trial_banner_absent_when_no_swap(app):
    """Empty trial state → no banner clutter on the gear surface."""
    from simf.io.simc_import import ItemSpec

    helm = ItemSpec(slot="head", item_id=100, name="OldHelm", ilvl=658)
    app.session_state["char_data"] = _baseline_char_state()
    app.session_state["simc_equipped"] = {"head": helm}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {}
    app.session_state["view"] = "gear"

    app.run()
    assert not app.exception
    body = "\n".join(str(m.value) for m in app.markdown).lower()
    assert "trial active" not in body


def test_reset_button_clears_trial_state(app):
    """Clicking Reset must wipe the trial overlay so the baseline returns."""
    from simf.io.simc_import import ItemSpec

    helm = ItemSpec(slot="head", item_id=100, name="OldHelm", ilvl=658)
    vault_helm = ItemSpec(slot="head", item_id=200, name="VaultHelm", ilvl=665)

    app.session_state["char_data"] = _baseline_char_state()
    app.session_state["simc_equipped"] = {"head": helm}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {"head": [vault_helm]}
    app.session_state["_trial_swaps"] = {"head": vault_helm}
    app.session_state["view"] = "gear"

    app.run()
    assert not app.exception
    reset_buttons = [b for b in app.button if "reset" in (b.label or "").lower()]
    assert reset_buttons, "Expected a Reset button when a trial is active"
    reset_buttons[0].click().run()
    assert not app.exception
    # State is gone either as an empty dict or removed entirely.
    # SafeSessionState (AppTest) has no `.get()` — fall back to membership check.
    if "_trial_swaps" in app.session_state:  # noqa: SIM108
        swaps = app.session_state["_trial_swaps"]
    else:
        swaps = {}
    assert not swaps


def test_per_slot_reset_drops_only_that_slot(app):
    """Stacking ring + trinket + neck swaps is the Tuesday-vault scenario.
    Reverting one slot must leave the other swaps intact — otherwise the
    user redoes two swaps to undo one bad pick."""
    from simf.io.simc_import import ItemSpec

    ring_a = ItemSpec(slot="finger1", item_id=11, name="RingA", ilvl=658)
    ring_b = ItemSpec(slot="finger2", item_id=12, name="RingB", ilvl=658)
    trinket = ItemSpec(slot="trinket1", item_id=21, name="Tk1", ilvl=665)
    swap_ring = ItemSpec(slot="finger1", item_id=110, name="VaultRing", ilvl=665)
    swap_tk = ItemSpec(slot="trinket1", item_id=210, name="VaultTk", ilvl=672)

    app.session_state["char_data"] = _baseline_char_state()
    app.session_state["simc_equipped"] = {
        "finger1": ring_a,
        "finger2": ring_b,
        "trinket1": trinket,
    }
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {}
    app.session_state["_trial_swaps"] = {"finger1": swap_ring, "trinket1": swap_tk}
    app.session_state["view"] = "gear"

    app.run()
    assert not app.exception
    # Per-slot reset buttons must exist for each active swap.
    ring_reset = [b for b in app.button if b.key == "trial_reset_finger1"]
    trinket_reset = [b for b in app.button if b.key == "trial_reset_trinket1"]
    assert ring_reset and trinket_reset
    # Reset just the ring.
    ring_reset[0].click().run()
    assert not app.exception
    # SafeSessionState (AppTest) has no `.get()` — fall back to membership check.
    if "_trial_swaps" in app.session_state:  # noqa: SIM108
        remaining = app.session_state["_trial_swaps"]
    else:
        remaining = {}
    assert "finger1" not in remaining
    assert remaining.get("trinket1") is not None


def test_reset_all_appears_only_with_multiple_swaps(app):
    """A single trial swap doesn't need a "Reset all" button — the per-slot
    Reset already covers it. Two or more swaps unlock the nuke-all."""
    from simf.io.simc_import import ItemSpec

    a = ItemSpec(slot="head", item_id=100, name="Old", ilvl=658)
    new_a = ItemSpec(slot="head", item_id=200, name="New", ilvl=665)

    app.session_state["char_data"] = _baseline_char_state()
    app.session_state["simc_equipped"] = {"head": a}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {}
    app.session_state["_trial_swaps"] = {"head": new_a}
    app.session_state["view"] = "gear"

    app.run()
    assert not app.exception
    reset_all = [b for b in app.button if b.key == "trial_reset"]
    assert not reset_all, "Reset all should not appear with one swap"


def test_trial_banner_count_singular_with_one_swap(app):
    """ROADMAP 2026-05-18: "Trial active" was anonymous; specificity rule
    says we should expose the slot count. One swap → "1 slot" (singular)."""
    from simf.io.simc_import import ItemSpec

    helm = ItemSpec(slot="head", item_id=100, name="OldHelm", ilvl=658)
    vault_helm = ItemSpec(slot="head", item_id=200, name="VaultHelm", ilvl=665)

    app.session_state["char_data"] = _baseline_char_state()
    app.session_state["simc_equipped"] = {"head": helm}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {"head": [vault_helm]}
    app.session_state["_trial_swaps"] = {"head": vault_helm}
    app.session_state["view"] = "gear"

    app.run()
    assert not app.exception
    body = "\n".join(str(m.value) for m in app.markdown)
    # Banner exposes "1 slot" (no plural s) when exactly one swap is active.
    assert "1 slot" in body
    assert "1 slots" not in body


def test_trial_banner_count_plural_with_multiple_swaps(app):
    """Two+ trial swaps → "N slots" (pluralised). The count gives sighted
    users immediate scope without reading the SR-only swap summary."""
    from simf.io.simc_import import ItemSpec

    ring_a = ItemSpec(slot="finger1", item_id=11, name="RingA", ilvl=658)
    ring_b = ItemSpec(slot="finger2", item_id=12, name="RingB", ilvl=658)
    trinket = ItemSpec(slot="trinket1", item_id=21, name="Tk1", ilvl=665)
    swap_ring = ItemSpec(slot="finger1", item_id=110, name="VaultRing", ilvl=665)
    swap_tk = ItemSpec(slot="trinket1", item_id=210, name="VaultTk", ilvl=672)

    app.session_state["char_data"] = _baseline_char_state()
    app.session_state["simc_equipped"] = {
        "finger1": ring_a,
        "finger2": ring_b,
        "trinket1": trinket,
    }
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {}
    app.session_state["_trial_swaps"] = {"finger1": swap_ring, "trinket1": swap_tk}
    app.session_state["view"] = "gear"

    app.run()
    assert not app.exception
    body = "\n".join(str(m.value) for m in app.markdown)
    assert "2 slots" in body


def test_trial_banner_gem_only_trial_does_not_crash(app):
    """2026-08-07 regression: `_render_trial_banner` only ever iterated
    `_trial_swaps`, so a gem-only trial (`_trial_gems` set, `_trial_swaps`
    empty) computed 0 slots and crashed on `st.columns(0)`
    (StreamlitInvalidColumnSpecError) even though `trial.is_active` was True.
    A fully-gemmed neck (one socket, no empty sockets) needs no network call
    to size — `sockets_from_equipped` only hits Wowhead for sockets beyond
    what `gem_ids` already accounts for."""
    from simf.io.simc_import import ItemSpec

    neck = ItemSpec(slot="neck", item_id=50228, name="Choker", ilvl=289, gem_ids=[240983])

    app.session_state["char_data"] = _baseline_char_state()
    app.session_state["simc_equipped"] = {"neck": neck}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {}
    app.session_state["_trial_gems"] = {"neck": [240894]}  # trialing Flawless Versatile Peridot
    app.session_state["view"] = "gear"

    app.run()
    assert not app.exception, f"Unhandled exception: {app.exception}"
    body = "\n".join(str(m.value) for m in app.markdown)
    assert "1 slot" in body
    assert "1 slots" not in body
    reset_buttons = [b for b in app.button if b.key == "trial_reset_neck"]
    assert reset_buttons


def test_trial_banner_enchant_only_trial_does_not_crash(app):
    """Same regression as the gem-only case, for `_trial_enchants`."""
    from simf.io.simc_import import ItemSpec

    chest = ItemSpec(slot="chest", item_id=1, name="Breastplate", ilvl=289, enchant_id=7987)

    app.session_state["char_data"] = _baseline_char_state()
    app.session_state["simc_equipped"] = {"chest": chest}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {}
    app.session_state["_trial_enchants"] = {"chest": 1236054}  # trialing Mark of Nalorakk
    app.session_state["view"] = "gear"

    app.run()
    assert not app.exception, f"Unhandled exception: {app.exception}"
    body = "\n".join(str(m.value) for m in app.markdown)
    assert "1 slot" in body
    reset_buttons = [b for b in app.button if b.key == "trial_reset_chest"]
    assert reset_buttons


def test_trial_banner_mixes_swap_and_gem_only_slots(app):
    """A full-item swap on one slot plus a gem-only trial on another must
    both show up in the banner's slot count — the two code paths
    (`_trial_delta_vs_baseline` for the swap, `_partial_trial_summary` for
    the gem) coexist in the same render."""
    from simf.io.simc_import import ItemSpec

    helm = ItemSpec(slot="head", item_id=100, name="OldHelm", ilvl=658)
    vault_helm = ItemSpec(slot="head", item_id=200, name="VaultHelm", ilvl=665)
    neck = ItemSpec(slot="neck", item_id=50228, name="Choker", ilvl=289, gem_ids=[240983])

    app.session_state["char_data"] = _baseline_char_state()
    app.session_state["simc_equipped"] = {"head": helm, "neck": neck}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {"head": [vault_helm]}
    app.session_state["_trial_swaps"] = {"head": vault_helm}
    app.session_state["_trial_gems"] = {"neck": [240894]}
    app.session_state["view"] = "gear"

    app.run()
    assert not app.exception, f"Unhandled exception: {app.exception}"
    body = "\n".join(str(m.value) for m in app.markdown)
    assert "2 slots" in body


def test_equipped_view_is_merged_for_gear_list(app):
    """The gear list should show the trial item in the swapped slot — not
    the parsed-from-SimC baseline."""
    from simf.io.simc_import import ItemSpec

    helm = ItemSpec(slot="head", item_id=100, name="BaselineHelm", ilvl=658)
    trial_helm = ItemSpec(slot="head", item_id=200, name="TrialHelm", ilvl=665)

    app.session_state["char_data"] = _baseline_char_state()
    app.session_state["simc_equipped"] = {"head": helm}
    app.session_state["simc_bag_items"] = {}
    app.session_state["simc_vault_items"] = {}
    app.session_state["_trial_swaps"] = {"head": trial_helm}
    app.session_state["view"] = "gear"

    app.run()
    assert not app.exception
    body = "\n".join(str(m.value) for m in app.markdown)
    # Gear list renders item names. The trial item is what we want to see.
    assert "TrialHelm" in body
    assert "BaselineHelm" not in body
