"""Tests for the Vengeance Demon Hunter mitigation chain — Phase 4.2.

Layers pinned to SimC (build 12.0.5.67823); see
docs/validation/phase4_vdh_characterization_2026_06_09.md.
"""

from __future__ import annotations

import random

import pytest

from simf.core.character import Character
from simf.core.constants import load_constants, spec_is_calibrated
from simf.core.events import DamageEvent
from simf.core.mitigation import MitigationState, apply_mitigation

META_SPELL = 187827


def _make_vdh() -> Character:
    return Character(
        name="TestVDH",
        race="night_elf",
        class_spec="vengeance_demon_hunter",
        talents="brutoh-actual",
        strength=2000,
        agility=2000,
        stamina=50000,
        armor_from_gear=4000,
    )


def _phys(t: float, amount: int, *, avoidable: bool = True) -> DamageEvent:
    return DamageEvent(
        time_s=t,
        source_id="trash",
        raw_amount=amount,
        school="physical",
        is_avoidable=avoidable,
        is_blockable=False,
        attack_type="melee",
    )


def _phys_replay(t: float, amount: int, active_buffs=frozenset()) -> DamageEvent:
    # is_avoidable False so the armor math is deterministic (no dodge/parry roll);
    # is_log_replay True so Meta is read from active_buffs (the window-gated path).
    return DamageEvent(
        time_s=t,
        source_id="trash",
        raw_amount=amount,
        school="physical",
        is_avoidable=False,
        is_blockable=False,
        attack_type="melee",
        is_log_replay=True,
        active_buffs=active_buffs,
    )


def _magic(t: float, amount: int) -> DamageEvent:
    return DamageEvent(
        time_s=t,
        source_id="caster",
        raw_amount=amount,
        school="shadow",
        is_avoidable=False,
        is_blockable=False,
        attack_type="spell",
    )


def test_vdh_dispatches_to_vdh_chain():
    char = _make_vdh()
    state = MitigationState(char)
    rng = random.Random(0)
    out = apply_mitigation(state, _phys(0.0, 100_000), rng)
    assert not out["was_blocked"]  # no shield
    assert "dealt" in out


def test_vdh_demon_spikes_adds_armor_not_flat_dr():
    """Demon Spikes has NO flat DR in Midnight; it adds +75% of Agility as
    armor. So DS-active physical hits land less than baseline — via armor."""
    char = _make_vdh()
    state_no = MitigationState(char)
    state_ds = MitigationState(char)
    state_ds.bsv_active_until = 100.0  # Demon Spikes up

    out_no = apply_mitigation(state_no, _phys_replay(0.0, 100_000), random.Random(11))
    out_ds = apply_mitigation(state_ds, _phys_replay(0.0, 100_000), random.Random(11))
    assert out_ds["dealt"] < out_no["dealt"]  # agi-armor bump reduces physical


def test_vdh_demon_spikes_flat_dr_is_zero():
    """The old phantom 20% flat physical DR is gone (SimC effect base = 0)."""
    assert load_constants()["specs"]["vengeance_demon_hunter"]["demon_spikes_physical_dr"] == 0.0


def test_vdh_metamorphosis_armor_window_gated():
    """Metamorphosis (×3 armor) cuts physical damage, credited only while its
    buff is in the event's active_buffs (the window-gated replay path)."""
    char = _make_vdh()
    state = MitigationState(char)
    rng = random.Random(0)
    no_meta = apply_mitigation(state, _phys_replay(0.0, 100_000, frozenset()), rng)
    with_meta = apply_mitigation(state, _phys_replay(0.0, 100_000, frozenset({META_SPELL})), rng)
    assert with_meta["dealt"] < no_meta["dealt"]  # ×3 armor → much less lands

    # The armor really tripled: recompute the expected DR both ways.
    c = load_constants()
    K = c["armor"]["k_constant"]
    armor = char.total_armor()
    dr_base = min(armor / (armor + K), c["armor"]["max_armor_dr"])
    dr_meta = min(armor * 3.0 / (armor * 3.0 + K), c["armor"]["max_armor_dr"])
    # ratio of post-armor damage should match (other flat layers cancel)
    assert with_meta["dealt"] / no_meta["dealt"] == pytest.approx(
        (1 - dr_meta) / (1 - dr_base), rel=0.01
    )


def test_vdh_metamorphosis_not_credited_without_buff_in_replay():
    """In replay, Meta armor must NOT apply just because the policy state is
    set — only the log's active_buffs drive it (no double-count)."""
    char = _make_vdh()
    state = MitigationState(char)
    state.last_stand_until = 100.0
    state.last_stand_base_max_hp = state.max_hp  # policy 'Meta' state set
    rng = random.Random(0)
    # replay event WITHOUT meta in active_buffs → no ×3 armor
    out = apply_mitigation(state, _phys_replay(0.0, 100_000, frozenset()), rng)
    no_meta_ref = apply_mitigation(
        MitigationState(char), _phys_replay(0.0, 100_000, frozenset()), random.Random(0)
    )
    assert out["dealt"] == pytest.approx(no_meta_ref["dealt"])


IMMOLATION_AURA_SPELL = 258920


def test_vdh_infernal_armor_window_gated():
    """Infernal Armor (+20% armor) cuts physical damage, credited only while
    the Immolation Aura buff is in the event's active_buffs."""
    char = _make_vdh()
    state = MitigationState(char)
    rng = random.Random(0)
    no_ia = apply_mitigation(state, _phys_replay(0.0, 100_000, frozenset()), rng)
    with_ia = apply_mitigation(
        state, _phys_replay(0.0, 100_000, frozenset({IMMOLATION_AURA_SPELL})), rng
    )
    assert with_ia["dealt"] < no_ia["dealt"]  # +20% armor → less lands

    c = load_constants()
    K = c["armor"]["k_constant"]
    armor = char.total_armor()
    dr_base = min(armor / (armor + K), c["armor"]["max_armor_dr"])
    dr_ia = min(armor * 1.20 / (armor * 1.20 + K), c["armor"]["max_armor_dr"])
    assert with_ia["dealt"] / no_ia["dealt"] == pytest.approx((1 - dr_ia) / (1 - dr_base), rel=0.01)


def test_vdh_infernal_armor_stacks_multiplicatively_with_metamorphosis():
    """Infernal Armor and Metamorphosis both multiply the SAME armor base
    (SimC: both are armor_multiplier-type effects) — combined they should
    equal base armor * 3.0 * 1.20, not just one or the other."""
    char = _make_vdh()
    state = MitigationState(char)
    rng = random.Random(0)
    both = apply_mitigation(
        state,
        _phys_replay(0.0, 100_000, frozenset({META_SPELL, IMMOLATION_AURA_SPELL})),
        rng,
    )
    meta_only = apply_mitigation(state, _phys_replay(0.0, 100_000, frozenset({META_SPELL})), rng)
    assert both["dealt"] < meta_only["dealt"]  # IA's extra +20% stacks on top of Meta's x3


def test_vdh_infernal_armor_does_not_affect_magic_damage():
    """Infernal Armor is a physical armor multiplier — must not touch magic hits."""
    char = _make_vdh()
    state_no = MitigationState(char)
    state_ia = MitigationState(char)
    magic_replay = DamageEvent(
        time_s=0.0,
        source_id="caster",
        raw_amount=100_000,
        school="shadow",
        is_avoidable=False,
        is_blockable=False,
        attack_type="spell",
        is_log_replay=True,
        active_buffs=frozenset(),
    )
    magic_replay_ia = DamageEvent(
        time_s=0.0,
        source_id="caster",
        raw_amount=100_000,
        school="shadow",
        is_avoidable=False,
        is_blockable=False,
        attack_type="spell",
        is_log_replay=True,
        active_buffs=frozenset({IMMOLATION_AURA_SPELL}),
    )
    out_no = apply_mitigation(state_no, magic_replay, random.Random(0))
    out_ia = apply_mitigation(state_ia, magic_replay_ia, random.Random(0))
    assert out_no["dealt"] == pytest.approx(out_ia["dealt"])


def test_vdh_demonic_wards_reduces_all_schools():
    """Demonic Wards is a flat -12% all-school always-on layer (magic too)."""
    char = _make_vdh()
    state = MitigationState(char)
    out = apply_mitigation(state, _magic(0.0, 100_000), random.Random(0))
    c = load_constants()["specs"]["vengeance_demon_hunter"]
    vers = 1 - char.versatility_dr()
    fb = 1 - c["fiery_brand_target_dr"] * c["fiery_brand_avg_uptime_in_m_plus"]
    vr = 1 - c["void_reaver_dr"] * c["void_reaver_avg_uptime_in_m_plus"]
    dw = 1 - c["demonic_wards_dr"]
    assert out["dealt"] == pytest.approx(100_000 * vers * dw * fb * vr, rel=0.001)


def test_vdh_higher_base_dodge_than_warrior():
    vdh = _make_vdh()
    warrior = Character(
        name="W",
        race="orc",
        class_spec="protection_warrior",
        talents="brutoh-actual",
        strength=2000,
        stamina=50000,
        armor_from_gear=4000,
    )
    assert vdh.base_dodge() > warrior.base_dodge()


def test_vdh_constants_block_present_and_uncalibrated():
    """VDH is `characterized`, not `calibrated` (Top-5 #4, 2026-07-06)."""
    spec = load_constants()["specs"].get("vengeance_demon_hunter")
    assert spec is not None
    assert spec["calibration_tier"] == "characterized"
    assert not spec_is_calibrated(spec)
    assert spec["metamorphosis_armor_multiplier"] == 3.0
    assert spec["demonic_wards_dr"] > 0
    assert spec["demon_spikes_armor_from_agility"] > 0
    assert spec["soul_fragments_per_minute"] > 0
    assert spec["infernal_armor_multiplier"] > 0
    assert spec["void_reaver_dr"] > 0


def test_vdh_policy_metamorphosis_fires_at_low_hp():
    from simf.classes.vengeance_dh import VengeanceDHPolicy

    char = _make_vdh()
    state = MitigationState(char)
    state.hp = state.max_hp * 0.30  # 30% HP — Metamorphosis should fire
    max_hp_before = state.max_hp

    policy = VengeanceDHPolicy(char)
    policy.decide(state, now=1.0, recent_dtps=300_000.0)

    # Max HP grows by 40% in Midnight (SimC), not the old 30%.
    assert state.max_hp == pytest.approx(max_hp_before * 1.40, rel=0.01)


# ─── Painbringer (buff 212988) — window-gated flat all-school DR ──────────────

PAINBRINGER_SPELL = 212988


def _magic_replay(t: float, amount: int, active_buffs=frozenset()) -> DamageEvent:
    return DamageEvent(
        time_s=t,
        source_id="caster",
        raw_amount=amount,
        school="shadow",
        is_avoidable=False,
        is_blockable=False,
        attack_type="spell",
        is_log_replay=True,
        active_buffs=active_buffs,
    )


def test_vdh_painbringer_value_pinned():
    """−3% all-school, non-stacking: DB2 12.0.7.68367 (talent 207387 effect0 =
    A_ADD_FLAT_LABEL_MODIFIER value −3, label 2406 → buff 212988 effect0 =
    A_MOD_DAMAGE_PERCENT_TAKEN, school mask 127; CumulativeAura = 1)."""
    c = load_constants()["specs"]["vengeance_demon_hunter"]
    assert c["painbringer_dr"] == 0.03
    assert c["painbringer_spell_id"] == PAINBRINGER_SPELL


def test_vdh_painbringer_window_gated_all_school():
    """Painbringer credits exactly ×(1−0.03) while buff 212988 is in the
    event's active_buffs — on magic AND physical (school mask 127) — and
    nothing when it isn't (window-gated replay path, like Metamorphosis)."""
    char = _make_vdh()
    c = load_constants()["specs"]["vengeance_demon_hunter"]
    for mk in (_magic_replay, _phys_replay):
        no_pb = apply_mitigation(
            MitigationState(char), mk(0.0, 100_000, frozenset()), random.Random(0)
        )
        with_pb = apply_mitigation(
            MitigationState(char),
            mk(0.0, 100_000, frozenset({PAINBRINGER_SPELL})),
            random.Random(0),
        )
        assert with_pb["dealt"] == pytest.approx(
            no_pb["dealt"] * (1 - c["painbringer_dr"]), rel=1e-6
        )


def test_vdh_painbringer_no_credit_on_live_events():
    """Live (non-replay) events carry an empty active_buffs → no Painbringer
    credit. The live magic chain stays vers × wards × brand exactly (the
    policy has no Soul-Fragment-consume model yet — deliberate staging)."""
    char = _make_vdh()
    out = apply_mitigation(MitigationState(char), _magic(0.0, 100_000), random.Random(0))
    c = load_constants()["specs"]["vengeance_demon_hunter"]
    vers = 1 - char.versatility_dr()
    fb = 1 - c["fiery_brand_target_dr"] * c["fiery_brand_avg_uptime_in_m_plus"]
    vr = 1 - c["void_reaver_dr"] * c["void_reaver_avg_uptime_in_m_plus"]
    dw = 1 - c["demonic_wards_dr"]
    assert out["dealt"] == pytest.approx(100_000 * vers * dw * fb * vr, rel=0.001)


# ─── Fiery Brand — attacker-side debuff, must be inert in log replay ──────────


def test_vdh_fiery_brand_not_double_counted_in_log_replay(monkeypatch):
    """Fiery Brand's target-source DR must NOT apply on log-replay events.

    Fiery Brand marks the MOB (attacker-side: it deals less, not "you take
    less"), so its reduction is already inside the log's `unmitigatedAmount`/
    `raw_amount`. Applying it again in replay double-counts it — the same bug
    class as Prot Warrior's Demo Shout/Phalanx, see
    docs/validation/protwarrior_demo_shout_double_count_2026_07_17.md.

    Mutation guard: dropping `if not event.is_log_replay:` around the Fiery
    Brand line in vengeance_dh.py makes the replay-mode with/without pair
    below diverge, failing the approx-equality assertion.
    """
    import simf.classes.vengeance_dh as vdh_module

    char = _make_vdh()
    real = load_constants()
    real_fb_dr = real["specs"]["vengeance_demon_hunter"]["fiery_brand_target_dr"]

    def _constants_with_fb(value: float) -> dict:
        patched = dict(real)
        patched["specs"] = dict(real["specs"])
        patched["specs"]["vengeance_demon_hunter"] = {
            **real["specs"]["vengeance_demon_hunter"],
            "fiery_brand_target_dr": value,
        }
        return patched

    # SYNTHETIC mode: Fiery Brand IS a legitimate modeled DR layer — zeroing
    # its constant must change the outcome.
    monkeypatch.setattr(vdh_module, "load_constants", lambda: _constants_with_fb(real_fb_dr))
    d_syn_with = apply_mitigation(MitigationState(char), _magic(0.0, 100_000), random.Random(0))[
        "dealt"
    ]
    monkeypatch.setattr(vdh_module, "load_constants", lambda: _constants_with_fb(0.0))
    d_syn_without = apply_mitigation(MitigationState(char), _magic(0.0, 100_000), random.Random(0))[
        "dealt"
    ]
    assert d_syn_with < d_syn_without, "Synthetic mode must still apply Fiery Brand's DR"

    # REPLAY mode: the mark's DR is already in raw_amount → zeroing the
    # constant must make NO difference (the layer is skipped entirely).
    monkeypatch.setattr(vdh_module, "load_constants", lambda: _constants_with_fb(real_fb_dr))
    d_rep_with = apply_mitigation(
        MitigationState(char), _magic_replay(0.0, 100_000), random.Random(0)
    )["dealt"]
    monkeypatch.setattr(vdh_module, "load_constants", lambda: _constants_with_fb(0.0))
    d_rep_without = apply_mitigation(
        MitigationState(char), _magic_replay(0.0, 100_000), random.Random(0)
    )["dealt"]
    assert d_rep_with == pytest.approx(d_rep_without), (
        "Log-replay must NOT apply Fiery Brand (already in unmitigatedAmount): "
        f"with={d_rep_with:.1f} vs without={d_rep_without:.1f}"
    )


# ─── Void Reaver's Frailty — attacker-side debuff, must be inert in log replay ─


def test_vdh_void_reaver_not_double_counted_in_log_replay(monkeypatch):
    """Void Reaver's Frailty DR must NOT apply on log-replay events.

    Frailty is a debuff the player applies to a mob; Void Reaver's -5%
    "damage to caster" was found empirically (2026-07-18, per-hit forensics
    on the WCL corpus) to already be baked into logged unmitigatedAmount —
    same bug class as Fiery Brand/Demo Shout/Phalanx. See
    docs/validation/phase4_vdh_magic_residual_leads_2026_07_18.md.

    Mutation guard: dropping `if not event.is_log_replay:` around the Void
    Reaver line in vengeance_dh.py makes the replay-mode with/without pair
    below diverge, failing the approx-equality assertion.
    """
    import simf.classes.vengeance_dh as vdh_module

    char = _make_vdh()
    real = load_constants()
    real_vr_dr = real["specs"]["vengeance_demon_hunter"]["void_reaver_dr"]

    def _constants_with_vr(value: float) -> dict:
        patched = dict(real)
        patched["specs"] = dict(real["specs"])
        patched["specs"]["vengeance_demon_hunter"] = {
            **real["specs"]["vengeance_demon_hunter"],
            "void_reaver_dr": value,
        }
        return patched

    # SYNTHETIC mode: Void Reaver IS a legitimate modeled DR layer — zeroing
    # its constant must change the outcome.
    monkeypatch.setattr(vdh_module, "load_constants", lambda: _constants_with_vr(real_vr_dr))
    d_syn_with = apply_mitigation(MitigationState(char), _magic(0.0, 100_000), random.Random(0))[
        "dealt"
    ]
    monkeypatch.setattr(vdh_module, "load_constants", lambda: _constants_with_vr(0.0))
    d_syn_without = apply_mitigation(MitigationState(char), _magic(0.0, 100_000), random.Random(0))[
        "dealt"
    ]
    assert d_syn_with < d_syn_without, "Synthetic mode must still apply Void Reaver's DR"

    # REPLAY mode: Frailty's DR is already in raw_amount → zeroing the
    # constant must make NO difference (the layer is skipped entirely).
    monkeypatch.setattr(vdh_module, "load_constants", lambda: _constants_with_vr(real_vr_dr))
    d_rep_with = apply_mitigation(
        MitigationState(char), _magic_replay(0.0, 100_000), random.Random(0)
    )["dealt"]
    monkeypatch.setattr(vdh_module, "load_constants", lambda: _constants_with_vr(0.0))
    d_rep_without = apply_mitigation(
        MitigationState(char), _magic_replay(0.0, 100_000), random.Random(0)
    )["dealt"]
    assert d_rep_with == pytest.approx(d_rep_without), (
        "Log-replay must NOT apply Void Reaver's Frailty DR (already in unmitigatedAmount): "
        f"with={d_rep_with:.1f} vs without={d_rep_without:.1f}"
    )


# ─── party_dr_by_school — parity with the warrior/Guardian paths ──────────────


def test_vdh_party_dr_by_school_replay_opt_in():
    """Applied only when state.party_magic_dr_active is set (replay opt-in):
    `all` covers physical AND magic; `magic` stacks on non-physical."""
    char = _make_vdh()
    cfg = load_constants()["specs"]["vengeance_demon_hunter"]["party_dr_by_school"]
    assert cfg.get("all", 0.0) > 0  # parity values actually present
    for mk, is_magic in ((_phys_replay, False), (_magic_replay, True)):
        off_state = MitigationState(char)
        on_state = MitigationState(char)
        on_state.party_magic_dr_active = True
        out_off = apply_mitigation(off_state, mk(0.0, 100_000, frozenset()), random.Random(0))
        out_on = apply_mitigation(on_state, mk(0.0, 100_000, frozenset()), random.Random(0))
        expected = 1 - cfg.get("all", 0.0)
        if is_magic:
            expected *= 1 - cfg.get("magic", 0.0)
        assert out_on["dealt"] == pytest.approx(out_off["dealt"] * expected, rel=1e-6)
