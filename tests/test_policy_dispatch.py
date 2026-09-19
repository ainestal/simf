"""Make-policy dispatch table integration test — Phase 4 follow-up.

The validator Phase-4 audit (2026-05-16) caught that `make_policy`
silently fell through to the Warrior policy for every non-Paladin spec,
meaning Death Strike / Soul Cleave / Purifying Brew / Tooth-and-Claw /
Vampiric Blood / Metamorphosis / Incarnation were all dead code at
runtime. Unit tests instantiated each policy class directly and never
checked the dispatch.

This file is the dispatch regression test: every Midnight tank spec
must return its own policy class, not the Warrior fallback.
"""

from __future__ import annotations

import pytest

from simf.classes.blood_death_knight import BloodDKPolicy
from simf.classes.brewmaster_monk import BrewmasterPolicy
from simf.classes.guardian_druid import GuardianPolicy
from simf.classes.protection_paladin import ProtPalPolicy
from simf.classes.vengeance_dh import VengeanceDHPolicy
from simf.core.character import Character
from simf.core.policy import ActiveMitigationPolicy, make_policy


def _char(spec: str) -> Character:
    return Character(
        name="dispatch",
        race="orc",
        class_spec=spec,
        talents="brutoh-actual",
        strength=2000,
        stamina=50000,
        armor_from_gear=4000,
    )


@pytest.mark.parametrize(
    "spec,expected_cls",
    [
        ("protection_warrior", ActiveMitigationPolicy),
        ("protection_paladin", ProtPalPolicy),
        ("blood_death_knight", BloodDKPolicy),
        ("vengeance_demon_hunter", VengeanceDHPolicy),
        ("brewmaster_monk", BrewmasterPolicy),
        ("guardian_druid", GuardianPolicy),
    ],
)
def test_make_policy_dispatches_to_per_spec_policy(spec: str, expected_cls):
    """Every Midnight tank spec gets its own policy class.

    Without this dispatch, the spec's `decide()` heals / emergency CDs
    silently never fire because the runner calls the Warrior policy.
    """
    char = _char(spec)
    policy = make_policy(char)
    assert isinstance(policy, expected_cls), (
        f"make_policy({spec}) returned {type(policy).__name__}, expected {expected_cls.__name__}"
    )
