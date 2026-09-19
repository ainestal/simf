"""Tests for Phase A NPC-ID extraction in combat log parser."""

from simf.io.combat_log import npc_id_from_guid


def test_npc_id_from_creature_guid():
    assert npc_id_from_guid("Creature-0-4244-2915-97430-248373-00008889FF") == 248373


def test_npc_id_from_vehicle_guid():
    assert npc_id_from_guid("Vehicle-0-4244-2915-97430-241643-00040889FF") == 241643


def test_npc_id_from_player_guid_returns_none():
    assert npc_id_from_guid("Player-1379-AAAA0001") is None


def test_npc_id_from_pet_guid_returns_none():
    # Pets have a different ID space (pet ID, not NPC ID); skip them.
    assert npc_id_from_guid("Pet-0-4244-2915-97430-417-01032CB41C") is None


def test_npc_id_from_empty_or_garbage():
    assert npc_id_from_guid("") is None
    assert npc_id_from_guid("not-a-guid") is None
    assert npc_id_from_guid("Creature-0-4244-2915-97430-NOTANINT-00008889FF") is None
