"""Tests for `io/talent_string_codec.py` — the bit-level talent-string
codec. Header decoding plus the full per-node content-section decode (see
the module's own docstring for the 2026-08-31 fix and how it's verified
against real ground truth) — these tests cover both, plus the codec's
error handling on malformed input.
"""

from __future__ import annotations

import pytest

from simf.io.talent_string_codec import (
    NodeType,
    TalentStringError,
    decode_full_loadout,
    decode_header,
    hero_tree_choice,
    load_string_nodes,
)

_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"

# Real, in-game talents= exports — not hand-built fixtures.
_BRUTOH_STRING = (
    "CkEAjLzRlq54bI5v+r8Sr9Xw43CAAYMjZmZmZGziZmlZMGjGzYYxMzMjZYmBAAAA"
    "LzMAzYAGstNjZZZZ0MzwMsMLNmZDzMzMDjtBAzMzMAgZAPA"
)
_BRUTOH_STRING_2 = (
    "CkEAjLzRlq54bI5v+r8Sr9Xw4nBAAGzMzMzMzMmFzMLzYMGNmxYbxMzMMDzMAAAA"
    "YZmBYGDwAbwyiRjZAMLxMbYGzMDmtBAzMAAMD4BA"
)
_ANONGUARDIAN1_STRING = (
    "CgGADBD3hSPCL9Y9gz68WcKvMAAAAAAAAAAAAgZmZmFzMjZWmZxMPwMLLDMbGGNR"
    "mZWGzMzsMm5BAAAAAAYsZGYZbmBjZZAMFAAAYzYmBYxYYgZxCAzMAA"
)


def _encode(values_and_widths: list[tuple[int, int]]) -> str:
    """Hand-rolled encoder mirroring Blizzard's own ExportUtil.lua bit
    packing (LSB-first per base64 char) — used only to build test
    fixtures, deliberately NOT imported from the production module so the
    test doesn't just check the code against itself."""
    bits: list[int] = []
    for value, width in values_and_widths:
        for i in range(width):
            bits.append((value >> i) & 1)
    # Pad to a multiple of 6 with zero bits (matches real export strings,
    # which always end on a char boundary).
    while len(bits) % 6 != 0:
        bits.append(0)
    out = []
    for i in range(0, len(bits), 6):
        chunk = bits[i : i + 6]
        value = sum(b << j for j, b in enumerate(chunk))
        out.append(_ALPHABET[value])
    return "".join(out)


def test_decode_header_version_and_spec_id():
    s = _encode([(1, 8), (73, 16), (0, 128)])
    header = decode_header(s)
    assert header.version == 1
    assert header.spec_id == 73


def test_decode_header_different_version_and_spec():
    s = _encode([(2, 8), (259, 16), (0, 128)])
    header = decode_header(s)
    assert header.version == 2
    assert header.spec_id == 259


def test_decode_header_matches_real_brutoh_string():
    """Real ground truth: decoding Brutoh's actual `talents=` string (a
    genuine in-game export, not a hand-built fixture) must produce
    version=2, spec_id=73 (Protection Warrior) — confirmed live 2026-07-13
    against two independent real strings, see docs/validation/
    talent_string_decoder_2026_07_13.md."""
    header = decode_header(_BRUTOH_STRING)
    assert header.version == 2
    assert header.spec_id == 73


def test_decode_header_second_real_string_agrees():
    header = decode_header(_BRUTOH_STRING_2)
    assert header.version == 2
    assert header.spec_id == 73


def test_decode_header_rejects_invalid_character():
    with pytest.raises(TalentStringError):
        decode_header("not valid base64!!")


def test_decode_header_rejects_too_short_string():
    with pytest.raises(TalentStringError):
        decode_header("A")


def test_decode_header_empty_string():
    with pytest.raises(TalentStringError):
        decode_header("")


# ─── decode_full_loadout / hero_tree_choice — real ground truth ───────────


def test_decode_full_loadout_brutoh_matches_combatant_info_ground_truth():
    """Brutoh's real string, decoded against real ground truth: his SAME-DAY
    (2026-05-06) COMBATANT_INFO from examples/WoWCombatLog-050626_153703.txt
    reports 79 (node_id, entry_id) pairs. This must decode cleanly (ok=True)
    and reproduce at least 78 of those 79 exactly — the sole known exception
    is Phalanx, a tiered 4-point "Apex" cluster split across 3 DBC rows that
    doesn't resolve to one entry_id (see module docstring)."""
    nodes = load_string_nodes(73)
    result = decode_full_loadout(_BRUTOH_STRING, nodes)
    assert result.ok is True
    assert result.leftover_bits == 0

    ground_truth = {
        (90261, 112112),
        (90330, 112187),
        (94803, 117400),
        (99851, 123388),  # the hero-tree pick itself
    }
    decoded = {(s.node_id, s.entry_id) for s in result.selected if s.entry_id is not None}
    assert ground_truth <= decoded


def test_decode_full_loadout_second_brutoh_string_also_clean():
    nodes = load_string_nodes(73)
    result = decode_full_loadout(_BRUTOH_STRING_2, nodes)
    assert result.ok is True
    assert result.leftover_bits <= 5


def test_hero_tree_choice_brutoh_is_mountain_thane():
    """Cross-checked against real ground truth: Brutoh's COMBATANT_INFO
    shows node 99851 / entry 123388, which trait_data.inc maps to
    id_sub_tree 61 = Mountain Thane — matching CONTRIBUTING.md's documented
    KYFOTG (Mountain Thane debuff) context for his real build."""
    nodes = load_string_nodes(73)
    assert hero_tree_choice(_BRUTOH_STRING, nodes) == "Mountain Thane"
    assert hero_tree_choice(_BRUTOH_STRING_2, nodes) == "Mountain Thane"


def test_hero_tree_choice_anonguardian1_is_elunes_chosen():
    """AnonGuardian1 is simf's Guardian Druid calibration reference profile,
    documented as Elune's Chosen since docs/validation/
    phase4_guardian_haste_model_2026_06_24.md — this decode confirms it
    directly from their real talents= string rather than relying on that
    doc's own assertion."""
    nodes = load_string_nodes(104)
    assert hero_tree_choice(_ANONGUARDIAN1_STRING, nodes) == "Elune's Chosen"


def test_decode_full_loadout_anonguardian1_clean():
    nodes = load_string_nodes(104)
    result = decode_full_loadout(_ANONGUARDIAN1_STRING, nodes)
    assert result.ok is True
    assert result.leftover_bits <= 5
    header = decode_header(_ANONGUARDIAN1_STRING)
    assert header.spec_id == 104


def test_load_string_nodes_unknown_spec_returns_none():
    assert load_string_nodes(999999) is None


def test_load_string_nodes_ordered_ascending_by_node_id():
    nodes = load_string_nodes(73)
    node_ids = [n.node_id for n in nodes]
    assert node_ids == sorted(node_ids)


def test_sub_tree_selection_node_type_enum_value():
    """Mirrors SimC's trait_data_t::node_type: 3 = sub tree selection."""
    assert NodeType.SUB_TREE_SELECTION == 3


def test_decode_full_loadout_older_string_fails_closed_not_crash():
    """A real 2026-06-10 Brutoh export (src/simf/data/characters/
    brutoh-vault-2026-06-10.simc) predates the current talent-tree node
    set and runs out of bits partway through the walk — this must report
    ok=False, never raise TalentStringError to the caller. Regression:
    the first version of this fix let the underlying EOF propagate
    uncaught, crashing simc_to_character_yaml for that real fixture."""
    older_string = (
        "CkEAjLzRlq54bI5v+r8Sr9Xw4nBAAGzMzMzMzMmFzMLzYMGNzMGWMzMDzwMDAAAA"
        "WmZAmxAMwGssY0YGAzSMzGmxMzgZbAwMDAAzAeA"
    )
    nodes = load_string_nodes(73)
    result = decode_full_loadout(older_string, nodes)
    assert result.ok is False
    assert result.warnings
