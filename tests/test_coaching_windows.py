"""Tests for the aura-window parsers that feed the coverage join.

`parse_self_buff_windows` / `parse_source_debuff_windows` turn raw
SPELL_AURA_APPLIED/REMOVED lines into [(start, end), …] intervals. These are
the only file-I/O piece of the coaching feature; the join itself is pure (see
test_coaching.py). Synthetic logs keep the assertions exact.
"""

from __future__ import annotations

from simf.io.combat_log import (
    parse_combat_log_line,
    parse_self_buff_windows,
    parse_source_debuff_windows,
)

TANK = "Brutoh-Uldum-EU"
TANK_GUID = "Player-1-AAAA"
OTHER = "Someone-Else-EU"


def _line(
    off,
    event,
    *,
    src_guid,
    src_name,
    dest_guid,
    dest_name,
    spell_id,
    spell_name,
    aura_type,
    school="0x1",
):
    m, s = divmod(off, 60)
    ts = f"1/1/2025 12:{m:02d}:{s:02d}.0000"
    return (
        f'{ts}  {event},{src_guid},"{src_name}",0x511,0x0,'
        f'{dest_guid},"{dest_name}",0x511,0x0,'
        f'{spell_id},"{spell_name}",{school},{aura_type}\n'
    )


def _self_buff(off, event, spell_id, name, *, dest_name=TANK, aura="BUFF"):
    return _line(
        off,
        event,
        src_guid=TANK_GUID,
        src_name=TANK,
        dest_guid=TANK_GUID,
        dest_name=dest_name,
        spell_id=spell_id,
        spell_name=name,
        aura_type=aura,
    )


def _epoch(off):
    line = _line(
        off,
        "X",
        src_guid="g",
        src_name="n",
        dest_guid="g",
        dest_name="n",
        spell_id=1,
        spell_name="s",
        aura_type="BUFF",
    )
    return parse_combat_log_line(line)[0]


def _write(tmp_path, lines):
    p = tmp_path / "log.txt"
    p.write_text("".join(lines))
    return p


# ── self-buff windows ────────────────────────────────────────────────────────


def test_basic_apply_remove(tmp_path):
    log = _write(
        tmp_path,
        [
            _self_buff(10, "SPELL_AURA_APPLIED", 871, "Shield Wall"),
            _self_buff(20, "SPELL_AURA_REMOVED", 871, "Shield Wall"),
        ],
    )
    w = parse_self_buff_windows(log, TANK, {871}, end_time_s=_epoch(100))
    assert len(w[871]) == 1
    s, e = w[871][0]
    assert e - s == 10.0


def test_refresh_keeps_single_window(tmp_path):
    log = _write(
        tmp_path,
        [
            _self_buff(10, "SPELL_AURA_APPLIED", 871, "Shield Wall"),
            _self_buff(15, "SPELL_AURA_REFRESH", 871, "Shield Wall"),
            _self_buff(25, "SPELL_AURA_REMOVED", 871, "Shield Wall"),
        ],
    )
    w = parse_self_buff_windows(log, TANK, {871}, end_time_s=_epoch(100))
    assert len(w[871]) == 1
    s, e = w[871][0]
    assert e - s == 15.0  # 10 -> 25, refresh did not split


def test_still_open_closed_at_run_end(tmp_path):
    log = _write(
        tmp_path,
        [
            _self_buff(10, "SPELL_AURA_APPLIED", 871, "Shield Wall"),
        ],
    )
    end = _epoch(40)
    w = parse_self_buff_windows(log, TANK, {871}, end_time_s=end)
    assert len(w[871]) == 1
    assert w[871][0][1] == end


def test_broken_closes_window(tmp_path):
    """SPELL_AURA_BROKEN (standard field layout) closes a window like REMOVED."""
    log = _write(
        tmp_path,
        [
            _self_buff(10, "SPELL_AURA_APPLIED", 871, "Shield Wall"),
            _self_buff(20, "SPELL_AURA_BROKEN", 871, "Shield Wall"),
        ],
    )
    w = parse_self_buff_windows(log, TANK, {871}, end_time_s=_epoch(100))
    assert len(w[871]) == 1
    assert w[871][0][1] - w[871][0][0] == 10.0


def test_remove_without_open_is_skipped(tmp_path):
    """Never fabricate a window — under-credit beats over-credit."""
    log = _write(
        tmp_path,
        [
            _self_buff(20, "SPELL_AURA_REMOVED", 871, "Shield Wall"),
        ],
    )
    w = parse_self_buff_windows(log, TANK, {871}, end_time_s=_epoch(100))
    assert w[871] == []


def test_ignores_other_player_and_debuff(tmp_path):
    log = _write(
        tmp_path,
        [
            # buff on a DIFFERENT player
            _self_buff(10, "SPELL_AURA_APPLIED", 871, "Shield Wall", dest_name=OTHER),
            _self_buff(20, "SPELL_AURA_REMOVED", 871, "Shield Wall", dest_name=OTHER),
            # a DEBUFF on the tank with the same id (shouldn't happen, but guard)
            _self_buff(30, "SPELL_AURA_APPLIED", 871, "Shield Wall", aura="DEBUFF"),
            _self_buff(40, "SPELL_AURA_REMOVED", 871, "Shield Wall", aura="DEBUFF"),
        ],
    )
    w = parse_self_buff_windows(log, TANK, {871}, end_time_s=_epoch(100))
    assert w[871] == []


def test_only_requested_spell_ids(tmp_path):
    log = _write(
        tmp_path,
        [
            _self_buff(10, "SPELL_AURA_APPLIED", 871, "Shield Wall"),
            _self_buff(20, "SPELL_AURA_REMOVED", 871, "Shield Wall"),
            _self_buff(12, "SPELL_AURA_APPLIED", 99999, "Random Buff"),
            _self_buff(22, "SPELL_AURA_REMOVED", 99999, "Random Buff"),
        ],
    )
    w = parse_self_buff_windows(log, TANK, {871}, end_time_s=_epoch(100))
    assert set(w) == {871}
    assert len(w[871]) == 1


def test_empty_id_set_returns_empty(tmp_path):
    log = _write(tmp_path, [_self_buff(10, "SPELL_AURA_APPLIED", 871, "Shield Wall")])
    assert parse_self_buff_windows(log, TANK, set(), end_time_s=_epoch(100)) == {}


# ── source-debuff windows (Demoralizing Shout) ───────────────────────────────


def _demo(off, event, enemy_guid, enemy_name, *, applier=TANK):
    return _line(
        off,
        event,
        src_guid=TANK_GUID,
        src_name=applier,
        dest_guid=enemy_guid,
        dest_name=enemy_name,
        spell_id=1160,
        spell_name="Demoralizing Shout",
        aura_type="DEBUFF",
    )


def test_debuff_keyed_by_enemy_guid(tmp_path):
    log = _write(
        tmp_path,
        [
            _demo(10, "SPELL_AURA_APPLIED", "mobA", "Raging Squall"),
            _demo(18, "SPELL_AURA_REMOVED", "mobA", "Raging Squall"),
            _demo(12, "SPELL_AURA_APPLIED", "mobB", "Outcast Warrior"),
            _demo(30, "SPELL_AURA_REMOVED", "mobB", "Outcast Warrior"),
        ],
    )
    w = parse_source_debuff_windows(log, TANK, 1160, end_time_s=_epoch(100))
    assert set(w) == {"mobA", "mobB"}
    assert w["mobA"][0][1] - w["mobA"][0][0] == 8.0
    assert w["mobB"][0][1] - w["mobB"][0][0] == 18.0


def test_debuff_ignores_other_applier(tmp_path):
    """Another warrior's Demo Shout must not be credited to this tank."""
    log = _write(
        tmp_path,
        [
            _demo(10, "SPELL_AURA_APPLIED", "mobA", "Raging Squall", applier=OTHER),
            _demo(18, "SPELL_AURA_REMOVED", "mobA", "Raging Squall", applier=OTHER),
        ],
    )
    w = parse_source_debuff_windows(log, TANK, 1160, end_time_s=_epoch(100))
    assert w == {}


def test_debuff_unclosed_capped_by_duration(tmp_path):
    log = _write(
        tmp_path,
        [
            _demo(10, "SPELL_AURA_APPLIED", "mobA", "Raging Squall"),
            # no REMOVED — mob died with debuff up
        ],
    )
    w = parse_source_debuff_windows(log, TANK, 1160, duration_s=8.0, end_time_s=_epoch(100))
    s, e = w["mobA"][0]
    assert e - s == 8.0  # capped at open + duration, not run end
