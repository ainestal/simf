"""Tests for hydrate race detection from racial-ability casts.

COMBATANT_INFO carries no race field, so the local-log hydrate infers race from
a player casting their signature racial (War Stomp → Tauren, etc.). This recovers
racial HP/vers/DR (e.g. Tauren +5% Endurance) that the `human` default drops.
"""

from simf.io.combat_log import detect_race

_TS0 = "6/20/2026 12:00:00.000"
_TS1 = "6/20/2026 12:00:30.000"
_TARGET = "AnonGuardian1-AnonRealm1-EU"


def _cast_line(ts, src_name, spell_id, spell_name="Racial"):
    """A SPELL_CAST_SUCCESS where `src_name` casts `spell_id`."""
    return (
        f'{ts}  SPELL_CAST_SUCCESS,Player-1-T,"{src_name}",0x511,0x0,'
        f'Player-1-T,"{src_name}",0x511,0x0,'
        f'{spell_id},"{spell_name}",0x1\n'
    )


def test_war_stomp_detects_tauren(tmp_path):
    log = tmp_path / "l.txt"
    log.write_text(_cast_line(_TS0, _TARGET, 20549, "War Stomp"))
    assert detect_race(log, _TARGET) == "tauren"


def test_each_mapped_racial(tmp_path):
    cases = {
        20549: "tauren",
        255654: "highmountain_tauren",
        20594: "dwarf",
        287712: "kul_tiran",
    }
    for spell_id, race in cases.items():
        log = tmp_path / f"l_{spell_id}.txt"
        log.write_text(_cast_line(_TS0, _TARGET, spell_id))
        assert detect_race(log, _TARGET) == race, f"{spell_id} → {race}"


def test_no_racial_cast_returns_none(tmp_path):
    log = tmp_path / "l.txt"
    log.write_text(_cast_line(_TS0, _TARGET, 6673, "Battle Shout"))  # not a racial
    assert detect_race(log, _TARGET) is None


def test_racial_cast_by_other_player_ignored(tmp_path):
    log = tmp_path / "l.txt"
    log.write_text(_cast_line(_TS0, "SomeoneElse-Realm-EU", 20549, "War Stomp"))
    assert detect_race(log, _TARGET) is None


def test_racial_outside_window_excluded(tmp_path):
    log = tmp_path / "l.txt"
    log.write_text(_cast_line(_TS1, _TARGET, 20549, "War Stomp"))
    from datetime import datetime

    end = datetime.strptime(_TS0, "%m/%d/%Y %H:%M:%S.%f").timestamp() + 5
    assert detect_race(log, _TARGET, end_time_s=end) is None


def test_empty_log_returns_none(tmp_path):
    log = tmp_path / "l.txt"
    log.write_text("")
    assert detect_race(log, _TARGET) is None
