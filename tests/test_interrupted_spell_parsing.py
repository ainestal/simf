"""Tests for `parse_interrupted_spell_ids` — the evidence source for the
coaching-coverage surface's "this cast was interruptible" lever.

Real `SPELL_INTERRUPT` line shape (verified against `examples/` logs):

    SPELL_INTERRUPT,srcGUID,"srcName",srcFlags,srcRaidFlags,
        dstGUID,"dstName",dstFlags,dstRaidFlags,
        spellId,"spellName",spellSchool,          <- the INTERRUPTING ability
        extraSpellId,"extraSpellName",extraSpellSchool   <- the INTERRUPTED cast

`extraSpellId` (fields[11]) is the spell we want: proof that this exact
spell_id can be, and was, kicked.
"""

from __future__ import annotations

from simf.io.combat_log import parse_interrupted_spell_ids


def _interrupt_line(ts: str, interrupt_spell_id: int, interrupted_spell_id: int) -> str:
    return (
        f'{ts}  SPELL_INTERRUPT,Player-1-1,"Tank-Realm-EU",0x511,0x0,'
        f'Creature-0-1-1-1-1-1,"Boss",0x10a48,0x0,'
        f'{interrupt_spell_id},"Pummel",0x1,'
        f'{interrupted_spell_id},"Icy Blast",0x10\n'
    )


def test_extracts_the_interrupted_spell_not_the_interrupting_one(tmp_path):
    log = tmp_path / "test.log"
    log.write_text(_interrupt_line("6/29/2026 13:44:00.000", 6552, 396640))

    ids = parse_interrupted_spell_ids(log)

    assert ids == frozenset({396640})
    assert 6552 not in ids  # Pummel itself must not leak in


def test_collects_across_multiple_interrupts(tmp_path):
    log = tmp_path / "test.log"
    log.write_text(
        _interrupt_line("6/29/2026 13:44:00.000", 6552, 111)
        + _interrupt_line("6/29/2026 13:45:00.000", 47528, 222)
        + _interrupt_line("6/29/2026 13:46:00.000", 6552, 111)  # duplicate — set dedups
    )

    assert parse_interrupted_spell_ids(log) == frozenset({111, 222})


def test_no_interrupts_returns_empty_frozenset(tmp_path):
    log = tmp_path / "test.log"
    log.write_text(
        '6/29/2026 13:44:00.000  SPELL_DAMAGE,Creature-0-1-1-1-1-1,"Boss",0x10a48,0x0,'
        'Player-1-1,"Tank-Realm-EU",0x511,0x0,111,"Icy Blast",0x10,0,0,50000,50000,0,0,100,0,0,nil,nil\n'
    )

    assert parse_interrupted_spell_ids(log) == frozenset()


def test_respects_time_window(tmp_path):
    from simf.io.combat_log_core import parse_combat_log_line

    lines = [
        _interrupt_line("6/29/2026 13:00:00.000", 6552, 111),  # before window
        _interrupt_line("6/29/2026 13:10:00.000", 6552, 222),  # in window
        _interrupt_line("6/29/2026 13:20:00.000", 6552, 333),  # after window
    ]
    times = [parse_combat_log_line(line)[0] for line in lines]
    log = tmp_path / "test.log"
    log.write_text("".join(lines))

    ids = parse_interrupted_spell_ids(log, start_time_s=times[0] + 1, end_time_s=times[2] - 1)

    assert ids == frozenset({222})


def test_malformed_line_is_skipped_not_fatal(tmp_path):
    log = tmp_path / "test.log"
    log.write_text(
        '6/29/2026 13:44:00.000  SPELL_INTERRUPT,Player-1-1,"Tank-Realm-EU",0x511,0x0,'
        'Creature-0-1-1-1-1-1,"Boss",0x10a48,0x0,6552,"Pummel",0x1\n'  # missing extraSpell fields
        + _interrupt_line("6/29/2026 13:45:00.000", 6552, 999)
    )

    assert parse_interrupted_spell_ids(log) == frozenset({999})
