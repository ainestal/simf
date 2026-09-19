"""Tests for `io/talent_decoder.py`.

`decode_combatant_info_entry_ids` — real ACL-on log ground truth, see
docs/validation/talent_string_decoder_2026_07_13.md. `decode_simc_talent_string`
and `decode_hero_tree_name` decode a pasted `talents=` string directly
(fixed 2026-08-31 — see `talent_string_codec.py`'s module docstring for
what was wrong before and how it's verified against real ground truth)
— tested against real in-game export strings, not hand-built fixtures.
"""

from __future__ import annotations

from simf.io.talent_decoder import (
    decode_combatant_info_entry_ids,
    decode_hero_tree_name,
    decode_simc_talent_string,
)
from simf.io.talent_tree_data import load_protection_warrior_tree

# Reinforced Plates' real entry_id, confirmed byte-for-byte against a real
# ACL-on log: `grep '(90368,112235,2)' examples/WoWCombatLog-050626_153703.txt`.
_REINFORCED_PLATES_ENTRY_ID = 112235


def test_decode_combatant_info_known_real_entry_id():
    result = decode_combatant_info_entry_ids(frozenset({_REINFORCED_PLATES_ENTRY_ID}))
    assert result.ok is True
    assert result.modeled == frozenset({"reinforced_plates"})
    assert len(result.all_talents) == 1
    assert result.all_talents[0].modeled_talent_id == "reinforced_plates"


def test_decode_combatant_info_empty_set_is_ok_not_a_failure():
    result = decode_combatant_info_entry_ids(frozenset())
    assert result.ok is True
    assert result.modeled == frozenset()
    assert result.all_talents == ()


def test_decode_combatant_info_unrecognized_entry_id_is_dropped_not_erroring():
    result = decode_combatant_info_entry_ids(frozenset({999999999}))
    assert result.ok is True
    assert result.modeled == frozenset()
    assert result.all_talents == ()


def test_decode_combatant_info_mixes_modeled_and_unmodeled_entries():
    """A real build has ~100+ real talents but simf only models 13 — the
    decoder must surface ALL recognized entries (for "Your talents,
    explained") while still reporting only the modeled subset for the sim."""
    tree = load_protection_warrior_tree()
    unmodeled_entry_id = next(
        eid for eid, e in tree.entry_catalog.items() if e.modeled_talent_id is None
    )
    result = decode_combatant_info_entry_ids(
        frozenset({_REINFORCED_PLATES_ENTRY_ID, unmodeled_entry_id})
    )
    assert result.modeled == frozenset({"reinforced_plates"})
    assert len(result.all_talents) == 2
    modeled_flags = {e.modeled_talent_id is not None for e in result.all_talents}
    assert modeled_flags == {True, False}


def test_decode_combatant_info_entries_sorted_by_entry_id():
    tree = load_protection_warrior_tree()
    some_ids = sorted(tree.entry_catalog)[:5]
    result = decode_combatant_info_entry_ids(frozenset(some_ids))
    # all_talents should be deterministically ordered regardless of the
    # input frozenset's arbitrary iteration order.
    result2 = decode_combatant_info_entry_ids(frozenset(reversed(some_ids)))
    assert [e.spell_id for e in result.all_talents] == [e.spell_id for e in result2.all_talents]


# ── decode_simc_talent_string: honest ok=False, not a fabricated build ────


def test_decode_simc_string_garbage_input_fails_closed():
    result = decode_simc_talent_string("not a real talent string")
    assert result.ok is False
    assert result.modeled == frozenset()
    assert result.warnings


def test_decode_simc_string_placeholder_value_fails_closed():
    """Existing tests/fixtures use plain placeholder strings like
    "ACTIVE_LOADOUT" for `sim.talents` — the decoder must fail closed on
    these (not crash, not fabricate a build), so simc_import.py's fallback
    logic can rely on `ok=False` unconditionally for non-real input."""
    result = decode_simc_talent_string("ACTIVE_LOADOUT")
    assert result.ok is False


def test_decode_simc_string_real_string_matches_combatant_info_ground_truth():
    """Brutoh's real, well-formed export string now decodes cleanly — see
    talent_string_codec.py's 2026-08-31 fix. His real, same-day
    COMBATANT_INFO (examples/WoWCombatLog-050626_153703.txt) reports these
    10 modeled talents; the decode must reproduce them exactly."""
    real_string = (
        "CkEAjLzRlq54bI5v+r8Sr9Xw43CAAYMjZmZmZGziZmlZMGjGzYYxMzMjZYmBAAAA"
        "LzMAzYAGstNjZZZZ0MzwMsMLNmZDzMzMDjtBAzMzMAgZAPA"
    )
    result = decode_simc_talent_string(real_string)
    assert result.ok is True
    assert result.modeled == frozenset(
        {
            "anger_management",
            "armor_specialization",
            "battle_scarred_veteran",
            "brace_for_impact",
            "brutal_vitality",
            "enduring_defenses",
            "indomitable",
            "reinforced_plates",
            "thunderlord",
            "unyielding_stance",
        }
    )


def test_decode_hero_tree_name_anonguardian1_is_elunes_chosen():
    """AnonGuardian1's real Guardian Druid talents= string (examples/AnonGuardian1 Guardian
    2026 08 28 Vault.md) decodes to Elune's Chosen — the signal
    character.py:_guardian_ironfur_avg_stacks needs to credit their haste."""
    anonguardian1_string = (
        "CgGADBD3hSPCL9Y9gz68WcKvMAAAAAAAAAAAAgZmZmFzMjZWmZxMPwMLLDMbGGNR"
        "mZWGzMzsMm5BAAAAAAYsZGYZbmBjZZAMFAAAYzYmBYxYYgZxCAzMAA"
    )
    assert decode_hero_tree_name(anonguardian1_string, 104) == "Elune's Chosen"


def test_decode_hero_tree_name_wrong_spec_returns_none():
    anonguardian1_string = (
        "CgGADBD3hSPCL9Y9gz68WcKvMAAAAAAAAAAAAgZmZmFzMjZWmZxMPwMLLDMbGGNR"
        "mZWGzMzsMm5BAAAAAAYsZGYZbmBjZZAMFAAAYzYmBYxYYgZxCAzMAA"
    )
    assert decode_hero_tree_name(anonguardian1_string, 73) is None


def test_decode_hero_tree_name_garbage_returns_none():
    assert decode_hero_tree_name("not a real talent string", 104) is None


def test_decode_simc_string_wrong_spec_is_flagged():
    """A string encoding a different spec_id (not 73/Protection Warrior)
    should fail with a spec-mismatch reason, not silently proceed."""
    # Hand-build a header-only string for spec_id=71 (Arms Warrior) using
    # the same encoder logic as test_talent_string_codec.py.
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    bits: list[int] = []
    for value, width in [(1, 8), (71, 16), (0, 128)]:
        for i in range(width):
            bits.append((value >> i) & 1)
    while len(bits) % 6 != 0:
        bits.append(0)
    chars = []
    for i in range(0, len(bits), 6):
        chunk = bits[i : i + 6]
        v = sum(b << j for j, b in enumerate(chunk))
        chars.append(alphabet[v])
    arms_string = "".join(chars)

    result = decode_simc_talent_string(arms_string)
    assert result.ok is False
    assert any("spec_id 71" in w for w in result.warnings)
