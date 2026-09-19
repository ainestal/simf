"""Tests for ``_segment_top_abilities`` row shape — pin the Wowhead-link
fields surfaced 2026-05-21 per Brutoh user-feedback ("there should be
links to the abilities and the mobs" in the damage-source list).

The renderer in ``ui/log_view.py`` builds Wowhead anchors from
``Spell ID`` (`wowhead_spell_url`) and ``Source NPC ID``
(`wowhead_npc_url`). Both must be carried through from the underlying
combat-log events; tests guard against future refactors silently
dropping them.
"""

from __future__ import annotations

from simf.io.combat_log import DamageTakenEvent
from simf.ui.log_view import _segment_top_abilities


def _evt(
    *,
    spell_name: str,
    spell_id: int | None,
    source_name: str,
    source_npc_id: int | None,
    amount: int = 1000,
    school: str = "physical",
) -> DamageTakenEvent:
    return DamageTakenEvent(
        time_s=0.0,
        event_type="SPELL_DAMAGE",
        source_name=source_name,
        spell_name=spell_name,
        school=school,
        amount=amount,
        base_amount=amount,
        overkill=0,
        blocked=0,
        absorbed=0,
        resisted=0,
        is_critical=False,
        is_glancing=False,
        source_npc_id=source_npc_id,
        spell_id=spell_id,
    )


def test_segment_top_abilities_carries_spell_and_source_ids() -> None:
    """Each row exposes Spell ID + Source NPC ID + Source name — the
    fields ``_render_damage_segments`` reads to build Wowhead links."""
    events = [
        _evt(
            spell_name="Spellbound Weapon",
            spell_id=465217,
            source_name="Champion of Anub'arak",
            source_npc_id=210068,
        ),
        _evt(
            spell_name="Spellbound Weapon",
            spell_id=465217,
            source_name="Champion of Anub'arak",
            source_npc_id=210068,
        ),
    ]
    rows = _segment_top_abilities(events, run_start_s=0.0, top_n=3)
    assert rows[0]["Ability"] == "Spellbound Weapon"
    assert rows[0]["Spell ID"] == 465217
    assert rows[0]["Source"] == "Champion of Anub'arak"
    assert rows[0]["Source NPC ID"] == 210068


def test_segment_top_abilities_handles_auto_attack_without_spell_id() -> None:
    """SWING auto-attacks have no spell_id — the row must still render,
    and the renderer's `_link_md` falls back to plain text."""
    events = [
        _evt(
            spell_name="auto-attack",
            spell_id=None,
            source_name="Champion of Anub'arak",
            source_npc_id=210068,
        ),
    ]
    rows = _segment_top_abilities(events, run_start_s=0.0, top_n=3)
    assert rows[0]["Ability"] == "auto-attack"
    assert rows[0]["Spell ID"] is None
    # Source link still works for an auto-attack.
    assert rows[0]["Source NPC ID"] == 210068


def test_segment_top_abilities_picks_dominant_source_for_ability() -> None:
    """When the same ability comes from multiple mobs, the dominant
    (by damage) source is the one we link — single link beats a list."""
    events = [
        _evt(
            spell_name="Cleave",
            spell_id=11111,
            source_name="Goblin",
            source_npc_id=999,
            amount=100,
        ),
        _evt(
            spell_name="Cleave",
            spell_id=11111,
            source_name="Boss",
            source_npc_id=42,
            amount=5000,
        ),
    ]
    rows = _segment_top_abilities(events, run_start_s=0.0, top_n=3)
    assert rows[0]["Source"] == "Boss"
    assert rows[0]["Source NPC ID"] == 42
