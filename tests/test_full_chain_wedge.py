"""Tests for scripts/full_chain_wedge.py — the corpus-wide full-modeled-chain
per-hit wedge decomposition (docs/validation/protwarrior_full_chain_wedge_2026_07_21.md).

Field layouts modeled on real ACL-on damage lines (same combat-log format);
expected values are computed in-test from the same formula the module
documents, not hand-transcribed, so a failure means the function diverged
from its own contract, not a decimal typo. Fixture GUID is a synthetic
placeholder, not a captured value.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from simf.io.combat_log_core import parse_combat_log_line

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "full_chain_wedge.py"


def _load():
    spec = importlib.util.spec_from_file_location("full_chain_wedge", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["full_chain_wedge"] = mod
    spec.loader.exec_module(mod)
    return mod


_mod = _load()
compute_run_wedge = _mod.compute_run_wedge
_bfi_stack_timeline = _mod._bfi_stack_timeline
_stack_at = _mod._stack_at

TANK = "Brutoh-Uldum-EU"
TANK_GUID = "Player-1379-AAAA0001"

# A single physical melee hit: amount=8080 base=181007 blocked=45788
# absorbed=0 armor=5517 school=physical. Same layout as
# test_measure_run_f.py's _PHYSICAL_HIT_LINE.
_PHYS_HIT = (
    "5/6/2026 17:29:20.0000  SWING_DAMAGE_LANDED,Creature-0-1465-2805-15389-232071-0003FB52AF,"
    f'"Dutiful Groundskeeper",0xa48,0x80000000,{TANK_GUID},"Brutoh-Uldum-EU",0x511,'
    f"0x80000000,{TANK_GUID},0000000000000000,781368,789448,3276,422,{5517},596,100,0,1,"
    f"250,1000,0,5226.69,-3029.34,2492,1.1735,280,{8080},{181007},-1,1,0,{45788},0,nil,nil,nil\n"
)

# A magic (arcane) hit, non-physical: amount=28827 base=41620 armor=4678.
_MAGIC_HIT = (
    "5/6/2026 17:29:21.0000  SPELL_DAMAGE,Creature-0-1465-2805-15389-232113-00007B52AF,"
    f'"Spellguard Magus",0xa48,0x80000000,{TANK_GUID},"Brutoh-Uldum-EU",0x511,'
    f'0x80000000,1216253,"Arcane Salvo",0x40,{TANK_GUID},0000000000000000,739647,789448,'
    f"3353,422,{4678},596,100,0,1,350,1000,0,5200.06,-3024.42,2492,5.7235,280,{28827},{41620},"
    "-1,64,0,0,0,nil,nil,nil,ST\n"
)


def _self_aura_line(
    t: str, event_type: str, spell_id: int, spell_name: str, stack: str = ""
) -> str:
    suffix = f",{stack}" if stack else ""
    return (
        f'{t}  {event_type},{TANK_GUID},"{TANK}",0x511,0x80000000,{TANK_GUID},"{TANK}",0x511,'
        f'0x80000000,{spell_id},"{spell_name}",0x1,BUFF{suffix}\n'
    )


def _write_log(tmp_path, lines: list[str]) -> Path:
    p = tmp_path / "log.txt"
    p.write_text("".join(lines))
    return p


_HIT_TIME_S = parse_combat_log_line(_PHYS_HIT)[0]
_BEFORE_S = _HIT_TIME_S - 5.0


def _brutoh(brutoh, talents: frozenset[str]):
    return replace(brutoh, talent_set_override=talents)


# --------------------------------------------------------------------------
# _bfi_stack_timeline / _stack_at
# --------------------------------------------------------------------------


def test_bfi_stack_timeline_tracks_dose_events(tmp_path):
    lines = [
        _self_aura_line("5/6/2026 17:29:10.000", "SPELL_AURA_APPLIED", 386029, "Brace For Impact"),
        _self_aura_line(
            "5/6/2026 17:29:12.000", "SPELL_AURA_APPLIED_DOSE", 386029, "Brace For Impact", "2"
        ),
        _self_aura_line(
            "5/6/2026 17:29:13.000", "SPELL_AURA_APPLIED_DOSE", 386029, "Brace For Impact", "3"
        ),
        _self_aura_line(
            "5/6/2026 17:29:14.000", "SPELL_AURA_REMOVED_DOSE", 386029, "Brace For Impact", "2"
        ),
        _self_aura_line("5/6/2026 17:29:20.000", "SPELL_AURA_REMOVED", 386029, "Brace For Impact"),
    ]
    log = _write_log(tmp_path, lines)
    t0 = parse_combat_log_line(lines[0])[0]
    timeline = _bfi_stack_timeline(log, TANK_GUID, start_time_s=t0 - 1, end_time_s=None)
    assert [stack for _, stack in timeline] == [1, 2, 3, 2, 0]
    # Before the first APPLIED: no stacks yet.
    assert _stack_at(timeline, t0 - 0.5) == 0
    assert _stack_at(timeline, t0) == 1
    assert _stack_at(timeline, t0 + 2.5) == 2  # between APPLIED_DOSE(2) and APPLIED_DOSE(3)
    assert _stack_at(timeline, t0 + 3.5) == 3
    assert _stack_at(timeline, t0 + 10) == 0  # after REMOVED


def test_bfi_refresh_does_not_change_stack(tmp_path):
    """SPELL_AURA_REFRESH resets duration only — must not appear in the
    timeline as a stack change (module docstring)."""
    lines = [
        _self_aura_line("5/6/2026 17:29:10.000", "SPELL_AURA_APPLIED", 386029, "Brace For Impact"),
        _self_aura_line(
            "5/6/2026 17:29:11.000", "SPELL_AURA_APPLIED_DOSE", 386029, "Brace For Impact", "2"
        ),
        _self_aura_line("5/6/2026 17:29:12.000", "SPELL_AURA_REFRESH", 386029, "Brace For Impact"),
    ]
    log = _write_log(tmp_path, lines)
    t0 = parse_combat_log_line(lines[0])[0]
    timeline = _bfi_stack_timeline(log, TANK_GUID, start_time_s=t0 - 1, end_time_s=None)
    assert [stack for _, stack in timeline] == [1, 2]


# --------------------------------------------------------------------------
# compute_run_wedge — per-hit chain composition
# --------------------------------------------------------------------------


def test_no_talents_no_windows_matches_hand_formula(tmp_path, brutoh):
    """No Indomitable/BfI/BSV, no SB/SW windows: chain = armor(capped) * vers
    * (1 - defensive_stance_dr) only — Defensive Stance is NOT talent-gated,
    it's a baseline spec passive, so it still applies even with an empty
    talent set."""
    log = _write_log(tmp_path, [_PHYS_HIT])
    char = _brutoh(brutoh, frozenset())
    wedge = compute_run_wedge(
        log,
        tank_name=TANK,
        start_time_s=_BEFORE_S,
        end_time_s=None,
        start_byte_offset=0,
        run_char=char,
        k=3430.0,
        label="test",
    )
    assert wedge is not None
    assert wedge.n_hits == 1
    r = (8080 + 0 + 45788) / 181007
    armor_dr = 5517 / (5517 + 3430.0)
    vers = char.versatility_dr()
    always_on = 1 - 0.15  # defensive_stance_dr, from constants.yaml
    expected = r / ((1 - armor_dr) * (1 - vers) * always_on)
    assert wedge.clean_wmean is not None
    assert abs(wedge.clean_wmean - expected) < 1e-9
    assert abs(wedge.full_wmean - expected) < 1e-9
    assert abs(wedge.full_no_sb_wmean - expected) < 1e-9
    assert wedge.n_armor_dr_capped == 0


def test_indomitable_divides_out_extra_four_percent(tmp_path, brutoh):
    log = _write_log(tmp_path, [_PHYS_HIT])
    char = _brutoh(brutoh, frozenset({"indomitable"}))
    wedge = compute_run_wedge(
        log,
        tank_name=TANK,
        start_time_s=_BEFORE_S,
        end_time_s=None,
        start_byte_offset=0,
        run_char=char,
        k=3430.0,
        label="test",
    )
    assert wedge is not None
    r = (8080 + 0 + 45788) / 181007
    armor_dr = 5517 / (5517 + 3430.0)
    vers = char.versatility_dr()
    always_on = (1 - 0.15) * (1 - 0.04)  # DS x Indomitable
    expected = r / ((1 - armor_dr) * (1 - vers) * always_on)
    assert abs(wedge.full_wmean - expected) < 1e-9


def test_bfi_real_stacks_divided_into_physical_only(tmp_path, brutoh):
    """A physical hit at 2 real BfI stacks: divided by (1 - 2*0.01). A magic
    hit at the same time is untouched (BfI is physical-only)."""
    bfi_lines = [
        _self_aura_line("5/6/2026 17:29:10.000", "SPELL_AURA_APPLIED", 386029, "Brace For Impact"),
        _self_aura_line(
            "5/6/2026 17:29:11.000", "SPELL_AURA_APPLIED_DOSE", 386029, "Brace For Impact", "2"
        ),
    ]
    log = _write_log(tmp_path, [*bfi_lines, _PHYS_HIT, _MAGIC_HIT])
    char = _brutoh(brutoh, frozenset({"brace_for_impact"}))
    t0 = parse_combat_log_line(bfi_lines[0])[0]
    wedge = compute_run_wedge(
        log,
        tank_name=TANK,
        start_time_s=t0 - 1,
        end_time_s=None,
        start_byte_offset=0,
        run_char=char,
        k=3430.0,
        label="test",
    )
    assert wedge is not None
    assert wedge.n_hits == 2
    # Physical hit's clean_resid should reflect the 2-stack BfI factor.
    r_phys = (8080 + 0 + 45788) / 181007
    armor_dr = 5517 / (5517 + 3430.0)
    vers = char.versatility_dr()
    always_on = 1 - 0.15
    bfi_factor = 1 - 2 * 0.01
    expected_phys = r_phys / ((1 - armor_dr) * (1 - vers) * always_on * bfi_factor)
    # Magic hit: no armor, no BfI.
    r_magic = 28827 / 41620
    expected_magic = r_magic / ((1 - vers) * always_on)
    # weighted mean over both hits (weights = base_amount)
    w_phys, w_magic = 181007, 41620
    expected_wmean = (w_phys * expected_phys + w_magic * expected_magic) / (w_phys + w_magic)
    assert wedge.clean_wmean is not None
    assert abs(wedge.clean_wmean - expected_wmean) < 1e-6


def test_shield_wall_window_applies_to_all_schools_and_excludes_clean(tmp_path, brutoh):
    sw_lines = [
        _self_aura_line("5/6/2026 17:29:10.000", "SPELL_AURA_APPLIED", 871, "Shield Wall"),
        _self_aura_line("5/6/2026 17:29:30.000", "SPELL_AURA_REMOVED", 871, "Shield Wall"),
    ]
    log = _write_log(tmp_path, [*sw_lines, _MAGIC_HIT])  # hit at 17:29:21, inside the window
    char = _brutoh(brutoh, frozenset())
    t0 = parse_combat_log_line(sw_lines[0])[0]
    wedge = compute_run_wedge(
        log,
        tank_name=TANK,
        start_time_s=t0 - 1,
        end_time_s=None,
        start_byte_offset=0,
        run_char=char,
        k=3430.0,
        label="test",
    )
    assert wedge is not None
    assert wedge.n_hits == 1
    # Inside a Shield Wall window -> excluded from "clean".
    assert wedge.clean_wmean is None
    assert wedge.clean_n == 0
    r = 28827 / 41620
    vers = char.versatility_dr()
    always_on = 1 - 0.15
    sw_dr = 0.40
    expected = r / ((1 - vers) * always_on * (1 - sw_dr))
    assert abs(wedge.full_wmean - expected) < 1e-9
    # Shield Wall isn't the SB layer — full and full_no_sb agree here.
    assert abs(wedge.full_no_sb_wmean - expected) < 1e-9


def test_shield_block_window_full_equals_full_no_sb_post_fix(tmp_path, brutoh):
    """RE-PURPOSED 2026-07-21 (was `..._diverges_...`): the wedge tool reads
    `active_mitigation.shield_block.physical_dr` live from constants.yaml,
    which the shield-block-fix session zeroed (docs/validation/
    protwarrior_shield_block_fix_2026_07_21.md — the layer was a spurious
    double-count, not a real Midnight 12.0.5 mechanic). `full_wmean` (as
    modeled) and `full_no_sb_wmean` must now be IDENTICAL for a physical hit
    inside an SB window — this is the wedge tool's own regression test for
    the fix (its own inputs are untouched; only the constant it reads
    changed), matching the task's expectation that `full` should "no longer
    be >1.0" once the double-count is gone."""
    sb_lines = [
        _self_aura_line("5/6/2026 17:29:10.000", "SPELL_AURA_APPLIED", 132404, "Shield Block"),
        _self_aura_line("5/6/2026 17:29:30.000", "SPELL_AURA_REMOVED", 132404, "Shield Block"),
    ]
    log = _write_log(tmp_path, [*sb_lines, _PHYS_HIT])  # hit at 17:29:20, inside the window
    char = _brutoh(brutoh, frozenset())
    t0 = parse_combat_log_line(sb_lines[0])[0]
    wedge = compute_run_wedge(
        log,
        tank_name=TANK,
        start_time_s=t0 - 1,
        end_time_s=None,
        start_byte_offset=0,
        run_char=char,
        k=3430.0,
        label="test",
    )
    assert wedge is not None
    assert wedge.full_wmean == pytest.approx(wedge.full_no_sb_wmean, abs=1e-9)
    assert wedge.clean_wmean is None  # SB window excludes it from "clean" too


def test_battle_scarred_veteran_gated_on_talent_presence(tmp_path, brutoh):
    bsv_lines = [
        _self_aura_line(
            "5/6/2026 17:29:10.000", "SPELL_AURA_APPLIED", 386397, "Battle-Scarred Veteran"
        ),
        _self_aura_line(
            "5/6/2026 17:29:30.000", "SPELL_AURA_REMOVED", 386397, "Battle-Scarred Veteran"
        ),
    ]
    log = _write_log(tmp_path, [*bsv_lines, _MAGIC_HIT])
    t0 = parse_combat_log_line(bsv_lines[0])[0]

    # Without the talent, the BSV window has no effect at all.
    char_no_talent = _brutoh(brutoh, frozenset())
    wedge_no_talent = compute_run_wedge(
        log,
        tank_name=TANK,
        start_time_s=t0 - 1,
        end_time_s=None,
        start_byte_offset=0,
        run_char=char_no_talent,
        k=3430.0,
        label="test",
    )
    assert wedge_no_talent is not None
    r = 28827 / 41620
    vers = char_no_talent.versatility_dr()
    always_on = 1 - 0.15
    expected_no_talent = r / ((1 - vers) * always_on)
    assert abs(wedge_no_talent.full_wmean - expected_no_talent) < 1e-9
    # No talent -> BSV window is never fetched/applied -> not excluded from "clean" either.
    assert wedge_no_talent.clean_wmean is not None

    # With the talent, the same window divides by (1 - bsv_dr) and excludes from "clean".
    char_talent = _brutoh(brutoh, frozenset({"battle_scarred_veteran"}))
    wedge_talent = compute_run_wedge(
        log,
        tank_name=TANK,
        start_time_s=t0 - 1,
        end_time_s=None,
        start_byte_offset=0,
        run_char=char_talent,
        k=3430.0,
        label="test",
    )
    assert wedge_talent is not None
    assert wedge_talent.clean_wmean is None
    bsv_dr = 0.30
    expected_talent = r / ((1 - vers) * always_on * (1 - bsv_dr))
    assert abs(wedge_talent.full_wmean - expected_talent) < 1e-9


def test_armor_dr_cap_applied_and_counted(tmp_path, brutoh):
    """A hit with absurdly high live armor must clamp armor_dr at
    max_armor_dr (0.85) rather than the uncapped armor/(armor+K) ratio — the
    exact gap the task brief flagged in measure_run_f.py /
    per_hit_mitigation_forensics.py (both compute this uncapped)."""
    k = 3430.0
    huge_armor = 50000  # >> the ~19,437 threshold at K=3430, max_armor_dr=0.85
    hit = _PHYS_HIT.replace(",5517,", f",{huge_armor},")
    log = _write_log(tmp_path, [hit])
    char = _brutoh(brutoh, frozenset())
    wedge = compute_run_wedge(
        log,
        tank_name=TANK,
        start_time_s=_BEFORE_S,
        end_time_s=None,
        start_byte_offset=0,
        run_char=char,
        k=k,
        label="test",
    )
    assert wedge is not None
    assert wedge.n_armor_dr_capped == 1
    uncapped = huge_armor / (huge_armor + k)
    assert uncapped > 0.85  # sanity: this hit really would exceed the cap
    r = (8080 + 0 + 45788) / 181007
    vers = char.versatility_dr()
    always_on = 1 - 0.15
    expected = r / ((1 - 0.85) * (1 - vers) * always_on)  # capped divisor, not uncapped
    assert wedge.clean_wmean is not None
    assert abs(wedge.clean_wmean - expected) < 1e-9


def test_base_amount_shares_no_windows(tmp_path, brutoh):
    """2026-07-21 Lead 1 (docs/validation/
    protwarrior_post_shield_block_bias_decomposition_2026_07_21.md): with no
    SB/SW/BSV windows at all, both hits are "clean" (in_cd_window=False)
    regardless of school — clean_base_sum covers the physical AND the magic
    hit's base_amount. physical_nonbleed_base_sum covers only the physical
    hit; sb_active_base_sum is 0.0 since no SB window exists."""
    log = _write_log(tmp_path, [_PHYS_HIT, _MAGIC_HIT])
    char = _brutoh(brutoh, frozenset())
    wedge = compute_run_wedge(
        log,
        tank_name=TANK,
        start_time_s=_BEFORE_S,
        end_time_s=None,
        start_byte_offset=0,
        run_char=char,
        k=3430.0,
        label="test",
    )
    assert wedge is not None
    assert wedge.total_base_sum == pytest.approx(181007 + 41620)
    assert wedge.clean_base_sum == pytest.approx(181007 + 41620)
    assert wedge.physical_nonbleed_base_sum == pytest.approx(181007)
    assert wedge.physical_nonbleed_outside_sb_base_sum == pytest.approx(181007)
    assert wedge.sb_active_base_sum == 0.0
    assert wedge.physical_nonbleed_n == 1
    assert wedge.physical_nonbleed_outside_sb_n == 1


def test_base_amount_shares_sb_window_reclassifies_physical_only(tmp_path, brutoh):
    """Same two hits, but the physical hit now falls inside a real Shield
    Block window. The magic hit is untouched by SB (it's not physical) and
    stays in the "clean" bin (this is exactly why the ALL-SCHOOLS clean bin
    is NOT apples-to-apples with the SB-active-vs-inactive hit-count split
    quoted from the validation doc, which was physical-non-bleed-only —
    `physical_nonbleed_*` exists specifically to give the apples-to-apples
    comparison instead)."""
    sb_lines = [
        _self_aura_line("5/6/2026 17:29:10.000", "SPELL_AURA_APPLIED", 132404, "Shield Block"),
        _self_aura_line("5/6/2026 17:29:30.000", "SPELL_AURA_REMOVED", 132404, "Shield Block"),
    ]
    # _PHYS_HIT is at 17:29:20 (inside), _MAGIC_HIT at 17:29:21 (also inside,
    # but SB never gates magic damage).
    log = _write_log(tmp_path, [*sb_lines, _PHYS_HIT, _MAGIC_HIT])
    char = _brutoh(brutoh, frozenset())
    t0 = parse_combat_log_line(sb_lines[0])[0]
    wedge = compute_run_wedge(
        log,
        tank_name=TANK,
        start_time_s=t0 - 1,
        end_time_s=None,
        start_byte_offset=0,
        run_char=char,
        k=3430.0,
        label="test",
    )
    assert wedge is not None
    assert wedge.total_base_sum == pytest.approx(181007 + 41620)
    assert wedge.clean_base_sum == pytest.approx(41620)  # magic hit only
    assert wedge.sb_active_base_sum == pytest.approx(181007)
    assert wedge.physical_nonbleed_base_sum == pytest.approx(181007)
    assert wedge.physical_nonbleed_outside_sb_base_sum == 0.0
    assert wedge.physical_nonbleed_outside_sb_n == 0


def test_no_per_hit_armor_data_returns_none(tmp_path, brutoh):
    log = _write_log(tmp_path, ["some unrelated line\n"])
    char = _brutoh(brutoh, frozenset())
    wedge = compute_run_wedge(
        log,
        tank_name=TANK,
        start_time_s=0.0,
        end_time_s=None,
        start_byte_offset=0,
        run_char=char,
        k=3430.0,
        label="test",
    )
    assert wedge is None
