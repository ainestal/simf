"""Phase 4 — Brewmaster hero-talent mitigation-layer ledger (2026-06-07).

The characterization (docs/validation/phase4_brewmaster_magic_layers_2026_06_07.md)
proved the Brewmaster over-prediction is a HERO-TALENT-DEPENDENT flat DR the
engine omitted — not the absorb/Stagger path. This wires a talent-gated ledger:
a flat DR applied ONLY when the talent's BUFF aura is detected active.

Detection gates on the BUFF spell_id (Predictive Training = 451230), NOT
`detected_talent_spell_ids` — COMBATANT_INFO carries trait-node-ENTRY ids, not
spell ids, so the buff aura is the only reliable replay signal.

Invariants pinned here:
  * Warrior `_always_on_dr()` is bit-identical regardless of `active_buff_spell_ids`.
  * Brewmaster without the buff is the identity (every non-PT build unchanged).
  * The ledger applies PRE-absorb so the leverage on net damage exceeds the
    nominal DR% (that is how +27% -> +11.8% comes from an 8%×0.88 cut on AnonBrewmaster2;
    that fixed-absorb leverage also over-credits PT — see the magic-layers doc).
"""

from __future__ import annotations

import random
from datetime import datetime

import pytest

from simf.classes.brewmaster_monk import apply_brewmaster_mitigation
from simf.core.character import Character
from simf.core.constants import load_constants
from simf.core.events import DamageEvent
from simf.core.mitigation import MitigationState
from simf.io.combat_log import detect_active_buffs

PT_BUFF_ID = 451230


def _brewmaster(active_buff_spell_ids=None, versatility_rating=0) -> Character:
    return Character(
        name="BM",
        race="pandaren",
        class_spec="brewmaster_monk",
        talents="",
        strength=0,
        stamina=30_000,
        armor_from_gear=2_000,
        agility=5_000,
        versatility_rating=versatility_rating,
        active_buff_spell_ids=active_buff_spell_ids,
    )


def _warrior(active_buff_spell_ids=None) -> Character:
    return Character(
        name="W",
        race="human",
        class_spec="protection_warrior",
        talents="default",
        strength=2_000,
        stamina=35_000,
        armor_from_gear=5_000,
        active_buff_spell_ids=active_buff_spell_ids,
    )


# ── _always_on_dr() ledger behaviour ─────────────────────────────────────────


def test_pt_buff_present_applies_uptime_weighted_8pct_all_school():
    char = _brewmaster(active_buff_spell_ids=frozenset({PT_BUFF_ID}))
    # 8% all-school × 0.88 measured damage-weighted uptime → 7.04% effective.
    assert char._always_on_dr() == pytest.approx(1 - 0.08 * 0.88)


def test_no_active_buffs_is_identity():
    assert _brewmaster(active_buff_spell_ids=None)._always_on_dr() == 1.0
    assert _brewmaster(active_buff_spell_ids=frozenset())._always_on_dr() == 1.0


def test_irrelevant_buff_is_identity():
    char = _brewmaster(active_buff_spell_ids=frozenset({999_999}))
    assert char._always_on_dr() == 1.0


def test_warrior_always_on_dr_unaffected_by_active_buffs():
    """The testable invariant: warrior path is bit-identical with or without
    the ledger field set — the ledger is brewmaster-only."""
    base = _warrior(active_buff_spell_ids=None)._always_on_dr()
    with_pt = _warrior(active_buff_spell_ids=frozenset({PT_BUFF_ID}))._always_on_dr()
    assert with_pt == base
    # And it's the real Defensive-Stance value, not an accidental 1.0.
    ds = load_constants()["specs"]["protection_warrior"]["defensive_stance_dr"]
    assert abs(base - (1 - ds)) < 1e-9


# ── replay path: pre-absorb leverage ─────────────────────────────────────────


def _replay_event(raw, log_absorbed):
    # Magic school → no armor DR, no avoidance/block in the brewmaster chain →
    # fully deterministic (rng unused).
    return DamageEvent(
        time_s=10.0,
        source_id="boss",
        school="shadow",
        raw_amount=raw,
        attack_type="spell",
        is_log_replay=True,
        log_absorbed=log_absorbed,
    )


def _dealt(char, raw, log_absorbed):
    state = MitigationState(char)
    res = apply_brewmaster_mitigation(state, _replay_event(raw, log_absorbed), random.Random(0))
    return res["dealt"]


def test_pt_reduces_replay_damage():
    raw, absorbed = 100_000.0, 80_000.0
    without = _dealt(_brewmaster(active_buff_spell_ids=None), raw, absorbed)
    with_pt = _dealt(_brewmaster(active_buff_spell_ids=frozenset({PT_BUFF_ID})), raw, absorbed)
    assert with_pt < without


def test_pt_pre_absorb_net_reduction_exceeds_nominal_dr():
    """Effective DR applied BEFORE a large fixed absorb subtract → much larger
    NET cut. raw=100k, absorbed=80k, vers=0, effective DR = 0.08×0.88 = 7.04%:
      without PT: 100k    - 80k = 20k
      with PT:    92.96k  - 80k = 12.96k  → ~35% net reduction from a 7.04% cut.
    This leverage is exactly why the pre-absorb placement matters (and why the
    headline delta is sensitive to the uptime constant)."""
    raw, absorbed = 100_000.0, 80_000.0
    without = _dealt(_brewmaster(active_buff_spell_ids=None), raw, absorbed)
    with_pt = _dealt(_brewmaster(active_buff_spell_ids=frozenset({PT_BUFF_ID})), raw, absorbed)
    assert without == 20_000.0
    assert with_pt == pytest.approx(12_960.0)
    net_reduction = (without - with_pt) / without
    assert net_reduction > 0.0704  # leverage: net cut >> nominal effective DR


def test_brewmaster_without_buff_is_bit_identical_to_pre_ledger():
    """No buff → ledger is identity → dealt matches the plain vers/absorb chain
    (raw - absorbed at vers=0). Guards AnonBrewmaster1-style builds from any drift."""
    assert _dealt(_brewmaster(active_buff_spell_ids=None), 100_000.0, 30_000.0) == 70_000.0


# ── detect_active_buffs helper ───────────────────────────────────────────────

_TS0 = "5/10/2026 12:00:00.000"
_TS1 = "5/10/2026 12:00:30.000"
_TARGET = "Tank-Realm-EU"


def _aura_line(ts, dest_name, spell_id, aura_type="BUFF", event="SPELL_AURA_APPLIED"):
    return (
        f'{ts}  {event},Player-1-S,"Src",0x10512,0x0,'
        f'Player-1-D,"{dest_name}",0x10512,0x0,'
        f'{spell_id},"Predictive Training",0x1,{aura_type}\n'
    )


def _epoch(ts):
    return datetime.strptime(ts, "%m/%d/%Y %H:%M:%S.%f").timestamp()


def test_detect_buff_on_target(tmp_path):
    log = tmp_path / "l.txt"
    log.write_text(_aura_line(_TS0, _TARGET, PT_BUFF_ID))
    assert detect_active_buffs(log, _TARGET, frozenset({PT_BUFF_ID})) == frozenset({PT_BUFF_ID})


def test_buff_on_other_player_not_detected(tmp_path):
    log = tmp_path / "l.txt"
    log.write_text(_aura_line(_TS0, "Someone-Else-EU", PT_BUFF_ID))
    assert detect_active_buffs(log, _TARGET, frozenset({PT_BUFF_ID})) == frozenset()


def test_debuff_not_counted_as_buff(tmp_path):
    log = tmp_path / "l.txt"
    log.write_text(_aura_line(_TS0, _TARGET, PT_BUFF_ID, aura_type="DEBUFF"))
    assert detect_active_buffs(log, _TARGET, frozenset({PT_BUFF_ID})) == frozenset()


def test_buff_outside_window_excluded(tmp_path):
    log = tmp_path / "l.txt"
    log.write_text(_aura_line(_TS1, _TARGET, PT_BUFF_ID))
    # window ends before the buff is applied
    out = detect_active_buffs(
        log,
        _TARGET,
        frozenset({PT_BUFF_ID}),
        start_time_s=_epoch(_TS0) - 1,
        end_time_s=_epoch(_TS0) + 1,
    )
    assert out == frozenset()


def test_empty_buff_set_returns_empty(tmp_path):
    log = tmp_path / "l.txt"
    log.write_text(_aura_line(_TS0, _TARGET, PT_BUFF_ID))
    assert detect_active_buffs(log, _TARGET, frozenset()) == frozenset()


def test_refresh_event_counts(tmp_path):
    log = tmp_path / "l.txt"
    log.write_text(_aura_line(_TS0, _TARGET, PT_BUFF_ID, event="SPELL_AURA_REFRESH"))
    assert detect_active_buffs(log, _TARGET, frozenset({PT_BUFF_ID})) == frozenset({PT_BUFF_ID})


# ── constants schema ─────────────────────────────────────────────────────────


def test_predictive_training_ledger_row_schema():
    ledger = load_constants()["specs"]["brewmaster_monk"]["mitigation_ledger"]
    pt = next(r for r in ledger if r["name"] == "predictive_training")
    assert pt["detect_buff_spell_id"] == PT_BUFF_ID
    assert pt["school"] == "all"
    assert pt["dr_pct"] == 0.08
    # Measured damage-weighted uptime on AnonBrewmaster2's +12 (NOT the asserted 1.0 the
    # validator refuted). One Shado-Pan run; refine when a second lands.
    assert pt["avg_uptime"] == 0.88


def test_constants_version_bumped_for_ledger():
    assert load_constants()["constants_version"] >= 23
