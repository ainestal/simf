"""Tests for the world-ceiling honesty caption on the verdict surface.

User-feedback origin (Brutoh, 2026-05-27): *"the highest keys in the
world with a prot warrior are currently +20, the highest in a spanish
server in EU is +19. So our calibration of 'can I survive this' is a
little off, according to the number of people that completed keys
over 16, it's not so easy to do."* The damage-stream math is fine; the
*framing* was missing a real-world ceiling. This test file pins:

  1. The loader returns a `WorldCeiling` row for every modelled tank
     spec — adding a spec without filling the YAML must fail loud.
  2. The caption renders when the verdict's claimed key level is at or
     above the spec's `broad_completion_key`.
  3. The caption stays silent when the claimed key is below
     `caveat_below` (the threshold at which the world ceiling becomes
     a relevant frame).
  4. Specs flagged `placeholder: true` still load cleanly — the flag
     is documentation hygiene, not a runtime gate.
  5. The caption helper renders nothing when called for a spec that
     isn't in the YAML table (defensive — adding a tank to the engine
     must not crash the verdict surface).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from simf.data.world_ceilings import (
    WorldCeiling,
    world_ceiling_for,
)
from simf.ui.app import _render_world_ceiling_caption, _verdict_claimed_key

MODELLED_TANK_SPECS = [
    "protection_warrior",
    "protection_paladin",
    "blood_death_knight",
    "vengeance_demon_hunter",
    "brewmaster_monk",
    "guardian_druid",
]


# ---- Loader ----------------------------------------------------------------


@pytest.mark.parametrize("spec", MODELLED_TANK_SPECS)
def test_loader_has_row_for_every_modelled_tank_spec(spec: str) -> None:
    """If a spec is in the engine but not in the YAML, the verdict
    surface silently skips its caption — that's the failure mode this
    test catches. Adding a tank without filling out the YAML must
    fail here, not later in production."""
    row = world_ceiling_for(spec)
    assert row is not None, f"Missing world_ceilings.yaml row for {spec}"
    assert isinstance(row, WorldCeiling)
    assert row.world_max_key > 0
    assert row.broad_completion_key > 0
    assert row.caveat_below > 0
    assert row.population_descriptor != ""


def test_loader_returns_none_for_unknown_spec() -> None:
    """Unknown spec → None, never an exception. Defensive against
    future spec strings that bypass the YAML check."""
    assert world_ceiling_for("definitely_not_a_real_spec") is None


def test_loader_caches_consistently() -> None:
    """Loader returns the same WorldCeiling instance across calls so
    callers can compare identities cheaply."""
    a = world_ceiling_for("protection_warrior")
    b = world_ceiling_for("protection_warrior")
    assert a is b


def test_placeholder_flag_loads_without_error() -> None:
    """The `placeholder: true` flag is metadata, not a runtime gate —
    placeholder rows must still produce usable `WorldCeiling`
    instances. The live WCL rankings layer (PR adding
    `simf.io.wcl_rankings`) may clear placeholder flags at the loader
    level when live data lands; the YAML continues to flag rows
    honestly as a documentation contract. Verify the YAML-level
    invariant directly so this assertion is independent of whether
    live creds are configured in the test environment."""
    from simf.data.world_ceilings import _load_yaml_rows

    rows = _load_yaml_rows()
    placeholder_count = sum(1 for r in rows.values() if r.placeholder)
    verified_count = sum(1 for r in rows.values() if not r.placeholder)
    # All rows accessible.
    for row in rows.values():
        assert isinstance(row.placeholder, bool)
        assert isinstance(row.world_max_key, int)
    # At least one verified (the Brutoh-anchored Prot Warrior row).
    assert verified_count >= 1
    # Placeholders exist — flagging them honestly is the point.
    assert placeholder_count >= 1


# ---- Claimed-key helper ----------------------------------------------------


class _StubVerdict:
    """Minimal stand-in for `KeyLevelVerdict` — just what the caption
    helper reads. Avoids the cost of spinning up a real sim."""

    def __init__(self, *, comfortable_max, displayable_prog):
        self.comfortable_max = comfortable_max
        self._displayable_prog = displayable_prog

    def displayable_prog_ceiling(self):
        return self._displayable_prog


def test_claimed_key_prefers_displayable_prog_ceiling() -> None:
    """The headline cites the prog ceiling when present — the caption
    must gate on the same value, or it'll fire at the wrong key."""
    v = _StubVerdict(comfortable_max=15, displayable_prog=18)
    assert _verdict_claimed_key(v) == 18


def test_claimed_key_falls_back_to_comfortable_max() -> None:
    """When every key is comfortable (displayable_prog is None), the
    headline cites comfortable_max — the caption follows."""
    v = _StubVerdict(comfortable_max=20, displayable_prog=None)
    assert _verdict_claimed_key(v) == 20


def test_claimed_key_returns_none_when_both_missing() -> None:
    """Empty sweep / undergeared — no ceiling to anchor against."""
    v = _StubVerdict(comfortable_max=None, displayable_prog=None)
    assert _verdict_claimed_key(v) is None


# ---- Caption render gate --------------------------------------------------


def test_caption_renders_above_broad_completion_key() -> None:
    """Claimed key ≥ broad_completion_key → caption fires. Prot
    Warrior threshold is +17, so a +17 verdict must trigger."""
    v = _StubVerdict(comfortable_max=15, displayable_prog=17)
    with patch("simf.ui.verdict.st") as st_mock:
        _render_world_ceiling_caption(v, "protection_warrior")
    assert st_mock.caption.called, "Caption should render at +17"
    text = st_mock.caption.call_args[0][0]
    assert "+17" in text
    assert "+20" in text  # world_max_key from the YAML
    assert "Prot Warriors" in text
    assert "Survivability is one of several gates" in text


def test_caption_suppressed_below_caveat_below() -> None:
    """Claimed key < caveat_below → silent. Prot Warrior caveat_below
    is +14, so a +13 verdict must NOT trigger the caption."""
    v = _StubVerdict(comfortable_max=13, displayable_prog=None)
    with patch("simf.ui.verdict.st") as st_mock:
        _render_world_ceiling_caption(v, "protection_warrior")
    assert not st_mock.caption.called, "Caption should be silent below +14"


def test_caption_suppressed_between_caveat_and_broad_completion() -> None:
    """Mid-band (caveat_below ≤ claimed_key < broad_completion_key) →
    silent. The player isn't being told to push into the rare tail
    yet — no need to clutter."""
    # Prot Warrior: caveat_below=14, broad_completion_key=17. +15
    # falls in the dead zone.
    v = _StubVerdict(comfortable_max=15, displayable_prog=None)
    with patch("simf.ui.verdict.st") as st_mock:
        _render_world_ceiling_caption(v, "protection_warrior")
    assert not st_mock.caption.called, "Caption should be silent between +14 and +17"


def test_caption_silent_for_unknown_spec() -> None:
    """Spec not in the YAML → no caption, no crash. Defensive against
    future tanks added to the engine before the YAML row lands."""
    v = _StubVerdict(comfortable_max=20, displayable_prog=None)
    with patch("simf.ui.verdict.st") as st_mock:
        _render_world_ceiling_caption(v, "not_a_real_spec")
    assert not st_mock.caption.called


def test_caption_uses_population_descriptor_from_yaml() -> None:
    """Brewmaster YAML row uses 'a few dozen' (rarer pool) — the
    caption must reflect that. Catches accidental hardcoding of
    'a few hundred' in the Python."""
    # Brewmaster: broad_completion_key=16, caveat_below=14. A +16
    # verdict triggers the caption.
    v = _StubVerdict(comfortable_max=14, displayable_prog=16)
    with patch("simf.ui.verdict.st") as st_mock:
        _render_world_ceiling_caption(v, "brewmaster_monk")
    assert st_mock.caption.called
    text = st_mock.caption.call_args[0][0]
    assert "a few dozen" in text
    assert "Brewmasters" in text


# ---- Live broad-count caption integration ---------------------------------


def test_caption_renders_live_count_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the live broad-count layer returns data, the caption reads
    the exact integer ("At least 1,253 Prot Warriors...") instead of
    the hand-wavy "Only a few hundred Prot Warriors..."."""
    from simf.data import world_ceilings as wc_module

    def fake_counts(spec_threshold_map, force_refresh: bool = False):
        return {
            "protection_warrior": {
                "broad_count": 1253,
                "broad_count_capped": False,
                "broad_threshold": spec_threshold_map["protection_warrior"],
            }
        }

    monkeypatch.setattr("simf.io.wcl_rankings.fetch_live_broad_completion_counts", fake_counts)
    wc_module.reset_cache()

    v = _StubVerdict(comfortable_max=15, displayable_prog=17)
    with patch("simf.ui.verdict.st") as st_mock:
        _render_world_ceiling_caption(v, "protection_warrior")

    assert st_mock.caption.called
    text = st_mock.caption.call_args[0][0]
    # Live count format — note the "At least" framing for the
    # single-encounter lower-bound caveat.
    assert "At least 1,253" in text
    assert "Prot Warriors" in text
    # Threshold is broad_completion_key (17), not the claimed key.
    assert "+17 or higher" in text
    # Hand-wavy "Only" copy must be gone when live data is present.
    assert "Only" not in text
    assert "a few hundred" not in text


def test_caption_marks_capped_count_with_plus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A capped live count reads "At least N+ ..." — the "+" signals the
    number is a floor (WCL truncated the response)."""
    from simf.data import world_ceilings as wc_module

    def fake_counts(spec_threshold_map, force_refresh: bool = False):
        return {
            "protection_warrior": {
                "broad_count": 2000,
                "broad_count_capped": True,
                "broad_threshold": spec_threshold_map["protection_warrior"],
            }
        }

    monkeypatch.setattr("simf.io.wcl_rankings.fetch_live_broad_completion_counts", fake_counts)
    wc_module.reset_cache()

    v = _StubVerdict(comfortable_max=15, displayable_prog=17)
    with patch("simf.ui.verdict.st") as st_mock:
        _render_world_ceiling_caption(v, "protection_warrior")

    assert st_mock.caption.called
    text = st_mock.caption.call_args[0][0]
    assert "At least 2,000+" in text


def test_caption_falls_back_when_live_count_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live layer returns None (creds missing, network down, every spec
    yielded 0) → caption uses the static `population_descriptor`."""
    from simf.data import world_ceilings as wc_module

    # Both live layers return None — this is the default conftest
    # stub, but we re-assert it explicitly for clarity.
    monkeypatch.setattr(
        "simf.io.wcl_rankings.fetch_live_broad_completion_counts",
        lambda *a, **kw: None,
    )
    wc_module.reset_cache()

    v = _StubVerdict(comfortable_max=15, displayable_prog=17)
    with patch("simf.ui.verdict.st") as st_mock:
        _render_world_ceiling_caption(v, "protection_warrior")

    assert st_mock.caption.called
    text = st_mock.caption.call_args[0][0]
    assert "Only" in text
    assert "a few hundred" in text
