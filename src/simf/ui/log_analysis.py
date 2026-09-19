"""simf UI — the main Why-died analysis orchestrator.

``render_log_analysis`` composes the hero verdict, cross-log threats, per-segment
risk, defensive coverage, the cooldown planner, and the full-breakdown expander
(school pie + top-sources/abilities + danger ranking + mitigation audit + rage
flow + per-death timelines). Both flows (local-log and WCL) call it, so it sits
in its own module below the flow modules. Extracted from ``log_view.py`` (PR 3/3).
"""

from __future__ import annotations

import contextlib
import html

import pandas as pd
import plotly.express as px
import streamlit as st

from simf.core.bleed_detection import event_is_periodic
from simf.core.bleed_detection import is_bleed as _is_bleed_spell
from simf.io.death_analysis import log_verdict
from simf.ui.helpers.damage_school_badge import (
    build_ability_school_map,
    render_school_badge,
    school_label,
)
from simf.ui.log_cd_plan import render_cd_plan_panel
from simf.ui.log_coaching import (
    _render_cross_log_threats,
    _render_rage_flow,
    render_defensive_coverage,
)
from simf.ui.log_data import _cached_coverage_report
from simf.ui.log_formatters import _render_run_identity_header, _verdict_card
from simf.ui.log_segment_risk import render_per_segment_risk


def render_log_analysis(
    summary,
    mit_stats,
    death_events,
    class_spec: str | None = None,
    run=None,
    events=None,
    deaths=None,
    segments=None,
    cross_log_rows: list[dict] | None = None,
    cross_log_n_logs: int = 0,
    cross_log_n_runs: int = 0,
    cd_plan_context: dict | None = None,
    coverage_report=None,
) -> None:
    """Render the analysis tables/charts for a parsed LogSummary.

    Hero verdict at the top — answers the tab's own headline question.
    Cross-log NPC threats next (when ≥2 fatal runs in session) — answers
    "what keeps killing me across every key, not just this one." Per-segment
    risk view (boss + trash buckets) follows — answers "where in this run
    did I die." Everything else collapsed into one power-user expander.
    """
    run_info = summary.run

    # Run identity — "Now analyzing: Ara-Kara +18 · Timed 28:34 · 2026-05-22".
    # Sits above the hero verdict so the user always knows which run inside
    # the log is being analyzed — single-run logs land here too (the picker
    # below this surface only renders when len(runs) > 1). Brutoh idea #a,
    # ROADMAP 2026-05-25.
    _render_run_identity_header(run_info)

    # Hero verdict — the first thing users see
    if death_events:
        v = log_verdict(death_events, class_spec=class_spec)
        if v is not None:
            # Derive the dominant school across fatal-window events so the
            # verdict-card subtitle carries a school badge next to the
            # named killer. Without this the card reads "Top killer:
            # Overcharged Discharge (nature) from …" — readable but the
            # color cue is what makes "nature vs physical" pre-attentive.
            from collections import Counter as _Counter

            fatal_school: _Counter = _Counter()
            kill_spell_name: str = ""
            for de in death_events:
                for ev in de.preceding:
                    fatal_school[(ev.school or "unknown").lower()] += ev.amount
            top_school = fatal_school.most_common(1)[0][0] if fatal_school else "unknown"
            # Derive bleed flag from the spell named in v.top_killer
            # (`"<spell> (<school>) from <source>"` from log_verdict).
            if "(" in v.top_killer:
                kill_spell_name = v.top_killer.split(" (", 1)[0]
            badge = render_school_badge(
                top_school,
                # Only the spell NAME survives the log_verdict formatting
                # upstream — no event object to read real periodicity from
                # here, so this stays name-only (cosmetic badge, not sim math).
                is_bleed=_is_bleed_spell(kill_spell_name, is_periodic=True),
                style="html",
            )
            _verdict_card(
                v.headline.replace("**", ""),
                # top_killer is a Warcraft Logs spell/NPC name — escaped
                # since this renders via unsafe_allow_html (badge itself
                # is already-rendered HTML, left as-is).
                f"Top killer: {badge} {html.escape(v.top_killer, quote=False)}  ·  Try: {v.suggested_fix}",
                warn=(v.n_deaths > 1),
            )
    else:
        _verdict_card(
            "You survived the whole run.",
            f"No deaths in {html.escape(run_info.map_name, quote=False)} +{run_info.key_level}.",
        )

    # Affixes-only sub-caption — dungeon / key / duration / outcome already
    # render in the run-identity header above this verdict. Keeping affixes
    # here preserves the one detail the header omits (it was the noisiest
    # field in the old caption and would have pushed the header to two
    # lines).
    st.caption(f"Affixes {run_info.affixes}")

    # Cross-log NPC threats — only renders when there's a multi-run signal.
    if cross_log_rows and cross_log_n_runs >= 2:
        _render_cross_log_threats(cross_log_rows, cross_log_n_logs, cross_log_n_runs)

    # Per-segment risk: boss + trash gaps. Only renders when the caller passes
    # the raw events/deaths/segments alongside the summary.
    if run is not None and segments and events is not None and deaths is not None:
        render_per_segment_risk(run, events, deaths, segments, death_events or [])

    # Defensive coverage on the biggest hits — the model-independent coaching
    # join. Sits between the diagnostic "where you died" and the prescriptive
    # "plan your cooldowns": it answers "did you have a defensive up for your
    # biggest hits?" straight from the log. Two sources, same renderer: the
    # WCL surface builds the report from fetched buff windows and passes it in
    # via `coverage_report`; the local-log surface builds it lazily here from
    # `cd_plan_context` (log_name + target → a file scan). A passed-in report
    # wins so the WCL path never double-builds.
    if coverage_report is not None:
        # Coaching is additive — never let it take down the surface
        # (symmetric with the local-log branch below).
        with contextlib.suppress(Exception):
            render_defensive_coverage(coverage_report)
    elif cd_plan_context is not None and class_spec:
        try:
            coverage = _cached_coverage_report(
                cd_plan_context["log_name"],
                cd_plan_context["target"],
                cd_plan_context["run_index"],
                class_spec,
            )
            render_defensive_coverage(coverage)
        except Exception:  # noqa: S110 — best-effort fallback
            # Coaching is additive — never let it take down the surface.
            pass

    # CD plan — prescriptive companion to the diagnosis above. Needs the
    # full surface context (log/run/char) which the caller threads through
    # via `cd_plan_context`.
    if cd_plan_context is not None:
        render_cd_plan_panel(
            log_name=cd_plan_context["log_name"],
            target=cd_plan_context["target"],
            run_index=cd_plan_context["run_index"],
            char_dict=cd_plan_context.get("char_dict"),
            healer_profile=cd_plan_context.get("healer_profile", "m+_high_key_healer"),
            uncalibrated_warning=cd_plan_context.get("uncalibrated_warning", ""),
            segments=segments,
        )

    with st.expander(
        "Full breakdown (metrics, schools, sources, per-death timelines)", expanded=False
    ):
        cols = st.columns(4)
        cols[0].metric("Damage events", f"{summary.event_count:,}")
        cols[1].metric("DTPS (post-mit)", f"{summary.total_amount / summary.duration_s:,.0f}")
        cols[2].metric("DTPS (pre-mit)", f"{summary.total_base_amount / summary.duration_s:,.0f}")
        cols[3].metric(
            "Mitigation %",
            f"{(1 - summary.total_amount / max(summary.total_base_amount, 1)) * 100:.1f}%",
        )
        cols = st.columns(4)
        cols[0].metric("Total to HP", f"{summary.total_amount:,}")
        cols[1].metric("Blocked", f"{summary.total_blocked:,}")
        cols[2].metric("Absorbed", f"{summary.total_absorbed:,}")
        cols[3].metric("Deaths", str(len(summary.deaths)), delta=None)

        if summary.by_school:
            from .helpers.plotly_codex import apply_codex_layout, codex_tints

            school_df = pd.DataFrame(
                [{"School": s, "Damage": amt} for s, amt in summary.by_school.items()]
            ).sort_values("Damage", ascending=False)
            fig = px.pie(school_df, names="School", values="Damage", title="Damage by school")
            # Codex committed to two accents only (steel + bronze, see
            # ``ui-revamp/phase-2/SPEC.md`` §1). For a damage-by-school
            # pie with up to 8 schools, reaching for 8 hues dilutes the
            # steel/bronze semantic — emit opacity tints off steel so
            # successive slices read as the same hue at decreasing
            # weight. The largest slice (sorted DESC) anchors at full
            # steel; the tail fades.
            fig.update_traces(
                textposition="inside",
                textinfo="percent+label",
                marker={"colors": list(codex_tints("#34556e", len(school_df)))},
            )
            apply_codex_layout(fig)
            st.plotly_chart(fig, width="stretch")

        # Build a {spell_name: (school, is_bleed)} map ONCE for every
        # dataframe surface below — Top abilities, Mitigation audit,
        # Ability danger ranking. Streamlit's `st.dataframe` doesn't
        # render HTML, so we ship plain text labels ("Physical",
        # "Physical (bleed)", "Fire", …) via `school_label`. Falls
        # back to ("unknown", False) for abilities that never appeared
        # in `events` (rare: every aggregator key originated from one).
        ability_school: dict[str, tuple[str, bool]] = (
            build_ability_school_map(events) if events else {}
        )

        def _ability_school_text(name: str) -> str:
            school, is_bleed = ability_school.get(name, ("unknown", False))
            return school_label(school, is_bleed=is_bleed)

        colS, colA = st.columns(2)
        with colS:
            st.markdown("**Top damage sources**")
            if summary.by_source_amount:
                # Sources are NPC mob names, not abilities — no per-row
                # school applies. Brutoh idea #d explicitly named
                # "top damage sources, ability danger ranking, mitigation
                # audit" — this `colS` panel is misnamed (it's been
                # "sources" since v0.8 but the badging requirement is on
                # the *abilities* column to its right). Intentionally
                # bare; mob-side surfacing of school sits at the per-NPC
                # cross-log threats panel above.
                src_df = pd.DataFrame(
                    [
                        {
                            "Source": s,
                            "Hits": summary.by_source_count.get(s, 1),
                            "Total damage": amt,
                        }
                        for s, amt in sorted(
                            summary.by_source_amount.items(), key=lambda kv: -kv[1]
                        )[:15]
                    ]
                )
                src_df["Mean per hit"] = src_df["Total damage"] / src_df["Hits"]
                st.dataframe(
                    src_df.style.format({"Total damage": "{:,}", "Mean per hit": "{:,.0f}"}),
                    width="stretch",
                    hide_index=True,
                )
            else:
                st.caption("No damage sources recorded for this run.")
        with colA:
            st.markdown("**Top damage abilities**")
            if summary.by_ability_amount:
                ab_df = pd.DataFrame(
                    [
                        {
                            "Ability": a,
                            "School": _ability_school_text(a),
                            "Hits": summary.by_ability_count.get(a, 1),
                            "Total damage": amt,
                        }
                        for a, amt in sorted(
                            summary.by_ability_amount.items(), key=lambda kv: -kv[1]
                        )[:15]
                    ]
                )
                ab_df["Mean per hit"] = ab_df["Total damage"] / ab_df["Hits"]
                st.dataframe(
                    ab_df.style.format({"Total damage": "{:,}", "Mean per hit": "{:,.0f}"}),
                    width="stretch",
                    hide_index=True,
                )
            else:
                st.caption("No damage abilities recorded for this run.")

        with st.expander("Ability danger ranking (spike score)", expanded=False):
            st.caption(
                "Spike score = max single hit ÷ mean hit. "
                "A high spike score means the ability is unpredictable and can one-shot "
                "you even when its average looks manageable. Sort by Max hit to find "
                "the abilities most likely to kill you."
            )
            if summary.ability_spike_score:
                danger_rows = []
                for ab, spike in sorted(summary.ability_spike_score.items(), key=lambda kv: -kv[1])[
                    :20
                ]:
                    hits = summary.by_ability_count.get(ab, 1)
                    total = summary.by_ability_amount.get(ab, 0)
                    max_hit = summary.ability_max_hit.get(ab, 0)
                    danger_rows.append(
                        {
                            "Ability": ab,
                            "School": _ability_school_text(ab),
                            "Hits": hits,
                            "Mean hit": total / hits,
                            "Max hit": max_hit,
                            "Spike score": spike,
                        }
                    )
                danger_df = pd.DataFrame(danger_rows)
                st.dataframe(
                    danger_df.style.format(
                        {
                            "Mean hit": "{:,.0f}",
                            "Max hit": "{:,.0f}",
                            "Spike score": "{:.2f}",
                        }
                    ),
                    width="stretch",
                    hide_index=True,
                )

        with st.expander("Mitigation audit (per ability)", expanded=False):
            st.caption(
                "How much of each ability's raw damage you blocked, absorbed, or resisted. "
                "A low Mitigation % on a high-damage ability is a candidate for cooldown coverage."
            )
            if mit_stats:
                mit_rows = [
                    {
                        "Ability": s.ability,
                        "School": _ability_school_text(s.ability),
                        "Hits": s.hits,
                        "Avg raw": s.avg_base,
                        "Avg to HP": s.avg_to_hp,
                        "Blocked%": s.blocked_pct,
                        "Absorbed%": s.absorbed_pct,
                        "Resisted%": s.resisted_pct,
                        "Unmitigated%": s.unmitigated_pct,
                    }
                    for s in mit_stats[:20]
                ]
                mit_df = pd.DataFrame(mit_rows)
                st.dataframe(
                    mit_df.style.format(
                        {
                            "Avg raw": "{:,.0f}",
                            "Avg to HP": "{:,.0f}",
                            "Blocked%": "{:.1%}",
                            "Absorbed%": "{:.1%}",
                            "Resisted%": "{:.1%}",
                            "Unmitigated%": "{:.1%}",
                        }
                    ),
                    width="stretch",
                    hide_index=True,
                )

        # Rage-flow audit (Brutoh ask 2026-05-24). Sibling to the death
        # timeline below — reads the same log+run already cached upstream.
        if cd_plan_context is not None:
            _render_rage_flow(
                log_name=cd_plan_context["log_name"],
                target=cd_plan_context["target"],
                run_index=cd_plan_context["run_index"],
                death_events=death_events or [],
            )

        if death_events:
            st.markdown("### Death timeline")
            for de in death_events:
                mm = int(de.death.rel_time_s // 60)
                ss = int(de.death.rel_time_s % 60)
                with st.expander(
                    f"Death at {mm}:{ss:02d} — {de.num_hits} hits in 5s window, "
                    f"{de.total_damage_window:,} total damage, "
                    f"max hit {de.max_hit:,}",
                    expanded=True,
                ):
                    if de.preceding:
                        pre_rows = [
                            {
                                "Time (s before death)": f"{de.death.time_s - e.time_s:.1f}s",
                                "Source": e.source_name,
                                # Mixed int/None promotes to float64 by default
                                # (NaN-fills the Nones), which renders IDs as
                                # "12345.0" — astype("Int64") below pins the
                                # nullable-integer dtype so they render bare.
                                "NPC ID": e.source_npc_id if e.source_npc_id else None,
                                "Ability": e.spell_name,
                                "Spell ID": e.spell_id if e.spell_id else None,
                                # Canonical school label — "Physical",
                                # "Physical (bleed)", "Fire" … — same
                                # text shape every dataframe surface
                                # uses (Brutoh idea #d, 2026-05-25).
                                "School": school_label(
                                    e.school,
                                    is_bleed=_is_bleed_spell(
                                        e.spell_name or "", event_is_periodic(e)
                                    ),
                                ),
                                "To HP": e.amount,
                                "Raw": e.base_amount,
                                "Blocked": e.blocked,
                                "Absorbed": e.absorbed,
                                "Crit": "✓" if e.is_critical else "",
                            }
                            for e in sorted(de.preceding, key=lambda e: e.time_s, reverse=True)
                        ]
                        pre_df = pd.DataFrame(pre_rows)
                        pre_df = pre_df.astype({"NPC ID": "Int64", "Spell ID": "Int64"})
                        st.dataframe(
                            pre_df.style.format(
                                {
                                    "To HP": "{:,}",
                                    "Raw": "{:,}",
                                    "Blocked": "{:,}",
                                    "Absorbed": "{:,}",
                                }
                            ),
                            width="stretch",
                            hide_index=True,
                        )
                        st.caption(
                            "NPC IDs and spell IDs cross-reference Wowhead — "
                            "`wowhead.com/npc=<id>` or `wowhead.com/spell=<id>`."
                        )
                    else:
                        st.info("No damage events recorded in the 5s before this death.")
