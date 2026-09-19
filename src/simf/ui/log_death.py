"""simf UI — death-attribution primitives for the Why-died log surface.

``_segment_top_abilities`` / ``_badge_for_event`` / ``_render_death_attribution``
— the shared kill-blow + NPC-attribution + damage-school-badge renderers used by
BOTH the per-segment risk view and the full analysis surface. Sits below the
panel layer so both can import it without a cycle. Extracted from ``log_view.py``
(PR 1/3).
"""

from __future__ import annotations

import streamlit as st

from simf.core.bleed_detection import event_is_periodic
from simf.core.bleed_detection import is_bleed as _is_bleed_spell
from simf.io.death_analysis import (
    kill_blow,
    npc_attribution,
    wowhead_npc_url,
    wowhead_spell_url,
)
from simf.io.mitigation_audit import audit_events_direct
from simf.ui.helpers.damage_school_badge import render_school_badge
from simf.ui.log_formatters import _link_md


def _segment_top_abilities(events, run_start_s: float, top_n: int = 3):
    """Top abilities for the segment by post-mit damage, with one-liner stats.

    Includes the dominant school for the ability so the renderer can prefix
    each line with a damage-type glyph — readers don't need to know every
    spell name in the game to see "this was magic" vs "this was physical".
    """
    audit = audit_events_direct(events)
    audit_by_total_to_hp = sorted(audit, key=lambda a: -a.total_to_hp)[:top_n]
    # Find dominant school per ability by summing damage across schools —
    # an ability with mixed schools (rare) shows its biggest one.
    school_by_ability: dict[str, str] = {}
    school_totals: dict[tuple[str, str], int] = {}
    # Brutoh user-feedback 2026-05-21: "there should be links to the
    # abilities and the mobs" in the damage-source list. Carry through
    # a representative spell_id and (NPC name, NPC id) for the dominant
    # caster so the renderer can build Wowhead anchors. SWING auto-attack
    # events have no spell_id → link stays None and the caller falls back
    # to plain text via ``_link_md``.
    spell_id_by_ability: dict[str, int | None] = {}
    source_totals: dict[tuple[str, tuple[str | None, int | None]], int] = {}
    for e in events:
        key = (e.spell_name, e.school or "unknown")
        school_totals[key] = school_totals.get(key, 0) + e.amount
        if e.spell_name not in spell_id_by_ability and e.spell_id:
            spell_id_by_ability[e.spell_name] = int(e.spell_id)
        src_key = (e.spell_name, (e.source_name or None, e.source_npc_id))
        source_totals[src_key] = source_totals.get(src_key, 0) + e.amount
    for (ability, school), amt in school_totals.items():
        current = school_by_ability.get(ability)
        if current is None or amt > school_totals.get((ability, current), 0):
            school_by_ability[ability] = school
    dominant_source: dict[str, tuple[str | None, int | None]] = {}
    for (ability, src), amt in source_totals.items():
        current = dominant_source.get(ability)
        if current is None or amt > source_totals.get((ability, current), 0):
            dominant_source[ability] = src
    rows = []
    for a in audit_by_total_to_hp:
        src_name, src_npc_id = dominant_source.get(a.ability, (None, None))
        rows.append(
            {
                "Ability": a.ability,
                "Spell ID": spell_id_by_ability.get(a.ability),
                "Source": src_name,
                "Source NPC ID": src_npc_id,
                "School": school_by_ability.get(a.ability, "unknown"),
                "Hits": a.hits,
                "To HP": a.total_to_hp,
                "Max single hit": max(
                    (e.amount for e in events if e.spell_name == a.ability), default=0
                ),
                "Mitigation %": a.mitigated_pct,
            }
        )
    return rows


# Damage-school badge renderer — Brutoh idea #d, 2026-05-25.
#
# Replaces the prior emoji-glyph approach with a canonical helper used at
# every Why-died ability rendering site (per-segment risk, death
# attribution, mitigation audit, danger ranking, top damage abilities,
# death timeline). Single source of truth lives in
# `simf.ui.helpers.damage_school_badge` so styling never drifts between
# surfaces; colors come from `data/constants.yaml`.
#
# Bleed treatment: physical-school DOTs that bypass armor (Rake / Rip /
# Rend / Deep Wounds / Lacerate …) render as "Physical (bleed)" so the
# user understands why the mitigation math looked different on that
# bucket. Detection routes through `core.bleed_detection.is_bleed` —
# the same helper the engine uses to set DamageEvent.is_bleed during
# log replay, so the audit table never disagrees with the engine.


def _badge_for_event(event) -> str:
    """Canonical HTML badge for a single DamageTakenEvent.

    Pulls school + is_bleed from the event so callers don't need to
    plumb both fields separately. Returns the HTML span; caller embeds
    it in a markdown string with `unsafe_allow_html=True`.
    """
    return render_school_badge(
        getattr(event, "school", None) or "unknown",
        is_bleed=_is_bleed_spell(
            getattr(event, "spell_name", None) or "", event_is_periodic(event)
        ),
        style="html",
    )


def _render_death_attribution(de, run_start_s: float, top_n: int = 5) -> None:
    """Per-death verdict line + NPC-aggregated attribution table.

    Replaces the bare time-sorted damage dump that used to live under each
    death. The verdict line names the kill-blow (the actual final hit).
    The table groups by (NPC, ability) so three Circuit-Seer swings show
    as one row with hits=3, not three near-duplicate rows.

    Wowhead links render when the source GUID encoded an NPC ID and the
    event carried a numeric spell ID (most SPELL_* events do; SWING does
    not). Both fall back to plain text without errors.
    """
    rel = de.death.rel_time_s
    mm, ss = int(rel // 60), int(rel % 60)
    if not de.preceding:
        st.warning(f"💀 Died at t+{mm}:{ss:02d} — no damage events recorded in the 5s before.")
        return

    kb = kill_blow(de)
    rows = npc_attribution(de, top_n=top_n)

    # Verdict line — name the last hit, not just the heaviest damage.
    if kb is not None:
        ability_md = _link_md(kb.spell_name, wowhead_spell_url(kb.spell_id))
        source_md = _link_md(kb.source_name or "?", wowhead_npc_url(kb.source_npc_id))
        badge = _badge_for_event(kb)
        st.markdown(
            f"**💀 Died at t+{mm}:{ss:02d}** — kill blow: {ability_md} "
            f"{badge} from {source_md}  ·  "
            f"{de.num_hits} hits in the 5s before  ·  "
            f"max hit {de.max_hit:,}  ·  {de.total_damage_window:,} total",
            unsafe_allow_html=True,
        )

    # Attribution table — one row per (NPC, ability), sorted by damage.
    if rows:
        lines = ["**What hit you in the 5s window:**"]
        for r in rows:
            mob = _link_md(r.npc_name, r.npc_url)
            spell = _link_md(r.spell_name, r.spell_url)
            # NPCAttribution aggregates across every hit under this
            # (NPC, ability) pair — some abilities mix a direct hit and a
            # periodic tick under one name (2026-07-18), so there's no
            # single real periodicity to read here. is_periodic=True keeps
            # this a name-only cosmetic badge, same as damage_school_badge's
            # own per-ability aggregation.
            badge = render_school_badge(
                r.school, is_bleed=_is_bleed_spell(r.spell_name, is_periodic=True), style="html"
            )
            lines.append(
                f"- {badge}  {spell} from {mob} — "
                f"{r.hits} hit{'s' if r.hits > 1 else ''}, "
                f"{r.damage_to_hp:,} to HP "
                f"({r.share_of_window:.0%} of fatal window)"
            )
        st.markdown("\n".join(lines), unsafe_allow_html=True)
