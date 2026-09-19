"""Phase 4 Brewmaster calibration outcome + the shield_armor hydrate gap it
surfaced (2026-06-07).

Full record: docs/validation/phase4_brewmaster_calibration_2026_06_07.md.
These pin the *decisions*, not the measured magnitudes (+27%/+57% are
iteration/seed-dependent and deliberately not asserted here).
"""

from __future__ import annotations

from simf.core.character import Character
from simf.core.constants import load_constants, spec_is_calibrated
from simf.io.character_from_combatant_info import hydrate_character
from tests.test_character_from_combatant_info import (
    _damage_line,
    _make_combatant_info_line,
)


def test_brewmaster_uncalibrated_with_stagger_constants():
    """Brewmaster stays `characterized`, not `calibrated` (Top-5 #4,
    2026-07-06): the 2026-06-07 local-log validation over-predicted damage
    at the canonical K=3430 (real model gap, confirmed against a warrior
    positive control). The mitigation knobs the replay chain reads must
    still be present so the engine doesn't KeyError."""
    c = load_constants()
    spec = c["specs"].get("brewmaster_monk")
    assert spec is not None, "brewmaster_monk specs block is missing"
    assert spec["calibration_tier"] == "characterized"
    assert not spec_is_calibrated(spec)
    # Spot-check the stagger knobs the mitigation chain reads.
    assert spec["stagger_pct_physical"] > 0
    assert spec["stagger_pct_magic"] > 0
    assert spec["stagger_dot_duration_s"] > 0
    assert spec["purifying_brew_clear_pct"] > 0


def test_global_k_is_3430_not_a_per_spec_constant():
    """K is one global armor constant; per-spec calibration validates against
    it rather than fitting a per-spec K. Guards the stale-comment cleanup."""
    c = load_constants()
    assert c["armor"]["k_constant"] == 3430


# off_hand is index 16 in the COMBATANT_INFO gear slot order
# (head..main_hand = 0..15, off_hand = 16). A shield tank's off-hand carries
# armor; a fist-weapon tank's does not.
_SHIELD_OFF_HAND_GEAR = [(0, 0, None, (), ()) for _ in range(16)] + [
    (237831, 285, None, (12214,), ())  # off_hand shield
]


def test_hydrate_no_shield_armor_without_resolver(tmp_path):
    """`shield_armor` (the off-hand shield's isolated armor) is NOT in
    COMBATANT_INFO's flat fields (field [24] is whole-character armor), so a
    hydrate with no resolver can't produce it. Documents why a resolver is
    required — and why the standalone tooling needs `--shield-armor` for shield
    specs. See docs/validation/phase4_brewmaster_calibration_2026_06_07.md."""
    log = tmp_path / "acl.log"
    log.write_text(
        _make_combatant_info_line(
            "5/10/2026 12:00:00.000", "Player-1-T", spec_id=73, gear=_SHIELD_OFF_HAND_GEAR
        )
        + _damage_line("5/10/2026 12:00:01.000", "Player-1-T", "Tankman-Uldum-EU")
    )
    hyd = hydrate_character(log, "Tankman-Uldum-EU")
    assert hyd is not None
    assert "shield_armor" not in hyd.char_data
    assert Character.from_dict(hyd.char_data).shield_armor == 0


def test_hydrate_sources_shield_armor_from_off_hand_via_resolver(tmp_path):
    """The fix (2026-06-07): when given a resolver, hydrate sources
    `shield_armor` from the equipped off-hand by item_id — so a shield tank
    loaded via the ACL "skip SimC paste" path no longer runs with block value
    ≈ 0 (the ~24pp over-pessimistic-verdict bug). The UI's
    `_cached_hydrate_character` passes `item_db.resolve_equipped_stats`; this
    test uses a network-free fake resolver to pin the wiring."""
    log = tmp_path / "acl.log"
    log.write_text(
        _make_combatant_info_line(
            "5/10/2026 12:00:00.000", "Player-1-T", spec_id=73, gear=_SHIELD_OFF_HAND_GEAR
        )
        + _damage_line("5/10/2026 12:00:01.000", "Player-1-T", "Tankman-Uldum-EU")
    )

    def fake_resolver(items: dict) -> dict[str, int]:
        # Mirrors item_db.resolve_equipped_stats: shield_armor for an off-hand.
        return {"shield_armor": 931, "armor_from_gear": 931} if "off_hand" in items else {}

    hyd = hydrate_character(log, "Tankman-Uldum-EU", resolve_stats_fn=fake_resolver)
    assert hyd is not None
    assert hyd.char_data["shield_armor"] == 931
    assert Character.from_dict(hyd.char_data).shield_armor == 931


def test_hydrate_no_shield_armor_for_non_shield_off_hand(tmp_path):
    """A non-shield off-hand (fist weapon → no armor) leaves shield_armor unset
    even with a resolver — the resolver returns no shield_armor for it."""
    log = tmp_path / "acl.log"
    log.write_text(
        _make_combatant_info_line(
            "5/10/2026 12:00:00.000", "Player-1-T", spec_id=73, gear=_SHIELD_OFF_HAND_GEAR
        )
        + _damage_line("5/10/2026 12:00:01.000", "Player-1-T", "Tankman-Uldum-EU")
    )
    # Resolver returns nothing armor-ish (fist weapon).
    hyd = hydrate_character(log, "Tankman-Uldum-EU", resolve_stats_fn=lambda items: {})
    assert hyd is not None
    assert "shield_armor" not in hyd.char_data
