"""Tests for ``scripts/per_hit_mitigation_forensics.py`` on a synthetic ACL log.

Pins the load-bearing parsing semantics the 2026-07-04 Brewmaster
physical-gap decomposition rests on (docs/validation/
phase4_brewmaster_physical_gap_decomposition_2026_07_04.md):

  1. Live tank armor is read from the advanced-info block of damage lines
     whose info unit is the DESTINATION — ``SWING_DAMAGE_LANDED`` for melee
     (field 14) and ``SPELL_DAMAGE`` for spells (field 17) — and the plain
     ``SWING_DAMAGE`` twin (info unit = attacker) is ignored, so swings are
     neither double-counted nor read with the mob's armor.
  2. ``STAGGER_CLEAR`` lines carry only the player GUID, never the name — the
     scan must not drop them (the original audit's name-only prefilter did,
     silently zeroing purify totals).
  3. The per-hit residual math: r = (amount+absorbed+blocked)/base divided by
     the armor-curve×vers expectation at the hit's LIVE armor.

The script lives outside ``src/`` so we import it via a path-based loader,
matching tests/test_calibrate_spec_from_wcl.py.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "per_hit_mitigation_forensics.py"

TANK_GUID = "Player-1-T"
TANK = "Tank-Test-EU"
MOB_GUID = "Creature-0-1-1-1-99-X"

# Tank info block: armor 3430 -> armor_dr at K=3430 is exactly 0.5.
_TANK_INFO = (
    f"{TANK_GUID},0000000000000000,500000,600000,100,100,3430,0,0,0,3,100,100,0,1.0,1.0,1,1.0,90"
)
_MOB_INFO = (
    f"{MOB_GUID},0000000000000000,900000,900000,100,100,1470,0,0,0,1,0,100,0,1.0,1.0,1,1.0,90"
)


def _load():
    spec = importlib.util.spec_from_file_location("per_hit_mitigation_forensics", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["per_hit_mitigation_forensics"] = mod
    spec.loader.exec_module(mod)
    return mod


def _fixture_log(tmp_path: Path) -> Path:
    src = f'{MOB_GUID},"Mob",0xa48,0x80000000'
    dst = f'{TANK_GUID},"{TANK}",0x511,0x80000000'
    self_ = f'{TANK_GUID},"{TANK}",0x511,0x80000000'
    lines = [
        # key start (map_id 9999, key 10)
        '5/3/2026 23:00:00.0000  CHALLENGE_MODE_START,"Testland",9999,0,10,[9,10]',
        # melee swing: base 10000, amount 3000, absorbed 1500 (blocked 0)
        # SWING_DAMAGE twin has the MOB as info unit -> must be ignored.
        f"5/3/2026 23:00:01.0000  SWING_DAMAGE,{src},{dst},{_MOB_INFO},3000,10000,-1,1,0,0,1500,nil,nil,nil",
        f"5/3/2026 23:00:01.0010  SWING_DAMAGE_LANDED,{src},{dst},{_TANK_INFO},3000,10000,-1,1,0,0,1500,nil,nil,nil",
        # the swing's stagger absorb companion (absorber spell 115069), 1500
        f'5/3/2026 23:00:01.0020  SPELL_ABSORBED,{src},{dst},{TANK_GUID},"{TANK}",0x511,0x80000000,115069,"Stagger",0x1,1500,3000,nil',
        # shadow spell hit: base 1000, amount 800, no absorb; trailing ST marker
        f'5/3/2026 23:00:02.0000  SPELL_DAMAGE,{src},{dst},7777,"Void Bolt",0x20,{_TANK_INFO},800,1000,-1,32,0,0,0,nil,nil,nil,ST',
        # stagger self-tick 500 (live armor rides along at field 17)
        f'5/3/2026 23:00:03.0000  SPELL_PERIODIC_DAMAGE,{self_},{self_},124255,"Stagger",0x1,{_TANK_INFO},500,500,-1,1,0,0,0,nil,nil,nil,ST',
        # purify: GUID-only line (regression: no player NAME anywhere on it)
        f"5/3/2026 23:00:04.0000  STAGGER_CLEAR,{TANK_GUID},250.0",
        "5/3/2026 23:01:00.0000  CHALLENGE_MODE_END,9999,1,10,1000,60.0,1500.0",
    ]
    p = tmp_path / "synthetic.txt"
    p.write_text("\n".join(lines) + "\n")
    return p


def test_forensics_on_synthetic_log(tmp_path, monkeypatch, capsys):
    log = _fixture_log(tmp_path)
    mod = _load()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "per_hit_mitigation_forensics.py",
            str(log),
            "--tank",
            TANK,
            "--vers",
            "0.0",
            "--k-override",
            "3430",
        ],
    )
    mod.main()
    out = capsys.readouterr().out

    # 1a. live armor sourced from the tank info block (stagger tick timeline)
    assert "live armor (from stagger ticks): 3430×1" in out

    # 2. STAGGER_CLEAR counted despite carrying no player name:
    #    in=1500, ticks=500, clears=250 -> out/in = 0.5
    assert "clears(n=1)=250" in out
    assert "out/in=0.500" in out

    # 3. residual math at live armor: physical swing r=(3000+1500)/10000=0.45,
    #    expectation (1-0.5)*(1-0) = 0.5 -> resid 0.90; the SWING_DAMAGE twin
    #    (mob info block, armor 1470) must NOT create a second physical hit.
    assert "residual — physical:" in out
    phys = out.split("residual — physical:")[1].split("residual —")[0]
    assert "ALL: n=1" in phys
    assert "wmean=0.9000" in phys

    # shadow spell: r=0.8 vs vers-only expectation 1.0 -> resid 0.80
    shadow = out.split("residual — shadow:")[1]
    assert "ALL: n=1" in shadow
    assert "wmean=0.8000" in shadow


def test_swing_twin_with_mob_info_is_skipped(tmp_path, monkeypatch, capsys):
    """A _LANDED line whose info unit is unexpectedly NOT the tank is dropped
    (armor unreadable) instead of being computed with the mob's armor."""
    src = f'{MOB_GUID},"Mob",0xa48,0x80000000'
    dst = f'{TANK_GUID},"{TANK}",0x511,0x80000000'
    lines = [
        '5/3/2026 23:00:00.0000  CHALLENGE_MODE_START,"Testland",9999,0,10,[9,10]',
        # _LANDED with MOB info block — malformed for our purposes
        f"5/3/2026 23:00:01.0000  SWING_DAMAGE_LANDED,{src},{dst},{_MOB_INFO},3000,10000,-1,1,0,0,1500,nil,nil,nil",
        "5/3/2026 23:01:00.0000  CHALLENGE_MODE_END,9999,1,10,1000,60.0,1500.0",
    ]
    log = tmp_path / "synthetic2.txt"
    log.write_text("\n".join(lines) + "\n")
    mod = _load()
    monkeypatch.setattr(
        sys,
        "argv",
        ["per_hit_mitigation_forensics.py", str(log), "--tank", TANK, "--k-override", "3430"],
    )
    mod.main()
    out = capsys.readouterr().out
    # no usable hits -> no physical residual section at all
    assert "residual — physical:" not in out
