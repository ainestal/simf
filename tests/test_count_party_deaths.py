"""Regression tests for `count_party_deaths_in_run`.

The log-picker preview surfaces a "did anyone wipe in this run" badge
next to each run's dungeon + key identity. The badge is sourced from
this helper — a cheap UNIT_DIED-only scan over the run's byte window
(orders of magnitude faster than the full party-role detection pass).

The helper has to:
  - Count Player-* UNIT_DIED events only (NPC / pet deaths abound in
    M+ and don't belong in a "party deaths" indicator).
  - Honour the run window — deaths before / after a run don't leak in.
  - Honour `start_byte_offset` so deep runs in a big session log skip
    the leading bytes (matches the perf shape of `detect_party_roles`
    and `iter_death_events`).
"""

from __future__ import annotations

from simf.io.combat_log import count_party_deaths_in_run

# Each line is its own combat log event. Timestamps are local-time
# decimal-month/day/year, matching what WoW writes.

LINE_PLAYER_DIED_A = (
    "5/11/2026 12:00:10.000  UNIT_DIED,0x0,Boss,0x1,0x0,"
    "Player-1234-ABCD1234,Brutoh-Uldum-EU,0x512,0x0,0\n"
)
LINE_PLAYER_DIED_B = (
    "5/11/2026 12:00:20.000  UNIT_DIED,0x0,Boss,0x1,0x0,"
    "Player-9999-FFFFFFFF,Anothertank-Uldum-EU,0x512,0x0,0\n"
)
LINE_NPC_DIED = (
    "5/11/2026 12:00:15.000  UNIT_DIED,0x0,Boss,0x1,0x0,"
    "Creature-0-4244-2915-97430-248373-00008889FF,Circuit Seer,0xa48,0x0,0\n"
)
LINE_PET_DIED = (
    # Real WoW pet GUID shape: Pet-0-<server>-<instance>-<zone>-<npc>-<spawn>.
    # Hunter pets, mage water elementals, warlock demons, DK ghouls all
    # share this prefix — none of them count as a *party* death.
    "5/11/2026 12:00:17.000  UNIT_DIED,0x0,Boss,0x1,0x0,"
    "Pet-0-4244-2915-97430-165189-0200008889FF,Tankpet,0x1112,0x0,0\n"
)
# A truncated UNIT_DIED line — only the source half is present, destGUID
# absent. parse_combat_log_line may still return a result with fields < 6.
# The guard at combat_log.py:448 (`len(fields) >= 6`) must keep us from
# IndexError-crashing on these.
LINE_TRUNCATED_UNIT_DIED = "5/11/2026 12:00:18.000  UNIT_DIED,0x0,Boss,0x1\n"
LINE_DAMAGE = (
    "5/11/2026 12:00:05.000  SPELL_DAMAGE,0x0,Boss,0x1,0x0,"
    "Player-1234,Brutoh-Uldum-EU,0x512,0x0,123,Fireball,0x4,"
    "0,0,1000,1000,0,0,100,0,0,nil,nil\n"
)


def test_counts_single_player_death(tmp_path):
    log = tmp_path / "test.log"
    log.write_text(LINE_PLAYER_DIED_A)
    assert count_party_deaths_in_run(log) == 1


def test_counts_multiple_player_deaths(tmp_path):
    log = tmp_path / "test.log"
    log.write_text(LINE_PLAYER_DIED_A + LINE_PLAYER_DIED_B)
    assert count_party_deaths_in_run(log) == 2


def test_ignores_npc_deaths(tmp_path):
    """The Creature-* prefix on the destGUID must not count. M+ trash
    deaths spam UNIT_DIED — without this filter the badge would read
    "200 deaths" on every successful run."""
    log = tmp_path / "test.log"
    log.write_text(LINE_NPC_DIED + LINE_NPC_DIED + LINE_NPC_DIED)
    assert count_party_deaths_in_run(log) == 0


def test_mixed_player_and_npc_deaths_only_count_players(tmp_path):
    log = tmp_path / "test.log"
    log.write_text(LINE_NPC_DIED + LINE_PLAYER_DIED_A + LINE_NPC_DIED)
    assert count_party_deaths_in_run(log) == 1


def test_ignores_non_unit_died_lines(tmp_path):
    """A SPELL_DAMAGE line containing "UNIT_DIED" by accident (e.g. in
    the spell name) must be parse-filtered out. The fast-path
    substring check is a perf optimisation, not a correctness guard."""
    log = tmp_path / "test.log"
    log.write_text(LINE_DAMAGE + LINE_PLAYER_DIED_A)
    assert count_party_deaths_in_run(log) == 1


def test_empty_log(tmp_path):
    log = tmp_path / "test.log"
    log.write_text("")
    assert count_party_deaths_in_run(log) == 0


def test_window_bounds_exclude_earlier_deaths(tmp_path):
    """Death timestamped before `start_time_s` must not count.
    LINE_PLAYER_DIED_A is at 12:00:10; pick a window starting at 12:00:15."""
    log = tmp_path / "test.log"
    log.write_text(LINE_PLAYER_DIED_A + LINE_PLAYER_DIED_B)
    # LINE_PLAYER_DIED_B is at 12:00:20; start = 12:00:15 must drop A, keep B.
    # The combat_log parser converts the line timestamp to a local-tz epoch
    # — we don't need exact arithmetic, just relative ordering. Read the
    # two timestamps back via iter_death_events to pin the boundary.
    from simf.io.combat_log import iter_death_events

    deaths = list(iter_death_events(log, "Brutoh-Uldum-EU")) + list(
        iter_death_events(log, "Anothertank-Uldum-EU")
    )
    assert len(deaths) == 2
    deaths.sort(key=lambda d: d.time_s)
    t_a, t_b = deaths[0].time_s, deaths[1].time_s
    mid = (t_a + t_b) / 2
    assert count_party_deaths_in_run(log, start_time_s=mid) == 1


def test_window_bounds_exclude_later_deaths(tmp_path):
    """Death timestamped after `end_time_s` must not count."""
    from simf.io.combat_log import iter_death_events

    log = tmp_path / "test.log"
    log.write_text(LINE_PLAYER_DIED_A + LINE_PLAYER_DIED_B)
    deaths = list(iter_death_events(log, "Brutoh-Uldum-EU")) + list(
        iter_death_events(log, "Anothertank-Uldum-EU")
    )
    deaths.sort(key=lambda d: d.time_s)
    t_a, t_b = deaths[0].time_s, deaths[1].time_s
    mid = (t_a + t_b) / 2
    assert count_party_deaths_in_run(log, end_time_s=mid) == 1


def test_byte_offset_skips_leading_bytes(tmp_path):
    """`start_byte_offset` is the same perf optimisation `detect_party_roles`
    uses for deep runs inside a long session log. Skipping past
    LINE_PLAYER_DIED_A's offset means we only see LINE_PLAYER_DIED_B."""
    log = tmp_path / "test.log"
    content = LINE_PLAYER_DIED_A + LINE_PLAYER_DIED_B
    log.write_text(content)
    # Byte offset = length of the first line, so the scan starts at
    # the beginning of LINE_PLAYER_DIED_B.
    offset = len(LINE_PLAYER_DIED_A.encode("utf-8"))
    assert count_party_deaths_in_run(log, start_byte_offset=offset) == 1


def test_pet_deaths_excluded(tmp_path):
    """A Hunter pet (or DK ghoul, warlock demon, etc.) carries a ``Pet-``
    GUID prefix. M+ pet deaths are extremely common — they must NOT
    count toward the "did the party wipe" badge. The existing
    ``startswith('Player-')`` filter already covers this, so this test
    pins that behavior against a future refactor that broadens the
    filter to e.g. ``not GUID.startswith('Creature-')`` — which would
    silently start counting pets."""
    log = tmp_path / "test.log"
    log.write_text(LINE_PET_DIED + LINE_PET_DIED + LINE_PLAYER_DIED_A)
    # Two pet deaths + one player death → only the player counts.
    assert count_party_deaths_in_run(log) == 1


def test_pet_only_log_has_zero_party_deaths(tmp_path):
    """A run where only pets died (a common boss enrage pattern) must
    register zero party deaths, not three."""
    log = tmp_path / "test.log"
    log.write_text(LINE_PET_DIED + LINE_PET_DIED + LINE_PET_DIED)
    assert count_party_deaths_in_run(log) == 0


def test_truncated_unit_died_line_does_not_crash(tmp_path):
    """``parse_combat_log_line`` is permissive — a malformed UNIT_DIED
    row (e.g., a log that was truncated by a hard process kill) may
    return a parsed tuple with ``fields`` shorter than the 6 the
    UNIT_DIED layout expects. The ``len(fields) >= 6`` guard at
    ``combat_log.py:448`` must keep that from IndexError-ing.

    Truncated-tail log files are a real shape — Brutoh's older
    logs occasionally include a half-written final line when WoW
    crashed mid-write."""
    log = tmp_path / "test.log"
    log.write_text(LINE_TRUNCATED_UNIT_DIED + LINE_PLAYER_DIED_A)
    # Should return 1 (only the valid Player death), not crash.
    assert count_party_deaths_in_run(log) == 1


def test_death_exactly_at_end_time_is_included(tmp_path):
    """Boundary semantics: the helper uses ``time_s > end_time_s`` to
    break, NOT ``>=``. That means a death timestamped exactly at the
    run's ``end_time_s`` (the CHALLENGE_MODE_END instant — the boss /
    final-trash UNIT_DIED that fires inside the same tick as the run
    end) IS counted as a party death.

    This is the right semantics for the picker badge: the final-room
    wipe that ended the run should show up, not silently fall off the
    edge because the death event happened to share a timestamp with
    CHALLENGE_MODE_END. Pin it so a refactor to ``>=`` (which would
    drop those deaths) fails loudly here."""
    from simf.io.combat_log import iter_death_events

    log = tmp_path / "test.log"
    log.write_text(LINE_PLAYER_DIED_A)
    deaths = list(iter_death_events(log, "Brutoh-Uldum-EU"))
    assert len(deaths) == 1
    t_death = deaths[0].time_s
    # end_time_s exactly equal to the death timestamp: death is included.
    assert count_party_deaths_in_run(log, end_time_s=t_death) == 1


def test_death_exactly_at_start_time_is_included(tmp_path):
    """Mirror of the end-time boundary: ``time_s < start_time_s`` skips,
    so a death AT ``start_time_s`` IS counted. The CHALLENGE_MODE_START
    instant + a UNIT_DIED firing in the same tick (rare but possible
    when an early-pull mob dies on the start frame) should not silently
    fall off the leading edge of the window."""
    from simf.io.combat_log import iter_death_events

    log = tmp_path / "test.log"
    log.write_text(LINE_PLAYER_DIED_A)
    deaths = list(iter_death_events(log, "Brutoh-Uldum-EU"))
    assert len(deaths) == 1
    t_death = deaths[0].time_s
    # start_time_s exactly equal to the death timestamp: death is included.
    assert count_party_deaths_in_run(log, start_time_s=t_death) == 1
