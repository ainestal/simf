"""HP-trough ledger — the lowest real HP moments in a log, straight from
the combat log's own advanced-logging block, not a Monte Carlo estimate.

Batch D (2026-07-07 Active Triage Queue, see ROADMAP.md) — "the HP field
sits unparsed in a block already read for position."
``combat_log_damage.parse_damage_event`` already anchors a position
sub-block (posX, posY, mapID, facing, level) from the advanced-logging
block on ACL-on logs; currentHP/maxHP sit at a fixed offset from the SAME
block, closer to its front. See that module's docstring for the parsing
details and the real non-obvious wrinkle it had to account for (the block
doesn't always describe the event's destination — SWING_DAMAGE attaches it
to the attacker instead, so ``current_hp``/``max_hp`` are ``None`` on every
SWING_DAMAGE event by construction, never a wrong-unit's HP).

Mirrors ``segment_risk_timeline.py``'s split: a frozen dataclass + pure
``compute_*`` (unit-testable without Streamlit) + thin ``render_*``.
"""

from __future__ import annotations

from dataclasses import dataclass

from simf.core.bleed_detection import event_is_periodic
from simf.core.bleed_detection import is_bleed as _is_bleed_spell

# Two ledger rows within this many seconds of each other collapse to one —
# keeps a single burst window from producing several near-identical rows.
# Matches this codebase's existing tail-risk window convention (see
# tail_risk_chart.py's p99_10s_window).
_MIN_ROW_GAP_S = 10.0


@dataclass(frozen=True)
class HpTroughRow:
    """One real logged HP low point, with the hit that produced it.

    ``hp_pct``/``current_hp``/``max_hp`` are the log's own reading at the
    moment this event landed — ground truth, not derived from the sim.
    """

    rel_time_s: float
    hp_pct: float  # 0-1 fraction of max_hp
    current_hp: int
    max_hp: int
    spell_name: str
    spell_id: int | None
    school: str
    source_name: str
    source_npc_id: int | None
    amount: int  # this hit's post-mitigation damage to HP
    is_bleed: bool  # computed from the real event's own periodicity, not derived at render time


def compute_hp_trough_ledger(events, run_start: float, top_n: int = 5) -> list[HpTroughRow]:
    """Return up to ``top_n`` lowest real HP readings from ``events``,
    each at least ``_MIN_ROW_GAP_S`` apart, in chronological order.

    Only events carrying a parsed ``current_hp``/``max_hp`` qualify — ACL-off
    logs and SWING_DAMAGE events (see module docstring) leave both ``None``
    and are skipped. Returns ``[]`` when nothing qualifies; callers don't
    need to guard.
    """
    candidates = [e for e in events if e.current_hp is not None and e.max_hp]
    if not candidates:
        return []

    ranked = sorted(candidates, key=lambda e: e.current_hp / e.max_hp)
    picked = []
    for e in ranked:
        if len(picked) >= top_n:
            break
        if any(abs(e.time_s - p.time_s) < _MIN_ROW_GAP_S for p in picked):
            continue
        picked.append(e)

    rows = [
        HpTroughRow(
            rel_time_s=e.time_s - run_start,
            hp_pct=e.current_hp / e.max_hp,
            current_hp=e.current_hp,
            max_hp=e.max_hp,
            spell_name=e.spell_name,
            spell_id=e.spell_id,
            school=e.school,
            is_bleed=_is_bleed_spell(e.spell_name, event_is_periodic(e)),
            source_name=e.source_name,
            source_npc_id=e.source_npc_id,
            amount=e.amount,
        )
        for e in picked
    ]
    return sorted(rows, key=lambda r: r.rel_time_s)


def render_hp_trough_ledger(rows: list[HpTroughRow]) -> None:
    """Render ``rows`` as a compact list with Wowhead links on ability +
    source, earliest-first.

    No-op when ``rows`` is empty — the common case on ACL-off logs, where
    this data doesn't exist at all. Callers don't need to guard.
    """
    if not rows:
        return

    import streamlit as st

    from simf.io.death_analysis import wowhead_npc_url, wowhead_spell_url
    from simf.ui.helpers.damage_school_badge import render_school_badge
    from simf.ui.log_formatters import _fmt_mmss, _link_md

    lines = ["**Closest calls — lowest real HP readings this run:**"]
    for r in rows:
        ability_md = _link_md(r.spell_name, wowhead_spell_url(r.spell_id))
        source_md = _link_md(r.source_name, wowhead_npc_url(r.source_npc_id))
        # r.is_bleed was computed at construction time from the real event's
        # own periodicity (2026-07-18) — don't re-derive from name alone here.
        badge = render_school_badge(r.school, is_bleed=r.is_bleed, style="html")
        lines.append(
            f"- t+{_fmt_mmss(r.rel_time_s)} — **{r.hp_pct:.0%} HP** "
            f"({r.current_hp:,} / {r.max_hp:,}) after {badge} {ability_md} "
            f"from {source_md} ({r.amount:,} to HP)"
        )
    st.markdown("\n".join(lines), unsafe_allow_html=True)
    st.caption(
        "Straight from your log's own advanced-combat-logging data — real HP "
        "readings, not a simulated estimate. Doesn't include melee auto-attacks "
        "(the log attaches their HP reading to the attacker, not you) — only "
        "spell and DoT hits. Only available when Advanced Combat Logging was on."
    )
