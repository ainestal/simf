"""simf UI — the "Where you died" per-segment / per-pull risk surface.

``render_per_segment_risk`` walks the run's boss/trash segments, expands the
ones with deaths, and (for a whole-run trash blob) flips to a per-pull-first
hierarchy. Descriptive, not prescriptive — the cooldown planner (log_cd_plan)
owns the "when to press buttons" answer. Extracted from ``log_view.py`` (PR 2/3).
"""

from __future__ import annotations

import streamlit as st

from simf.core.bleed_detection import is_bleed as _is_bleed_spell
from simf.io.death_analysis import wowhead_npc_url, wowhead_spell_url
from simf.ui.helpers.damage_school_badge import render_school_badge
from simf.ui.helpers.hp_trough_ledger import (
    compute_hp_trough_ledger,
    render_hp_trough_ledger,
)
from simf.ui.helpers.segment_risk_timeline import (
    compute_segment_risk_bars,
    render_segment_risk_timeline,
)
from simf.ui.log_death import _render_death_attribution, _segment_top_abilities
from simf.ui.log_formatters import _fmt_mmss, _link_md

# ─── Per-pull-first hierarchy heuristic ───────────────────────────────────────
#
# Brutoh user-feedback 2026-05-27 (Windrunner Spire +19, timed, no deaths):
# "I still don't understand why the runs analysed from WCL don't show each of
# the pulls but they do a general 'trash' that seems to put all of the dungeon
# together, but then it does distinguish between pulls as it highlights them
# in the 'Pulls in this trash window'."
#
# When the entire log is effectively one giant trash window (clean key, no
# ENCOUNTER events parsed by the WCL adapter), the default aggregate-first
# view buries the actionable per-pull info under a single 30-minute card.
# Flip the hierarchy in that narrow case: per-pull cards become the primary
# view, aggregate becomes a footer summary.
#
# Heuristic threshold (kept conservative so death-anchored runs are
# untouched): trash segment AND duration > 80% of total run AND ≥ 2 pulls.

_PER_PULL_RUN_FRACTION_THRESHOLD = 0.80
_PER_PULL_MIN_PULLS = 2


def _should_flip_to_per_pull_view(seg, run_total_duration_s: float, n_pulls: int) -> bool:
    """Return True when a trash segment should render per-pull cards first.

    The threshold is intentionally narrow — death-anchored runs and runs with
    proper ENCOUNTER segmentation are not in scope. Only when the entire run
    is one giant trash blob (typical clean WCL key) do we flip.
    """
    if seg.kind != "trash":
        return False
    if n_pulls < _PER_PULL_MIN_PULLS:
        return False
    if run_total_duration_s <= 0:
        return False
    return seg.duration_s() > _PER_PULL_RUN_FRACTION_THRESHOLD * run_total_duration_s


def _render_pull_card(pull, seg_events, run_start: float) -> None:
    """Render one trash pull as a primary card.

    Reuses ``_segment_top_abilities`` against the pull's event slice so the
    top-damage / max-hit / mitigation info is recomputed at pull granularity
    rather than aggregated across the whole trash window.
    """
    rel_pull_start = pull.start_time_s - run_start
    n_types = len(pull.sources)
    n_mobs = len(pull.mob_guids)
    if n_mobs >= n_types and n_mobs > 0:
        mob_count_text = (
            f"{n_mobs} mob{'s' if n_mobs > 1 else ''} ({n_types} type{'s' if n_types > 1 else ''})"
            if n_mobs > n_types
            else f"{n_mobs} mob{'s' if n_mobs > 1 else ''}"
        )
    else:
        mob_count_text = (
            f"{n_types} mob type{'s' if n_types > 1 else ''} "
            "(individual count unknown — older log format)"
        )

    pull_events = [e for e in seg_events if pull.start_time_s <= e.time_s <= pull.end_time_s]
    pull_dmg = sum(e.amount for e in pull_events)
    pull_dur = max(pull.duration_s(), 1.0)

    st.markdown(
        f"#### Pull {pull.index} · t+{_fmt_mmss(rel_pull_start)} · "
        f"{pull.duration_s():.0f}s · {mob_count_text}"
    )
    cols = st.columns(3)
    cols[0].metric("DTPS", f"{pull_dmg / pull_dur:,.0f}")
    cols[1].metric("Damage to HP", f"{pull_dmg:,}")
    cols[2].metric("Hits taken", f"{len(pull_events):,}")

    top_rows = _segment_top_abilities(pull_events, run_start, top_n=3)
    if top_rows:
        lines = ["**Top damage abilities in this pull:**"]
        for r in top_rows:
            ability_md = _link_md(r["Ability"], wowhead_spell_url(r.get("Spell ID")))
            src_md = (
                _link_md(r["Source"], wowhead_npc_url(r.get("Source NPC ID")))
                if r.get("Source")
                else ""
            )
            src_clause = f" from {src_md}" if src_md else ""
            badge = render_school_badge(
                r["School"],
                # r is aggregated across every hit of this ability (via
                # _segment_top_abilities) — some abilities mix a direct hit
                # and a periodic tick under one name (2026-07-18), so
                # is_periodic=True keeps this a name-only cosmetic badge.
                is_bleed=_is_bleed_spell(r["Ability"], is_periodic=True),
                style="html",
            )
            lines.append(
                f"- {badge}  **{ability_md}**{src_clause} — "
                f"{r['Hits']} hits, "
                f"{r['To HP']:,} to HP "
                f"(max single hit {r['Max single hit']:,}, "
                f"{r['Mitigation %']:.0%} mitigated)"
            )
        st.markdown("\n".join(lines), unsafe_allow_html=True)


def render_per_segment_risk(run, events, deaths, segments, death_events) -> None:
    """Boss-by-boss + trash-gap risk view, anchored to the user's actual log.

    Brutoh's ask: "specific places that are risky for me." Phase A surfaces
    where deaths actually happened, with the top-3 fatal-window abilities and
    their source mob/boss. Predictive per-pull risk is deferred until magic-mit
    is fixed (see docs/validation/mgt_12_2026_05_15.md).
    """
    if not segments:
        return

    # Map deaths into segment indices for fast lookup.
    death_by_segment: dict[int, list] = {}
    for d in deaths:
        for i, seg in enumerate(segments):
            if seg.start_time_s <= d.time_s <= seg.end_time_s:
                death_by_segment.setdefault(i, []).append(d)
                break

    # Match reconstructed DeathEvents (which carry the 5s preceding window)
    # to deaths by absolute time, so the same death_event isn't re-computed.
    death_event_by_time = {de.death.time_s: de for de in death_events}

    n_bosses = sum(1 for s in segments if s.kind == "boss")
    n_boss_first_try = sum(
        1
        for s in segments
        if s.kind == "boss"
        and s.encounter
        and s.encounter.attempt_index == 1
        and s.encounter.success
    )
    n_deaths = len(deaths)
    deaths_on_boss = sum(1 for ds in death_by_segment.values() for _ in ds if True)  # placeholder
    deaths_on_boss = sum(
        len(ds) for i, ds in death_by_segment.items() if segments[i].kind == "boss"
    )
    deaths_on_trash = n_deaths - deaths_on_boss

    st.markdown("### Where you died")
    if n_deaths == 0:
        st.success(
            f"Clean run — no deaths across {n_bosses} bosses "
            f"({n_boss_first_try} on first try) and the trash between them."
        )
    else:
        trash_word = "trash gap" if deaths_on_trash == 1 else "trash gaps"
        boss_word = "boss attempt" if deaths_on_boss == 1 else "boss attempts"
        parts = []
        if deaths_on_trash:
            parts.append(f"{deaths_on_trash} on {trash_word}")
        if deaths_on_boss:
            parts.append(f"{deaths_on_boss} on {boss_word}")
        st.caption(
            f"{n_deaths} death{'s' if n_deaths > 1 else ''} — {' · '.join(parts)}. "
            "Bucketed by encounter window from your log."
        )

    # At-a-glance risk timeline across the whole run — the detailed
    # expanders below require scrolling to see where the dangerous
    # stretches were; this compact strip answers that at a glance.
    render_segment_risk_timeline(
        compute_segment_risk_bars(run, events, deaths, segments),
        key="segment_risk_timeline",
    )

    # Real logged HP low points, not a sim estimate — ACL-on logs only
    # (no-op otherwise). Sits next to the timeline above: the timeline
    # answers "when was it dangerous", this answers "how low did you
    # actually get, and what hit you".
    render_hp_trough_ledger(compute_hp_trough_ledger(events, run.start_time_s))

    # Walk segments in time order. Auto-expand segments with deaths; collapse
    # the rest. Keep the visual scan-able — title carries enough info to skip.
    run_start = run.start_time_s
    # `run.end_time_s` is `None` for truncated logs (CHALLENGE_MODE_START with
    # no END). Fall back to the latest segment end so the per-pull heuristic
    # below can still compute a sensible run duration in that case.
    run_end_for_duration = run.end_time_s
    if run_end_for_duration is None:
        run_end_for_duration = max((s.end_time_s for s in segments), default=run.start_time_s)
    run_total_duration_s = max(run_end_for_duration - run.start_time_s, 0.0)

    from simf.io.pull_segmentation import cluster_trash_pulls

    for i, seg in enumerate(segments):
        seg_events = [e for e in events if seg.start_time_s <= e.time_s <= seg.end_time_s]
        seg_deaths = death_by_segment.get(i, [])
        seg_dmg = sum(e.amount for e in seg_events)
        seg_base = sum(e.base_amount for e in seg_events)
        rel_start = seg.start_time_s - run_start

        # Compute pulls up-front for trash segments — the heuristic needs the
        # count before we decide expander defaults, and the per-pull renderer
        # below uses the same list. cluster_trash_pulls is cheap (linear).
        pulls = cluster_trash_pulls(seg_events, seg) if seg.kind == "trash" and seg_events else []
        flip_to_per_pull = _should_flip_to_per_pull_view(seg, run_total_duration_s, len(pulls))

        icon = "💀" if seg_deaths else ("🗡" if seg.kind == "boss" else "·")
        death_str = (
            f"  ·  {len(seg_deaths)} death{'s' if len(seg_deaths) > 1 else ''}"
            if seg_deaths
            else ""
        )
        # When the flip fires, advertise the pull count in the title so the
        # collapsed expander promises what's inside.
        pull_str = f"  ·  {len(pulls)} pulls" if flip_to_per_pull else ""
        title = (
            f"{icon}  t+{_fmt_mmss(rel_start)}  ·  {seg.label}  "
            f"·  {_fmt_mmss(seg.duration_s())}  ·  {seg_dmg:,} to HP"
            f"{death_str}{pull_str}"
        )

        # Force-expand when the flip fires — otherwise the per-pull cards we
        # render below sit inside a collapsed expander and the user never
        # sees them (Brutoh feedback 2026-05-27).
        expanded = bool(seg_deaths) or flip_to_per_pull

        with st.expander(title, expanded=expanded):
            if not seg_events:
                st.caption("No damage events in this window.")
                continue

            if flip_to_per_pull:
                # Per-pull-first hierarchy: pull cards are the primary view,
                # aggregate becomes a footer summary. This is the narrow
                # "whole run is one giant trash blob" case.
                st.caption(
                    f"This run was effectively one continuous trash window "
                    f"({len(pulls)} pulls). Showing per-pull breakdown first "
                    "— whole-window aggregate is below."
                )
                # Deaths take precedence over the per-pull view — if a death
                # landed in the giant trash blob, the user still needs the
                # 5s reconstruction. Render death attribution before the
                # per-pull cards.
                for d in seg_deaths:
                    de = death_event_by_time.get(d.time_s)
                    if de is None:
                        st.warning(
                            f"Death at t+{_fmt_mmss(d.rel_time_s)} — no damage events recorded in the 5s before."
                        )
                        continue
                    _render_death_attribution(de, run_start)
                for p in pulls:
                    _render_pull_card(p, seg_events, run_start)
                st.markdown(
                    "_Mob counts include only creatures that hit you — patrols you "
                    "killed before they reached you don't show here._"
                )

                # Whole-window aggregate as a footer summary, not a dominant
                # card. Keep it terse — the user came for per-pull detail.
                mit_pct = (1 - seg_dmg / max(seg_base, 1)) * 100
                st.markdown("---")
                st.markdown(
                    f"**Whole-window totals** · "
                    f"DTPS {seg_dmg / max(seg.duration_s(), 1):,.0f} · "
                    f"Mitigation {mit_pct:.1f}% · "
                    f"Hits taken {len(seg_events):,} · "
                    f"Damage to HP {seg_dmg:,}"
                )
                continue

            # Default (non-flipped) hierarchy below — aggregate first.
            #
            # Headline metrics for the segment.
            cols = st.columns(4)
            cols[0].metric("DTPS", f"{seg_dmg / max(seg.duration_s(), 1):,.0f}")
            mit_pct = (1 - seg_dmg / max(seg_base, 1)) * 100
            cols[1].metric("Mitigation", f"{mit_pct:.1f}%")
            cols[2].metric("Hits taken", f"{len(seg_events):,}")
            cols[3].metric("Deaths", str(len(seg_deaths)))

            # Per-death verdict + NPC-aggregated attribution table — Wowhead
            # links on mobs and abilities. Replaces the old time-sorted dump.
            for d in seg_deaths:
                de = death_event_by_time.get(d.time_s)
                if de is None:
                    st.warning(
                        f"Death at t+{_fmt_mmss(d.rel_time_s)} — no damage events recorded in the 5s before."
                    )
                    continue
                _render_death_attribution(de, run_start)

            # Top abilities in the segment — ranked by post-mit damage to HP.
            # Deliberately NOT called a "window" — that word is reserved for
            # the 5s-before-death list above (log_death.py's "What hit you in
            # the 5s window"). This list spans the whole segment/pull; reusing
            # "window" here let a player read a segment-wide leaderboard entry
            # as if it were part of what actually killed them (round-1
            # multi-agent review, 2026-07-05 — a real mis-coaching bug, not
            # just a naming nit).
            top_rows = _segment_top_abilities(seg_events, run_start, top_n=5)
            if top_rows:
                lines = ["**Top damage abilities across this whole pull:**"]
                for r in top_rows:
                    # Wowhead links on ability + source, falling back to plain
                    # text for SWING auto-attacks (no spell ID) or events with
                    # no parseable NPC GUID. Brutoh user-feedback 2026-05-21.
                    ability_md = _link_md(r["Ability"], wowhead_spell_url(r.get("Spell ID")))
                    src_md = (
                        _link_md(r["Source"], wowhead_npc_url(r.get("Source NPC ID")))
                        if r.get("Source")
                        else ""
                    )
                    src_clause = f" from {src_md}" if src_md else ""
                    badge = render_school_badge(
                        r["School"],
                        # Aggregated across every hit of this ability — see
                        # the sibling call site's comment above (2026-07-18).
                        is_bleed=_is_bleed_spell(r["Ability"], is_periodic=True),
                        style="html",
                    )
                    lines.append(
                        f"- {badge}  **{ability_md}**{src_clause} — "
                        f"{r['Hits']} hits, "
                        f"{r['To HP']:,} to HP "
                        f"(max single hit {r['Max single hit']:,}, "
                        f"{r['Mitigation %']:.0%} mitigated)"
                    )
                st.markdown("\n".join(lines), unsafe_allow_html=True)

            # Phase 3.7 — for trash segments, surface the inferred pulls so
            # the user can see "this 90s window was 4 packs, not one blob".
            # Predictive per-pull risk (Phase 3.8) will hang off this same
            # structure once magic-mit is good enough to trust.
            if seg.kind == "trash" and pulls:
                pull_lines = [f"**Pulls in this trash window ({len(pulls)}):**"]
                for p in pulls:
                    rel_pull_start = p.start_time_s - run_start
                    n_types = len(p.sources)
                    n_mobs = len(p.mob_guids)
                    # When GUIDs come back empty (old logs / ACL off), fall
                    # back to "N types only". When n_mobs > n_types we're
                    # disaggregating same-name mobs honestly.
                    if n_mobs >= n_types and n_mobs > 0:
                        mob_count_text = (
                            f"{n_mobs} mob{'s' if n_mobs > 1 else ''} "
                            f"({n_types} type{'s' if n_types > 1 else ''})"
                            if n_mobs > n_types
                            else f"{n_mobs} mob{'s' if n_mobs > 1 else ''}"
                        )
                    else:
                        mob_count_text = (
                            f"{n_types} mob type{'s' if n_types > 1 else ''} "
                            "(individual count unknown — older log format)"
                        )
                    pull_lines.append(
                        f"- Pull {p.index} at t+{_fmt_mmss(rel_pull_start)} "
                        f"· {p.duration_s():.0f}s · {mob_count_text} "
                        f"· {p.total_damage:,} dmg"
                    )
                pull_lines.append(
                    "_Mob counts include only creatures that hit you — patrols you "
                    "killed before they reached you don't show here._"
                )
                st.markdown("\n".join(pull_lines))
