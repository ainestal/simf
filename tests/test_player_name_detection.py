"""Regression tests for player-name detection in combat logs.

The Why-did-I-die surface auto-detects who took damage in the picked log,
ranks by hit count, and pre-selects the top entry (the tank, in M+) for
analysis. Without this:
  - A user who types `Brutoh` instead of `Brutoh-Uldum-EU` gets zero events
    silently — the parser uses strict equality on `Name-Server-Region`.
  - An empty character-name field analyses a 13k-event log as 0 events with
    no diagnostic, confusing power users who *know* damage happened.

These tests pin:
  - the detection function ranks by hit count (tank first)
  - non-player GUIDs (creatures, pets, totems) don't pollute the list
  - the function bails before reading the whole file on a big log
  - role detection (tank/healer/DPS) classifies by behaviour, not spec table
"""

from __future__ import annotations

from simf.io.combat_log import detect_destination_player_names, detect_party_roles


def _damage_line(ts: str, dest_guid: str, dest_name: str, amount: int = 1000) -> str:
    """One synthetic SPELL_DAMAGE line in the WoW 12.0.5 format."""
    return (
        f"{ts}  SPELL_DAMAGE,0x0,Boss,0x1,0x0,"
        f"{dest_guid},{dest_name},0x512,0x0,"
        f"123,Fireball,0x4,0,0,{amount},{amount},0,0,100,0,0,nil,nil\n"
    )


def _heal_line(ts: str, src_guid: str, src_name: str, amount: int = 1000) -> str:
    """One synthetic SPELL_HEAL line in the WoW 12.0.5 format.

    For role detection only the source identity matters; the dest fields
    can carry the same player (self-heal) without affecting the count.
    """
    return (
        f"{ts}  SPELL_HEAL,{src_guid},{src_name},0x511,0x0,"
        f"{src_guid},{src_name},0x511,0x0,"
        f"456,Holy Light,0x2,0,0,{amount},0,nil,nil,nil,nil,nil\n"
    )


def test_detect_ranks_tank_first(tmp_path):
    """Five-player M+ party — the tank takes the most hits, lands at top."""
    log = tmp_path / "test.log"
    lines = []
    # Tank: 20 hits. Healer + 3 DPS: a handful each.
    for i in range(20):
        lines.append(
            _damage_line(f"5/11/2026 12:00:{i:02d}.000", "Player-1-AAA", "Brutoh-Uldum-EU")
        )
    for i in range(5):
        lines.append(
            _damage_line(f"5/11/2026 12:01:{i:02d}.000", "Player-1-BBB", "Healer-Uldum-EU")
        )
    for i in range(3):
        lines.append(_damage_line(f"5/11/2026 12:02:{i:02d}.000", "Player-1-CCC", "Dps1-Uldum-EU"))
    for i in range(2):
        lines.append(_damage_line(f"5/11/2026 12:03:{i:02d}.000", "Player-1-DDD", "Dps2-Uldum-EU"))
    for i in range(1):
        lines.append(_damage_line(f"5/11/2026 12:04:{i:02d}.000", "Player-1-EEE", "Dps3-Uldum-EU"))
    log.write_text("".join(lines))

    detected = detect_destination_player_names(log)

    assert detected[0] == ("Brutoh-Uldum-EU", 20)
    names = [n for n, _ in detected]
    assert names == [
        "Brutoh-Uldum-EU",
        "Healer-Uldum-EU",
        "Dps1-Uldum-EU",
        "Dps2-Uldum-EU",
        "Dps3-Uldum-EU",
    ]


def test_detect_filters_non_player_guids(tmp_path):
    """Pets, creatures, totems should not appear in the detected list."""
    log = tmp_path / "test.log"
    lines = [
        _damage_line("5/11/2026 12:00:00.000", "Player-1-AAA", "Brutoh-Uldum-EU"),
        _damage_line("5/11/2026 12:00:01.000", "Creature-0-1-2-3-99-AB", "Boss-Add"),
        _damage_line("5/11/2026 12:00:02.000", "Pet-0-1-2-3-99-AB", "Felguard"),
        # Pet with player-like name format but Pet- GUID — still filtered.
        _damage_line("5/11/2026 12:00:03.000", "Pet-0-1-2-3-99-AB", "Mage-Faketown-EU"),
    ]
    log.write_text("".join(lines))

    detected = detect_destination_player_names(log)

    names = [n for n, _ in detected]
    assert names == ["Brutoh-Uldum-EU"]


def test_detect_skips_names_without_realm_suffix(tmp_path):
    """Combat logs use Name-Server-Region; bare names indicate corrupt rows."""
    log = tmp_path / "test.log"
    lines = [
        _damage_line("5/11/2026 12:00:00.000", "Player-1-AAA", "Brutoh-Uldum-EU"),
        # Bare name with no hyphen — invalid for a real player record.
        _damage_line("5/11/2026 12:00:01.000", "Player-1-BBB", "Anon"),
    ]
    log.write_text("".join(lines))

    detected = detect_destination_player_names(log)

    names = [n for n, _ in detected]
    assert names == ["Brutoh-Uldum-EU"]


def test_detect_respects_max_bytes_budget(tmp_path):
    """A multi-MB log must not be fully scanned — the byte cap fires first.

    Tank takes 20 hits in the first 1 KB; the rest of the file is filler.
    With max_bytes=2048 the function should stop early and never see the
    filler events at the tail.
    """
    log = tmp_path / "big.log"
    head = []
    for i in range(20):
        head.append(_damage_line(f"5/11/2026 12:00:{i:02d}.000", "Player-1-AAA", "Tank-Uldum-EU"))
    # Padding well past the 2 KB budget — these events should never be read.
    filler = []
    for i in range(50_000):
        filler.append(
            _damage_line(f"5/11/2026 12:30:{i:02d}.000", "Player-1-ZZZ", "Latecomer-Uldum-EU")
        )
    log.write_text("".join(head) + "".join(filler))

    detected = detect_destination_player_names(log, max_bytes=2048)

    names = [n for n, _ in detected]
    assert "Tank-Uldum-EU" in names
    assert "Latecomer-Uldum-EU" not in names, (
        "max_bytes budget was ignored — function read past the cap"
    )


def test_detect_early_exits_on_full_party(tmp_path):
    """Once 5 distinct Player- destinations have >10 hits each, stop scanning."""
    log = tmp_path / "test.log"
    lines = []
    # Five players, 11 hits each → 55 events total, well-clustered at the head.
    for player_idx in range(5):
        for hit in range(11):
            ts = f"5/11/2026 12:0{player_idx}:{hit:02d}.000"
            lines.append(
                _damage_line(ts, f"Player-1-P{player_idx}", f"Player{player_idx}-Uldum-EU")
            )
    # If the early-exit doesn't fire, this 6th player would land in the result.
    for i in range(20):
        lines.append(
            _damage_line(f"5/11/2026 13:00:{i:02d}.000", "Player-1-LATE", "Latecomer-Uldum-EU")
        )
    log.write_text("".join(lines))

    detected = detect_destination_player_names(log, early_exit_count=5)

    names = [n for n, _ in detected]
    assert len(names) == 5
    assert "Latecomer-Uldum-EU" not in names


def test_detect_returns_empty_on_no_combat(tmp_path):
    """A log with no damage events yields an empty list, not a crash."""
    log = tmp_path / "empty.log"
    log.write_text(
        '5/11/2026 12:00:00.000  ZONE_CHANGE,2915,"UNKNOWN AREA",8\n'
        "5/11/2026 12:00:01.000  COMBATANT_INFO,Player-1-AAA,0,0,0,0,0\n"
    )

    detected = detect_destination_player_names(log)

    assert detected == []


def test_parse_challenge_modes_records_start_byte_offset(tmp_path):
    """The byte offset of each CHALLENGE_MODE_START line is captured so
    downstream scans can seek there directly. Critical for big multi-run
    logs where Brutoh's run starts 167 MB into a 239 MB file — without the
    seek, name detection would pay 13s of sequential read just to reach it.
    """
    from simf.io.combat_log import parse_challenge_modes

    log = tmp_path / "test.log"
    # Pad with content so the second run's start offset is meaningfully > 0.
    padding = (
        "5/11/2026 12:00:00.000  SPELL_AURA_APPLIED,0x0,X,0x0,0x0,0x0,Y,0x0,0x0,1,Test,0x1,BUFF\n"
        * 50
    )
    log.write_text(
        padding
        + '5/11/2026 12:30:00.000  CHALLENGE_MODE_START,"Test Dungeon A",1,1,10,[]\n'
        + padding
        + "5/11/2026 13:00:00.000  CHALLENGE_MODE_END,1,1,1,1800000,1,10,0,0,0\n"
        + padding
        + '5/11/2026 13:30:00.000  CHALLENGE_MODE_START,"Test Dungeon B",2,2,14,[]\n'
        + padding
        + "5/11/2026 14:00:00.000  CHALLENGE_MODE_END,2,1,1,1800000,1,14,0,0,0\n"
    )

    runs = parse_challenge_modes(log)

    assert len(runs) == 2
    assert runs[0].start_byte_offset > 0
    assert runs[1].start_byte_offset > runs[0].start_byte_offset
    # Verify the offsets actually land on the CHALLENGE_MODE_START lines.
    with log.open("rb") as f:
        f.seek(runs[0].start_byte_offset)
        line0 = f.readline().decode()
        f.seek(runs[1].start_byte_offset)
        line1 = f.readline().decode()
    assert "CHALLENGE_MODE_START" in line0
    assert "Test Dungeon A" in line0
    assert "CHALLENGE_MODE_START" in line1
    assert "Test Dungeon B" in line1


def test_parse_challenge_modes_keeps_all_affixes(tmp_path):
    """The bracketed affix array (e.g. ``[9,10,147]``) contains commas that the
    CSV reader splits across fields[4:]. Regression: the parser used to read only
    fields[4] and keep the first affix ("[9" → [9]); it must capture all of them.
    """
    from simf.io.combat_log import parse_challenge_modes

    log = tmp_path / "affixes.log"
    log.write_text(
        '6/29/2026 13:44:26.295  CHALLENGE_MODE_START,"Pit of Saron",658,556,17,[9,10,147]\n'
        "6/29/2026 14:00:00.000  CHALLENGE_MODE_END,658,1,17,1800000,1,17,0,0,0\n"
        '6/29/2026 14:08:02.453  CHALLENGE_MODE_START,"Algeth\'ar Academy",2526,402,16,[9]\n'
        "6/29/2026 14:30:00.000  CHALLENGE_MODE_END,2526,1,16,1800000,1,16,0,0,0\n"
        '6/29/2026 14:40:00.000  CHALLENGE_MODE_START,"Empty Affix Dungeon",3,3,12,[]\n'
        "6/29/2026 15:00:00.000  CHALLENGE_MODE_END,3,1,12,1800000,1,12,0,0,0\n"
    )

    runs = parse_challenge_modes(log)

    assert len(runs) == 3
    assert runs[0].affixes == [9, 10, 147]  # was [9] before the fix
    assert runs[1].affixes == [9]
    assert runs[2].affixes == []


def test_detect_with_byte_offset_skips_earlier_content(tmp_path):
    """Passing a byte offset skips bytes before it — the events at the head
    of the file should never appear in the detected list."""
    log = tmp_path / "test.log"
    head_events = []
    for i in range(20):
        head_events.append(
            _damage_line(f"5/11/2026 12:00:{i:02d}.000", "Player-1-EARLY", "EarlyTank-Uldum-EU")
        )
    head_text = "".join(head_events)
    head_bytes = len(head_text)
    tail_events = []
    for i in range(20):
        tail_events.append(
            _damage_line(f"5/11/2026 13:00:{i:02d}.000", "Player-1-LATE", "LateTank-Uldum-EU")
        )
    log.write_text(head_text + "".join(tail_events))

    detected = detect_destination_player_names(log, start_byte_offset=head_bytes)

    names = [n for n, _ in detected]
    assert names == ["LateTank-Uldum-EU"], f"start_byte_offset should skip head events; got {names}"


# ── role detection ────────────────────────────────────────────────────────────


def test_detect_party_roles_classifies_tank_healer_dps(tmp_path):
    """5-player party — tank by damage taken, healer by heals cast, rest DPS."""
    log = tmp_path / "test.log"
    lines = []
    # Tank: 50 hits taken, a handful of self-heals (Word of Glory etc.).
    for i in range(50):
        lines.append(_damage_line(f"5/11/2026 12:00:{i:02d}.000", "Player-1-T", "Tankman-Uldum-EU"))
    for i in range(5):
        lines.append(_heal_line(f"5/11/2026 12:01:{i:02d}.000", "Player-1-T", "Tankman-Uldum-EU"))
    # Healer: few hits taken, hundreds of heals.
    for i in range(10):
        lines.append(
            _damage_line(f"5/11/2026 12:02:{i:02d}.000", "Player-1-H", "Healwoman-Uldum-EU")
        )
    for i in range(300):
        # synthesize unique timestamps to avoid collisions
        lines.append(
            _heal_line(
                f"5/11/2026 12:0{(i % 5) + 3}:{i % 60:02d}.000", "Player-1-H", "Healwoman-Uldum-EU"
            )
        )
    # DPS: moderate hits taken, occasional self-heals only.
    for label_i, guid in [(1, "Player-1-D1"), (2, "Player-1-D2"), (3, "Player-1-D3")]:
        name = f"Dps{label_i}-Uldum-EU"
        for i in range(20):
            lines.append(_damage_line(f"5/11/2026 12:1{label_i}:{i % 60:02d}.000", guid, name))
        for i in range(2):
            lines.append(_heal_line(f"5/11/2026 12:2{label_i}:{i:02d}.000", guid, name))
    log.write_text("".join(lines))

    members = detect_party_roles(log)

    by_role = {m.role: m for m in members}
    assert by_role["tank"].name == "Tankman-Uldum-EU"
    assert by_role["healer"].name == "Healwoman-Uldum-EU"
    # DPS appear after tank + healer.
    dps_names = sorted(m.name for m in members if m.role == "dps")
    assert dps_names == ["Dps1-Uldum-EU", "Dps2-Uldum-EU", "Dps3-Uldum-EU"]
    # Ordering: tank first, healer second, then DPS by damage taken.
    assert members[0].role == "tank"
    assert members[1].role == "healer"
    assert all(m.role == "dps" for m in members[2:])


def test_detect_party_roles_no_heals_means_no_healer(tmp_path):
    """A trash-only window with no heals cast → all non-tanks classified DPS."""
    log = tmp_path / "test.log"
    lines = []
    for i in range(30):
        lines.append(_damage_line(f"5/11/2026 12:00:{i:02d}.000", "Player-1-T", "Tankman-Uldum-EU"))
    for i in range(5):
        lines.append(_damage_line(f"5/11/2026 12:01:{i:02d}.000", "Player-1-D", "Dpsman-Uldum-EU"))
    log.write_text("".join(lines))

    members = detect_party_roles(log)

    assert members[0].role == "tank"
    assert all(m.role != "healer" for m in members)


def test_detect_party_roles_handles_byte_offset_seek(tmp_path):
    """Like the dest-name test but for the unified role-detection scan."""
    log = tmp_path / "test.log"
    head = []
    for i in range(20):
        head.append(
            _damage_line(f"5/11/2026 12:00:{i:02d}.000", "Player-1-OLD", "OldTank-Uldum-EU")
        )
    head_text = "".join(head)
    head_bytes = len(head_text)
    tail = []
    for i in range(30):
        tail.append(
            _damage_line(f"5/11/2026 13:00:{i:02d}.000", "Player-1-NEW", "NewTank-Uldum-EU")
        )
    for i in range(20):
        tail.append(_heal_line(f"5/11/2026 13:01:{i:02d}.000", "Player-1-NEW", "NewTank-Uldum-EU"))
    log.write_text(head_text + "".join(tail))

    members = detect_party_roles(log, start_byte_offset=head_bytes)

    names = [m.name for m in members]
    assert "OldTank-Uldum-EU" not in names
    assert "NewTank-Uldum-EU" in names


# ── COMBATANT_INFO-driven role detection ──────────────────────────────────────


def _combatant_info_line(ts: str, guid: str, spec_id: int) -> str:
    """Synthetic COMBATANT_INFO. We only care about fields[1] (GUID) and
    fields[25] (spec_id); other fields are placeholder zeros and a stub
    talent block to mimic the bracketed-list format the parser must skip."""
    leading_fields = ["COMBATANT_INFO", guid] + ["0"] * 23 + [str(spec_id)]
    return f"{ts}  {','.join(leading_fields)},[(1,2,3),(4,5,6)],[],[]\n"


def test_detect_party_roles_uses_combatant_info_when_present(tmp_path):
    """COMBATANT_INFO.spec_id is authoritative — overrides damage heuristics."""
    log = tmp_path / "test.log"
    lines = []
    # Player who takes a few hits but is the Holy Priest (spec 257 = healer).
    lines.append(_combatant_info_line("5/11/2026 12:00:00.000", "Player-1-H", 257))
    # Player who takes the most hits but is Protection Warrior (spec 73 = tank).
    lines.append(_combatant_info_line("5/11/2026 12:00:00.001", "Player-1-T", 73))
    # Player who's Havoc DH (spec 577 = dps).
    lines.append(_combatant_info_line("5/11/2026 12:00:00.002", "Player-1-D", 577))
    # Now damage events — tank takes the most.
    for i in range(40):
        lines.append(
            _damage_line(f"5/11/2026 12:01:{i % 60:02d}.000", "Player-1-T", "Tankman-Uldum-EU")
        )
    for i in range(10):
        lines.append(
            _damage_line(f"5/11/2026 12:02:{i % 60:02d}.000", "Player-1-H", "Healwoman-Uldum-EU")
        )
    for i in range(15):
        lines.append(
            _damage_line(f"5/11/2026 12:03:{i % 60:02d}.000", "Player-1-D", "Dpsguy-Uldum-EU")
        )
    log.write_text("".join(lines))

    members = detect_party_roles(log)

    by_role = {m.role: m for m in members}
    assert by_role["tank"].name == "Tankman-Uldum-EU"
    assert by_role["tank"].class_spec == "protection_warrior"
    assert by_role["tank"].role_source == "combatant_info"
    assert by_role["healer"].name == "Healwoman-Uldum-EU"
    assert by_role["healer"].class_spec == "holy_priest"
    assert by_role["healer"].role_source == "combatant_info"
    dps = [m for m in members if m.role == "dps"]
    assert dps[0].name == "Dpsguy-Uldum-EU"
    assert dps[0].class_spec == "havoc_demon_hunter"


def test_detect_party_roles_falls_back_to_behavioural_when_no_combatant_info(tmp_path):
    """ACL-off logs emit zero COMBATANT_INFO; behavioural still classifies."""
    log = tmp_path / "test.log"
    lines = []
    for i in range(50):
        lines.append(
            _damage_line(f"5/11/2026 12:00:{i % 60:02d}.000", "Player-1-T", "Tankman-Uldum-EU")
        )
    for i in range(200):
        lines.append(
            _heal_line(
                f"5/11/2026 12:0{(i % 5) + 1}:{i % 60:02d}.000", "Player-1-H", "Healwoman-Uldum-EU"
            )
        )
    log.write_text("".join(lines))

    members = detect_party_roles(log)

    by_role = {m.role: m for m in members}
    assert by_role["tank"].name == "Tankman-Uldum-EU"
    assert by_role["tank"].class_spec is None
    assert by_role["tank"].role_source == "behavioural"
    assert by_role["healer"].name == "Healwoman-Uldum-EU"
    assert by_role["healer"].role_source == "behavioural"


def test_detect_party_roles_mixed_combatant_info_and_behavioural(tmp_path):
    """Half the party has COMBATANT_INFO, half doesn't — both sources used."""
    log = tmp_path / "test.log"
    lines = []
    # Only the tank announces themselves via COMBATANT_INFO.
    lines.append(_combatant_info_line("5/11/2026 12:00:00.000", "Player-1-T", 73))
    # Tank gets hit a lot; healer logs many heals; DPS some damage.
    for i in range(30):
        lines.append(
            _damage_line(f"5/11/2026 12:01:{i % 60:02d}.000", "Player-1-T", "Tankman-Uldum-EU")
        )
    for i in range(100):
        lines.append(
            _heal_line(
                f"5/11/2026 12:0{(i % 5) + 2}:{i % 60:02d}.000", "Player-1-H", "Healwoman-Uldum-EU"
            )
        )
    for i in range(10):
        lines.append(
            _damage_line(f"5/11/2026 12:08:{i % 60:02d}.000", "Player-1-D", "Dpsguy-Uldum-EU")
        )
    log.write_text("".join(lines))

    members = detect_party_roles(log)

    by_name = {m.name: m for m in members}
    assert by_name["Tankman-Uldum-EU"].role == "tank"
    assert by_name["Tankman-Uldum-EU"].role_source == "combatant_info"
    assert by_name["Tankman-Uldum-EU"].class_spec == "protection_warrior"
    # Healer + DPS have no spec entry → behavioural fallback.
    assert by_name["Healwoman-Uldum-EU"].role == "healer"
    assert by_name["Healwoman-Uldum-EU"].role_source == "behavioural"
    assert by_name["Healwoman-Uldum-EU"].class_spec is None
    assert by_name["Dpsguy-Uldum-EU"].role == "dps"
    assert by_name["Dpsguy-Uldum-EU"].role_source == "behavioural"


def test_iter_combatant_info_extracts_guid_and_spec_id(tmp_path):
    """The COMBATANT_INFO parser must isolate guid + spec_id + talent_spell_ids
    without tripping over the bracketed talent block — which contains commas
    that a naive csv split mis-parses. The synthetic block [(1,2,3),(4,5,6)]
    should yield talent_spell_ids={2, 5} (middle value of each tuple)."""
    from simf.io.combat_log import iter_combatant_info

    log = tmp_path / "test.log"
    log.write_text(
        _combatant_info_line("5/11/2026 12:00:00.000", "Player-1-AAA", 73)
        + _combatant_info_line("5/11/2026 12:00:00.001", "Player-1-BBB", 581)
    )

    events = list(iter_combatant_info(log))

    assert len(events) == 2
    guid0, spec0, talents0 = events[0]
    guid1, spec1, talents1 = events[1]
    assert (guid0, spec0) == ("Player-1-AAA", 73)
    assert (guid1, spec1) == ("Player-1-BBB", 581)
    # Synthetic talent block [(1,2,3),(4,5,6)] → spell IDs {2, 5}
    assert talents0 == frozenset({2, 5})
    assert talents1 == frozenset({2, 5})


def test_spec_to_role_known_specs():
    from simf.io.spec_ids import spec_to_role

    assert spec_to_role(73) == "tank"  # Protection Warrior
    assert spec_to_role(581) == "tank"  # Vengeance DH
    assert spec_to_role(105) == "healer"  # Restoration Druid
    assert spec_to_role(1468) == "healer"  # Preservation Evoker
    assert spec_to_role(258) == "dps"  # Shadow Priest
    assert spec_to_role(99999) == "dps"  # unknown → safe default


def _unit_died_line(ts: str, dest_guid: str, dest_name: str) -> str:
    """One synthetic UNIT_DIED line in the WoW 12.0.5 format."""
    return (
        f"{ts}  UNIT_DIED,0000000000000000,nil,0x80000000,0x80000000,"
        f"{dest_guid},{dest_name},0x511,0x0,0\n"
    )


def test_detect_party_roles_counts_deaths_per_player(tmp_path):
    """Deaths is the headline signal on the Why-died picker — UNIT_DIED
    on each player is tallied alongside damage/heal counts. NPC deaths
    are ignored. The death tally must survive the role-classification
    early-exit so late-run deaths are still counted."""
    log = tmp_path / "test.log"
    lines = []
    # Tank: 50 hits taken + 2 deaths (one late in the run, past the
    # early-exit point for role classification).
    for i in range(50):
        lines.append(_damage_line(f"5/11/2026 12:00:{i:02d}.000", "Player-1-T", "Tankman-Uldum-EU"))
    lines.append(_unit_died_line("5/11/2026 12:00:30.000", "Player-1-T", "Tankman-Uldum-EU"))
    # Healer with enough heals to satisfy early-exit threshold.
    for i in range(50):
        lines.append(
            _heal_line(f"5/11/2026 12:01:{i % 60:02d}.000", "Player-1-H", "Healwoman-Uldum-EU")
        )
    # DPS so 5-player set passes early-exit.
    for label_i, guid in [(1, "Player-1-D1"), (2, "Player-1-D2"), (3, "Player-1-D3")]:
        name = f"Dps{label_i}-Uldum-EU"
        for i in range(20):
            lines.append(_damage_line(f"5/11/2026 12:1{label_i}:{i % 60:02d}.000", guid, name))
    # A DPS death AFTER the early-exit fires (role classification has
    # already converged; only UNIT_DIED scanning continues).
    lines.append(_unit_died_line("5/11/2026 12:15:00.000", "Player-1-D1", "Dps1-Uldum-EU"))
    # The tank's second death also fires late.
    lines.append(_unit_died_line("5/11/2026 12:16:00.000", "Player-1-T", "Tankman-Uldum-EU"))
    # NPC death should NOT be counted.
    lines.append(_unit_died_line("5/11/2026 12:17:00.000", "Creature-0-1-2-3-99-AB", "Boss-Add"))
    log.write_text("".join(lines))

    members = detect_party_roles(log)
    by_name = {m.name: m for m in members}

    assert by_name["Tankman-Uldum-EU"].deaths == 2, (
        "Tank died twice — second death was after the early-exit and must still be counted."
    )
    assert by_name["Dps1-Uldum-EU"].deaths == 1, "DPS death after early-exit must be counted."
    assert by_name["Healwoman-Uldum-EU"].deaths == 0
    assert by_name["Dps2-Uldum-EU"].deaths == 0
    # NPC deaths must not pollute any player's count.
    assert all(m.name != "Boss-Add" for m in members)
