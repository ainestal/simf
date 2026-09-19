"""Unit tests for ``io/wcl_replay.py`` field-mapping logic.

All tests run against synthetic ``DamageTakenEvent`` fixtures — no
network, no fixtures depending on WCL credentials. The live-fetch
``wcl_to_replay_data`` wrapper is covered indirectly: it composes
``fetch_report`` + ``fetch_damage_events`` (both have their own tests)
with ``adapt_events`` (tested here).
"""

from __future__ import annotations

from simf.io.combat_log import DamageTakenEvent
from simf.io.wcl_replay import adapt_events


def _make_dte(**overrides) -> DamageTakenEvent:
    """Build a DamageTakenEvent with sensible defaults, override per test."""
    base = {
        "time_s": 1000.0,
        "event_type": "SPELL_DAMAGE",
        "source_name": "Boss",
        "spell_name": "Fireball",
        "school": "fire",
        "amount": 80000,
        "base_amount": 100000,
        "overkill": 0,
        "blocked": 0,
        "absorbed": 5000,
        "resisted": 0,
        "is_critical": False,
        "is_glancing": False,
    }
    base.update(overrides)
    return DamageTakenEvent(**base)


# ─── school passthrough ──────────────────────────────────────────────────────


def test_school_passthrough_all_seven_schools():
    schools = ["physical", "fire", "shadow", "frost", "nature", "arcane", "holy"]
    raw = [_make_dte(school=s, event_type="SPELL_DAMAGE") for s in schools]
    events, _ = adapt_events(raw, target_name="Brutoh-Uldum-EU", run_start_time_s=0.0)
    assert [e.school for e in events] == schools


# ─── raw_amount fallback ─────────────────────────────────────────────────────


def test_raw_amount_uses_base_amount_when_present():
    raw = [_make_dte(base_amount=123456, amount=50, blocked=0, absorbed=0, resisted=0)]
    events, _ = adapt_events(raw, target_name="Brutoh", run_start_time_s=0.0)
    assert events[0].raw_amount == 123456.0


def test_raw_amount_fallback_when_base_amount_zero():
    # When unmitigatedAmount is absent on old logs, base_amount == 0.
    # Adapter must reconstruct: amount + blocked + absorbed + resisted.
    raw = [_make_dte(base_amount=0, amount=60000, blocked=15000, absorbed=20000, resisted=5000)]
    events, _ = adapt_events(raw, target_name="Brutoh", run_start_time_s=0.0)
    assert events[0].raw_amount == 60000.0 + 15000.0 + 20000.0 + 5000.0


# ─── attack_type derivation ──────────────────────────────────────────────────


def test_attack_type_swing_is_melee():
    raw = [_make_dte(event_type="SWING_DAMAGE", school="physical")]
    events, _ = adapt_events(raw, target_name="Brutoh", run_start_time_s=0.0)
    assert events[0].attack_type == "melee"


def test_attack_type_range_is_ranged():
    raw = [_make_dte(event_type="RANGE_DAMAGE", school="physical")]
    events, _ = adapt_events(raw, target_name="Brutoh", run_start_time_s=0.0)
    assert events[0].attack_type == "ranged"


def test_attack_type_spell_default():
    raw = [_make_dte(event_type="SPELL_DAMAGE", school="fire")]
    events, _ = adapt_events(raw, target_name="Brutoh", run_start_time_s=0.0)
    assert events[0].attack_type == "spell"


# ─── is_blockable ────────────────────────────────────────────────────────────


def test_is_blockable_true_for_physical_melee():
    raw = [_make_dte(event_type="SWING_DAMAGE", school="physical")]
    events, _ = adapt_events(raw, target_name="Brutoh", run_start_time_s=0.0)
    assert events[0].is_blockable is True


def test_is_blockable_true_for_physical_ranged():
    raw = [_make_dte(event_type="RANGE_DAMAGE", school="physical")]
    events, _ = adapt_events(raw, target_name="Brutoh", run_start_time_s=0.0)
    assert events[0].is_blockable is True


def test_is_blockable_false_for_physical_spell():
    raw = [_make_dte(event_type="SPELL_DAMAGE", school="physical")]
    events, _ = adapt_events(raw, target_name="Brutoh", run_start_time_s=0.0)
    assert events[0].is_blockable is False


def test_is_blockable_false_for_fire_melee():
    raw = [_make_dte(event_type="SWING_DAMAGE", school="fire")]
    events, _ = adapt_events(raw, target_name="Brutoh", run_start_time_s=0.0)
    assert events[0].is_blockable is False


# ─── is_bleed ────────────────────────────────────────────────────────────────


def test_is_bleed_true_for_periodic_rake_tick():
    # Rake's DoT tick: tick_flag=True is what makes it a real bleed.
    raw = [
        _make_dte(
            spell_name="Rake", school="physical", event_type="SPELL_PERIODIC_DAMAGE", tick_flag=True
        )
    ]
    events, _ = adapt_events(raw, target_name="Brutoh", run_start_time_s=0.0)
    assert events[0].is_bleed is True


def test_is_bleed_false_for_direct_rake_hit():
    # 2026-07-18: Rake's initial DIRECT hit (tick_flag=False/default) shares
    # its name with the bleed DoT tick but is ordinary armor-mitigated
    # physical damage — WoW has no such thing as a one-shot bleed. Confirmed
    # empirically for the same name-collision class (Searing Rend, Rending
    # Gore) via per-hit forensics on the ratified calibration corpora; see
    # docs/validation/bleed_fragment_periodicity_gap_2026_07_18.md.
    raw = [_make_dte(spell_name="Rake", school="physical", event_type="SPELL_DAMAGE")]
    events, _ = adapt_events(raw, target_name="Brutoh", run_start_time_s=0.0)
    assert events[0].is_bleed is False


def test_is_bleed_false_for_non_bleed_spell():
    raw = [_make_dte(spell_name="Fireball", school="fire", event_type="SPELL_DAMAGE")]
    events, _ = adapt_events(raw, target_name="Brutoh", run_start_time_s=0.0)
    assert events[0].is_bleed is False


# ─── is_self_inflicted (with realm-suffix normalisation) ─────────────────────


def test_is_self_inflicted_exact_match():
    raw = [_make_dte(source_name="Brutoh", amount=50, base_amount=999, school="nature")]
    events, _ = adapt_events(raw, target_name="Brutoh", run_start_time_s=0.0)
    assert events[0].is_self_inflicted is True


def test_is_self_inflicted_with_realm_suffix_on_target_only():
    # The .txt path passes "Brutoh-Uldum-EU" as target; WCL's source.name
    # for the same character may come back bare. Normalisation must
    # strip both to the bare name before comparing.
    raw = [_make_dte(source_name="Brutoh", amount=50, base_amount=999, school="nature")]
    events, _ = adapt_events(raw, target_name="Brutoh-Uldum-EU", run_start_time_s=0.0)
    assert events[0].is_self_inflicted is True


def test_is_self_inflicted_with_realm_suffix_on_source_only():
    # And the reverse: WCL sometimes carries the suffix on the actor
    # name (see ``_fetch_actor_id`` realm-aware match).
    raw = [_make_dte(source_name="Brutoh-Stormrage", amount=50, base_amount=999, school="nature")]
    events, _ = adapt_events(raw, target_name="Brutoh", run_start_time_s=0.0)
    assert events[0].is_self_inflicted is True


def test_self_inflicted_uses_amount_not_base_amount():
    # Stagger / self-DoT events carry the HP delta in ``amount``; the
    # ``base_amount`` (if any) is irrelevant. Mirrors load_replay:61-75.
    raw = [
        _make_dte(
            source_name="Brutoh",
            amount=12345,
            base_amount=99999,
            absorbed=7777,
            school="physical",
        )
    ]
    events, _ = adapt_events(raw, target_name="Brutoh", run_start_time_s=0.0)
    ev = events[0]
    assert ev.is_self_inflicted is True
    assert ev.raw_amount == 12345.0
    assert ev.is_dot_tick is True
    assert ev.is_avoidable is False
    assert ev.is_blockable is False
    # Self-inflicted always sets log_absorbed to 0.0 (the absorb already
    # happened when the pool was filled — don't double-count).
    assert ev.log_absorbed == 0.0


# ─── time_s rebasing ─────────────────────────────────────────────────────────


def test_time_s_rebased_to_run_relative():
    raw = [_make_dte(time_s=1_700_000_100.0)]
    events, _ = adapt_events(raw, target_name="Brutoh", run_start_time_s=1_700_000_000.0)
    assert events[0].time_s == 100.0


# ─── log_absorbed passthrough ────────────────────────────────────────────────


def test_log_absorbed_passes_through_for_non_self_events():
    raw = [_make_dte(absorbed=42000, source_name="Boss")]
    events, _ = adapt_events(raw, target_name="Brutoh", run_start_time_s=0.0)
    assert events[0].log_absorbed == 42000.0


# ─── invariants every event must satisfy ─────────────────────────────────────


def test_is_log_replay_always_true():
    raw = [
        _make_dte(),
        _make_dte(source_name="Brutoh", amount=10, base_amount=0),  # self
    ]
    events, _ = adapt_events(raw, target_name="Brutoh", run_start_time_s=0.0)
    assert all(e.is_log_replay is True for e in events)


def test_is_avoidable_always_false():
    # Log events already survived avoidance — never re-roll dodge/parry.
    raw = [
        _make_dte(event_type="SWING_DAMAGE", school="physical"),
        _make_dte(event_type="SPELL_DAMAGE", school="fire"),
    ]
    events, _ = adapt_events(raw, target_name="Brutoh", run_start_time_s=0.0)
    assert all(e.is_avoidable is False for e in events)


# ─── is_dot_tick classification (tick_flag from WCL) ─────────────────────────


def test_is_dot_tick_driven_by_tick_flag_true():
    # Probe finding: WCL events carry ``tick: true`` on periodic damage;
    # ``_map_event`` reads it into ``DamageTakenEvent.tick_flag`` and
    # the adapter routes that straight to ``DamageEvent.is_dot_tick``.
    raw = [_make_dte(spell_name="Shadow Word: Pain", tick_flag=True)]
    events, _ = adapt_events(raw, target_name="Brutoh", run_start_time_s=0.0)
    assert events[0].is_dot_tick is True


def test_is_dot_tick_driven_by_tick_flag_false_default():
    raw = [_make_dte(spell_name="Shadow Bolt")]  # tick_flag defaults to False
    events, _ = adapt_events(raw, target_name="Brutoh", run_start_time_s=0.0)
    assert events[0].is_dot_tick is False


# ─── aggregate sums ──────────────────────────────────────────────────────────


def test_aggregates_sum_dealt_blocked_absorbed_resisted():
    raw = [
        _make_dte(amount=100, blocked=10, absorbed=5, resisted=2),
        _make_dte(amount=200, blocked=20, absorbed=15, resisted=8),
        _make_dte(amount=50, blocked=0, absorbed=0, resisted=0),
    ]
    _events, agg = adapt_events(raw, target_name="Brutoh", run_start_time_s=0.0)
    assert agg["actual_dealt"] == 350
    assert agg["actual_blocked"] == 30
    assert agg["actual_absorbed"] == 20
    assert agg["actual_resisted"] == 10
