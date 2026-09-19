"""Build a Character from a Warcraft Logs CombatantInfo event (gear-certain).

WCL's ``events(dataType: CombatantInfo)`` carries the same data as a local
COMBATANT_INFO line — exact stats, positional gear, talents — so this is the
**gear-and-stat-certain** calibration input: the gear actually worn in the
fight, not current armory gear. It converts the event into the shared
``CombatantInfoFull`` intermediate and feeds the one COMBATANT_INFO → Character
builder (``character_from_combatant_info.build_hydrate_result``), so a
WCL-sourced and a local-log-sourced character from identical data calibrate to
the **same** Character (no cross-source drift).

More correct than the local line-parser in one place: it reads the event's
**named** stat fields (``critMelee``/``hasteMelee``/``mastery``/
``versatilityDamageReduction``) instead of the positional crit read the local
parser mis-sources (see ``hydrate_not_calibration_grade``).

Gated on ACL: returns ``None`` when the report has no CombatantInfo events.
"""

from __future__ import annotations

from .character_from_combatant_info import build_hydrate_result
from .combat_log import _COMBATANT_INFO_SLOT_ORDER, CombatantInfoEquippedItem, CombatantInfoFull
from .spec_ids import TANK_SPEC_IDS

__all__ = ["character_from_wcl", "combatant_info_full_from_wcl_event"]


def _gear_from_event(event: dict) -> tuple[CombatantInfoEquippedItem, ...]:
    """WCL ``gear[]`` (positional, WoW equip order) → CombatantInfoEquippedItem
    tuple, preserving empty slots (id=0) so it mirrors the local parser exactly
    (``_equipped_from_combatant_info`` filters the zeros downstream)."""
    out: list[CombatantInfoEquippedItem] = []
    for i, g in enumerate(event.get("gear") or []):
        if i >= len(_COMBATANT_INFO_SLOT_ORDER):
            break
        if not isinstance(g, dict):
            continue
        item_id = g.get("id")
        ench = g.get("permanentEnchant")
        out.append(
            CombatantInfoEquippedItem(
                slot=_COMBATANT_INFO_SLOT_ORDER[i],
                item_id=int(item_id) if isinstance(item_id, int) else 0,
                ilvl=int(g.get("itemLevel") or 0),
                enchant_id=int(ench) if isinstance(ench, int) and ench else None,
                bonus_ids=tuple(int(b) for b in (g.get("bonusIDs") or []) if isinstance(b, int)),
                gem_ids=tuple(
                    int(gid)
                    for gem in (g.get("gems") or [])
                    if isinstance((gid := (gem or {}).get("id")), int)
                ),
            )
        )
    return tuple(out)


def combatant_info_full_from_wcl_event(event: dict) -> CombatantInfoFull:
    """Convert one WCL CombatantInfo event → ``CombatantInfoFull``."""
    tt = event.get("talentTree") or []
    talent_ids = frozenset(
        int(t["id"]) for t in tt if isinstance(t, dict) and isinstance(t.get("id"), int)
    )
    return CombatantInfoFull(
        guid=f"wcl-{event.get('sourceID')}",
        spec_id=int(event.get("specID") or 0),
        talent_spell_ids=talent_ids,
        strength=int(event.get("strength") or 0),
        agility=int(event.get("agility") or 0),
        stamina=int(event.get("stamina") or 0),
        intellect=int(event.get("intellect") or 0),
        dodge_rating=int(event.get("dodge") or 0),
        parry_rating=int(event.get("parry") or 0),
        crit_rating=int(event.get("critMelee") or 0),
        haste_rating=int(event.get("hasteMelee") or 0),
        mastery_rating=int(event.get("mastery") or 0),
        versatility_rating=int(event.get("versatilityDamageReduction") or 0),
        total_armor=int(event.get("armor") or 0),
        equipped=_gear_from_event(event),
    )


def character_from_wcl(
    report_code: str,
    fight_id: int,
    target_name: str,
    *,
    target_actor_id: int | None = None,
    region_hint: str = "",
    resolve_stats_fn=None,
    token: str | None = None,
    cache_dir=None,
):
    """Fetch a fight's CombatantInfo and build a ``HydrateResult`` for the tank.

    Picks the event by ``target_actor_id`` (the report's ``sourceID``) when
    provided, else falls back to the first event whose ``specID`` is a modeled
    tank. Returns ``None`` when the report has no CombatantInfo (ACL off) or no
    modeled-tank event is found.

    ``cache_dir``: optional opt-in on-disk cache (see ``wcl_api._gql``'s
    docstring) — omit for the production calibrate-k --wcl-url path.
    """
    from . import wcl_api

    tok = token or wcl_api._get_token()
    events = wcl_api.fetch_combatant_info_events(report_code, fight_id, tok, cache_dir=cache_dir)
    if not events:
        return None

    ev = None
    if target_actor_id is not None:
        ev = next((e for e in events if e.get("sourceID") == target_actor_id), None)
    if ev is None:
        ev = next((e for e in events if e.get("specID") in TANK_SPEC_IDS), None)
    if ev is None:
        return None

    ci = combatant_info_full_from_wcl_event(ev)
    return build_hydrate_result(
        ci,
        target_name,
        region_hint=region_hint,
        resolve_stats_fn=resolve_stats_fn,
        source="wcl_combatant_info",
        summary_origin="Warcraft Logs",
        summary_suffix="",
    )
