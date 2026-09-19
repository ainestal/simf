"""simf UI — Why-died coaching sub-sections.

``render_defensive_coverage`` (hit-vs-coverage, model-independent), the Warrior
rage-flow diagnostic (``_render_rage_flow``), and the cross-log NPC threat
aggregate (``_render_cross_log_threats``). All take pre-computed data as args
and touch no session state. Extracted from ``log_view.py`` (PR 2/3).
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from simf.io.death_analysis import (
    annotate_rage_starvation,
    compute_rage_timeline,
    detect_starvation_windows,
    wowhead_npc_url,
    wowhead_spell_url,
)
from simf.ui.helpers.damage_school_badge import render_school_badge
from simf.ui.log_data import _cached_rage_events
from simf.ui.log_formatters import _fmt_mmss, _link_md

# ─── cross-log NPC threat renderer ────────────────────────────────────────────


def _render_cross_log_threats(rows: list[dict], n_logs: int, n_runs: int) -> None:
    """Top NPCs that have killed `target` across the scanned runs.

    Renders inline above the per-segment risk view. Skipped silently
    when there's only one fatal-data point — the aggregate angle is only
    interesting once a pattern can emerge across runs.
    """
    if not rows:
        return
    scope = (
        f"across {n_runs} runs in {n_logs} logs"
        if n_logs > 1
        else f"across {n_runs} runs in this log"
    )
    st.markdown("### Who keeps killing you")
    st.caption(f"Aggregated {scope}. Click an NPC name to open it on Wowhead.")
    lines = []
    for r in rows:
        mob = _link_md(r["npc_name"], r["npc_url"])
        kills = r["fatal_kills"]
        runs = r["runs"]
        lines.append(
            f"- {mob} — killed you **{kills}×** "
            f"(across {runs} run{'s' if runs != 1 else ''})  ·  "
            f"top ability: _{r['top_ability']}_  ·  "
            f"{r['fatal_damage']:,} fatal-window damage"
        )
    st.markdown("\n".join(lines))


# ─── Rage-flow audit (Brutoh ask 2026-05-24) ──────────────────────────────────


def _render_rage_flow(
    log_name: str,
    target: str,
    run_index: int,
    death_events,
) -> None:
    """Render the rage timeline + starvation windows + starved-spike markers.

    Sits inside the "Full breakdown" expander alongside the existing
    Mitigation audit / Spike score / Death timeline siblings — no separate
    Advanced-toggle gate (the existing toggle is feature-flagged off; gating
    behind it would hide the diagnostic Brutoh explicitly asked for).

    Quiet exit on missing data — empty energize stream means the log
    doesn't carry rage telemetry (e.g. non-Warrior actor or ACL-off log);
    we render nothing rather than a debug-dump.
    """
    energize, casts, run = _cached_rage_events(log_name, target, run_index)
    if not energize or run is None:
        return  # No rage telemetry → no panel.

    # Pull thresholds straight from constants.yaml — no Python-side hardcoding.
    from simf.core.constants import load_constants

    rage_cfg = load_constants().get("rage", {}) or {}
    threshold = float(rage_cfg.get("starvation_threshold", 20.0))
    min_duration = float(rage_cfg.get("starvation_min_duration", 3.0))

    timeline = compute_rage_timeline(
        energize_events=energize,
        cast_events=casts,
        t_start=run.start_time_s,
        t_end=run.end_time_s if run.end_time_s is not None else energize[-1].time_s,
        dt=0.5,
    )
    windows = detect_starvation_windows(timeline, threshold=threshold, min_duration=min_duration)
    starved_attrib = annotate_rage_starvation(death_events or [], windows)

    n_windows = len(windows)
    total_starved_s = sum(end - start for start, end in windows)
    with st.expander(
        f"Rage flow — {n_windows} starvation window{'s' if n_windows != 1 else ''} "
        f"({total_starved_s:.0f}s total)",
        expanded=False,
    ):
        st.caption(
            f"Rage timeline reconstructed from SPELL_ENERGIZE (gains) + Shield "
            f"Block / Ignore Pain / Revenge / Thunder Clap casts (spends). "
            f"Red bands mark windows where rage stayed under {threshold:.0f} for "
            f"≥ {min_duration:.0f}s — \"can't afford the cheapest defensive on "
            f'demand." Red markers on the X-axis mark death-window damage events '
            f"that fell inside (or within 2s of) a starvation window — those hits "
            f"landed when you had no rage to spend. Revenge is modelled at fixed "
            f"cost (no proc-aware accounting yet), so total spend is slightly "
            f"over-estimated — windows may over-report rather than under."
        )

        run_start_s = run.start_time_s

        # Plotly line for the rage timeline. Relative seconds on X for
        # readability (death timeline already uses absolute mm:ss; rage flow
        # mirrors that convention).
        df = pd.DataFrame(
            [{"Time (s into run)": t - run_start_s, "Rage": rage} for t, rage in timeline]
        )
        fig = px.line(df, x="Time (s into run)", y="Rage")
        fig.update_traces(line={"color": "#d9b85a", "width": 1.5})  # warm gold
        fig.update_yaxes(range=[0, 105])

        # Red bands for starvation windows.
        for w_start, w_end in windows:
            fig.add_vrect(
                x0=w_start - run_start_s,
                x1=w_end - run_start_s,
                fillcolor="rgba(168, 60, 50, 0.18)",
                line_width=0,
            )

        # Threshold line so the eye can confirm "below this is starvation."
        fig.add_hline(
            y=threshold,
            line_dash="dot",
            line_color="rgba(168, 60, 50, 0.55)",
            annotation_text=f"starvation < {threshold:.0f}",
            annotation_position="bottom right",
        )

        # Red markers for starved damage events. We aggregate across all
        # deaths so the chart isn't N-overlays; each tick is one event.
        starved_event_times: list[float] = []
        for di, ei_set in starved_attrib.items():
            for ei in ei_set:
                starved_event_times.append(death_events[di].preceding[ei].time_s - run_start_s)
        if starved_event_times:
            fig.add_scatter(
                x=starved_event_times,
                y=[2.0] * len(starved_event_times),
                mode="markers",
                marker={"color": "#a83c32", "symbol": "triangle-up", "size": 11},
                name="Rage-starved damage event",
                showlegend=True,
            )

        fig.update_layout(
            height=260,
            margin={"l": 8, "r": 8, "t": 8, "b": 8},
            showlegend=bool(starved_event_times),
        )
        # Apply Codex paper/font tokens — line + marker colors stay as
        # the hand-picked warm-gold + red since both predate the Codex
        # palette and are already earth-tone-compatible. The layout
        # helper lands paper_bgcolor + plot_bgcolor + font.color so the
        # chart frame blends into the cream page background.
        from .helpers.plotly_codex import apply_codex_layout

        apply_codex_layout(fig)
        st.plotly_chart(fig, width="stretch")

        if windows:
            window_rows = [
                {
                    "Starts (s into run)": f"{w_start - run_start_s:.1f}",
                    "Duration (s)": f"{w_end - w_start:.1f}",
                }
                for w_start, w_end in windows
            ]
            st.dataframe(pd.DataFrame(window_rows), width="stretch", hide_index=True)
        else:
            st.success(
                "No rage starvation detected — you stayed above "
                f"{threshold:.0f} rage throughout, or never sat below it long enough."
            )


def render_defensive_coverage(report) -> None:
    """The hit-vs-coverage coaching section.

    Answers "did I have a defensive up for my biggest hits?" — read straight
    from the log, no simulation (so it works on any spec, calibrated or not).
    `report` is a `core.coaching.CoverageReport` (or None → render nothing).
    """
    if report is None:
        return

    st.markdown("### Defensive coverage on your biggest hits")

    if report.too_short:
        st.caption(report.headline)
        return

    def _continuous_caption() -> None:
        # Continuous mitigation: judged by uptime, never spike-coverage. Plain
        # log-derived fact, no benchmark (a benchmark would re-run the sim and
        # bill model error to the player — see core/coaching.py honesty rules).
        if report.continuous:
            cont = "  ·  ".join(f"{c.name} {c.uptime_pct:.0f}%" for c in report.continuous)
            st.caption(
                f"Continuous active mitigation, uptime over the run (read from your log): {cont}"
            )

    def _honesty_caption() -> None:
        st.caption(
            "Read straight from your combat log — no simulation, so this holds "
            "for any spec. Only cooldowns you actually cast are shown."
        )

    def _kick_availability_caption() -> None:
        # Only worth a summary line once there's ≥1 interruptible hit AND
        # we know this spec's interrupt ability — otherwise the per-hit
        # badges above already say everything there is to say.
        if report.interrupt_ability_name and report.n_interruptible:
            ready, on_cd = report.n_kick_ready, report.n_kick_on_cooldown
            st.caption(
                f"Of your {report.n_interruptible} interruptible hit(s), your own "
                f"{report.interrupt_ability_name} was off cooldown for {ready} and "
                f"on cooldown for {on_cd}."
            )

    if not report.has_data:
        # No reactive cooldowns detected. Stay soft — the player may have
        # correctly tanked on continuous mitigation, or their build's CDs
        # aren't in our registry yet. Never render "N/N uncovered" as a
        # failure — and still surface the continuous uptime we DID measure
        # (throwing it away here was an over-blame trust leak).
        st.info(report.headline)
        st.caption(report.detail)
        _continuous_caption()
        _kick_availability_caption()
        _honesty_caption()
        return

    # Answer-first: headline (no %) then the one-line so-what. A plain bold
    # line, NOT a verdict-card — the surface's single verdict-card is the hero
    # death verdict above; this is a derivative sub-section like per-segment risk.
    st.markdown(f"**{report.headline}**")
    st.markdown(report.detail)

    # Coverage table, sorted into three blocks: uncovered first (the actionable
    # ones), then partial (a minor lever like Demoralizing Shout's −20% only),
    # then cleanly major-covered (credit). Each block keeps biggest-hit order.
    ordered = (
        [h for h in report.top_hits if not h.covered and not h.partial]
        + [h for h in report.top_hits if h.partial]
        + [h for h in report.top_hits if h.covered]
    )
    lines: list[str] = []
    for h in ordered:
        badge = render_school_badge(h.school, is_bleed=h.is_bleed, style="html")
        spell_md = _link_md(h.spell_name, wowhead_spell_url(h.spell_id))
        src_md = _link_md(h.source_name or "?", wowhead_npc_url(h.source_npc_id))
        if h.major_by:
            cov = "🛡 " + " + ".join(h.major_by)
        elif h.minor_by:
            # partial — a real but weaker lever (e.g. Demo Shout −20%); never
            # dressed up as a clean major-CD soak.
            cov = "🟡 partial: " + " + ".join(h.minor_by)
            if h.available_major_levers:
                # …but a real major was off cooldown — name it so the partial
                # doesn't hide an actionable "press the big button" miss.
                cov += "  ·  ⚠️ " + ", ".join(h.available_major_levers) + " ready"
        elif h.had_cd_available:
            # The actionable miss: a major was off cooldown — name it so the
            # player knows exactly what to pre-press next time.
            cov = "⚠️ ready: " + ", ".join(h.available_major_levers)
        elif h.kit_spent:
            # Not a reaction miss — the whole kit was on cooldown. Softer glyph
            # so this doesn't read as "you should have pressed something."
            cov = "⚪ kit on cooldown"
        else:
            cov = "⚠️ no cooldown up"
        if h.interruptible:
            # A stronger, independent signal than defensive coverage — this
            # cast was proven interruptible elsewhere in this same run, so a
            # kick prevents the damage entirely regardless of what else was up.
            kick_note = ""
            if h.kick_was_ready is True:
                # Personally-preventable: their own interrupt was off cooldown.
                kick_note = f"  ·  🟢 your {report.interrupt_ability_name} was ready"
            elif h.kick_was_ready is False:
                # Not on them specifically — their kick was already spent.
                kick_note = f"  ·  ⚪ your {report.interrupt_ability_name} was on cooldown"
            cov = (
                "⚡ interruptible (kicked elsewhere this run) — free prevention"
                f"{kick_note}  ·  " + cov
            )
        lines.append(
            f"- {badge} **{h.amount:,}** at {_fmt_mmss(h.rel_s)} — "
            f"{spell_md} from {src_md}  ·  {cov}"
        )
    st.markdown("\n".join(lines), unsafe_allow_html=True)

    _continuous_caption()
    _kick_availability_caption()
    _honesty_caption()
