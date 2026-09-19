"""Real-log integration smoke for the coverage-coaching pipeline.

Combat logs are not committed (too large), so this skips when none is present
(CI). When a Brutoh warrior log IS present locally it exercises the full
io → core path end-to-end and pins the structural invariants the unit tests
can't see on synthetic data (real aura interleaving, real damage schools,
real per-source debuffs).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from simf.core import coaching
from simf.core.bleed_detection import is_bleed
from simf.core.constants import load_constants
from simf.io.combat_log import (
    iter_damage_events,
    parse_cast_events,
    parse_challenge_modes,
    parse_self_buff_windows,
    parse_source_debuff_windows,
)

_EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
_TANK = "Brutoh-Uldum-EU"
_SPEC = "protection_warrior"


def _find_brutoh_run():
    """First (log_path, run) where Brutoh took ≥20 hits, else (None, None)."""
    if not _EXAMPLES.exists():
        return None, None
    for log in sorted(_EXAMPLES.rglob("WoWCombatLog-*.txt")):
        try:
            runs = parse_challenge_modes(log)
        except Exception:  # noqa: S112 — best-effort fallback
            continue
        for run in sorted(runs, key=lambda r: -(r.duration_ms or 0)):
            if run.end_time_s is None:
                continue
            n = sum(1 for _ in iter_damage_events(log, _TANK, run.start_time_s, run.end_time_s))
            if n >= 20:
                return log, run
    return None, None


def _build(log, run):
    C = load_constants()
    levers = coaching.levers_for_spec(_SPEC, C)
    buff_ids = {lv.spell_id for lv in levers if lv.detect == "buff"}
    buff_windows = parse_self_buff_windows(
        log,
        _TANK,
        buff_ids,
        start_time_s=run.start_time_s,
        end_time_s=run.end_time_s,
        start_byte_offset=run.start_byte_offset,
    )
    debuff = {}
    for lv in levers:
        if lv.detect == "debuff_on_source":
            debuff[lv.spell_id] = parse_source_debuff_windows(
                log,
                _TANK,
                lv.spell_id,
                duration_s=lv.duration_s,
                start_time_s=run.start_time_s,
                end_time_s=run.end_time_s,
                start_byte_offset=run.start_byte_offset,
            )
    events = list(iter_damage_events(log, _TANK, run.start_time_s, run.end_time_s))
    cfg = C["coaching"]
    interrupt_ability = coaching.interrupt_ability_for_spec(_SPEC, C)
    own_cast_times = ()
    if interrupt_ability is not None:
        own_cast_times = tuple(
            c.time_s
            for c in parse_cast_events(
                log,
                source_name=_TANK,
                spell_name=interrupt_ability.name,
                spell_id=interrupt_ability.spell_id,
                start_time_s=run.start_time_s,
                end_time_s=run.end_time_s,
            )
        )
    return coaching.build_coverage_report(
        _SPEC,
        events,
        levers=levers,
        buff_windows=buff_windows,
        debuff_windows_by_source=debuff,
        run_start=run.start_time_s,
        run_end=run.end_time_s,
        is_bleed_fn=is_bleed,
        top_n=cfg["top_hits_n"],
        min_events=cfg["min_events_for_verdict"],
        interrupt_ability=interrupt_ability,
        own_interrupt_cast_times=own_cast_times,
    )


def test_real_log_coverage_invariants():
    log, run = _find_brutoh_run()
    if log is None:
        pytest.skip("no local WoWCombatLog with a Brutoh run present")

    rep = _build(log, run)

    # Structural invariants that must hold on any real run.
    assert "%" not in rep.headline  # answer-first, no false precision
    assert rep.n_total == len(rep.top_hits)
    assert rep.n_total <= load_constants()["coaching"]["top_hits_n"]
    assert 0 <= rep.n_covered <= rep.n_total

    # covered/uncovered partition is exact and consistent with covered_by.
    for h in rep.top_hits:
        assert (h.covered_by != ()) == h.covered
    assert rep.n_covered == sum(1 for h in rep.top_hits if h.covered_by)

    # top_hits are sorted biggest-first.
    amounts = [h.amount for h in rep.top_hits]
    assert amounts == sorted(amounts, reverse=True)

    # school scope is honored: a physical-scope lever never appears on a
    # magic hit's covered_by.
    levers = {lv.name: lv for lv in coaching.levers_for_spec(_SPEC, load_constants())}
    for h in rep.top_hits:
        fam = coaching.school_family(h.school)
        for name in h.covered_by:
            assert coaching._scope_covers(levers[name].school_scope, fam)

    # continuous uptime is a valid percentage, never a coverage entry.
    for c in rep.continuous:
        assert 0.0 <= c.uptime_pct <= 100.0
        assert c.name not in rep.coverage_levers_considered

    # kick-availability split: Brutoh is a warrior, so Pummel is always
    # registered — kick_was_ready must be assessable (not None) on every
    # interruptible hit, and never assessable on a non-interruptible one.
    assert rep.interrupt_ability_name == "Pummel"
    for h in rep.top_hits:
        if h.interruptible:
            assert h.kick_was_ready in (True, False)
        else:
            assert h.kick_was_ready is None
    assert rep.n_kick_ready + rep.n_kick_on_cooldown == rep.n_interruptible
