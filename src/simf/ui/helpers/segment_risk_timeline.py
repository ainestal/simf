"""Key-level risk timeline — at-a-glance strip across a whole run.

Batch D (2026-07-07 Active Triage Queue, see ROADMAP.md) — the "Where you
died" surface (``log_segment_risk.render_per_segment_risk``) already computes
per-segment DTPS / mitigation / hits-taken / deaths, but only renders them as
a vertical stack of ``st.expander`` cards. A player has to scroll through N
expanders to see where the dangerous stretches of a run actually were. This
module adds a compact horizontal strip above that list — pure assembly over
numbers ``render_per_segment_risk`` already computes, not a new risk score.

Mirrors ``tail_risk_chart.py``'s split: a frozen dataclass + a pure
``compute_*`` function (unit-testable without Streamlit) + a thin
``render_*`` function using ``px``/``go`` wiring, Codex theming via
``plotly_codex``, and a no-op early return on empty input.

No chart animation is added here (static Plotly bar, no
``layout.transition``) — same reduced-motion discipline as
``tail_risk_chart.py`` (see that module's docstring + ``plotly_codex.py``'s
pointer comment: the CSS ``prefers-reduced-motion`` guard doesn't reach
Plotly's own animation engine, so the safe default is to add none).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SegmentRiskBar:
    """One boss/trash segment's risk profile, in chronological order.

    Fields are re-derived from the exact same per-segment calculations
    ``render_per_segment_risk`` already does inline (segment event slice →
    summed ``amount`` → DTPS) — no new composite "risk score." When a single
    scalar is needed for bar color, ``n_deaths`` (deaths take precedence)
    plus ``dtps`` (for hover) is enough; this is assembly, not new risk math.
    """

    label: str
    rel_start_s: float
    duration_s: float
    dtps: float
    n_deaths: int
    kind: str  # "boss" or "trash" — mirrors RunSegment.kind


def compute_segment_risk_bars(run, events, deaths, segments) -> list[SegmentRiskBar]:
    """Build one ``SegmentRiskBar`` per segment, in existing chronological
    order (``segments`` is already sorted by ``start_time_s`` — no re-sort).

    Death-to-segment matching and the DTPS denominator (``max(duration, 1)``)
    are copied verbatim from ``render_per_segment_risk`` so the timeline and
    the detailed cards below it never disagree on the same run.

    Returns ``[]`` when there are no segments — callers don't need to guard.
    """
    if not segments:
        return []

    death_by_segment: dict[int, list] = {}
    for d in deaths:
        for i, seg in enumerate(segments):
            if seg.start_time_s <= d.time_s <= seg.end_time_s:
                death_by_segment.setdefault(i, []).append(d)
                break

    run_start = run.start_time_s
    bars: list[SegmentRiskBar] = []
    for i, seg in enumerate(segments):
        seg_events = [e for e in events if seg.start_time_s <= e.time_s <= seg.end_time_s]
        seg_dmg = sum(e.amount for e in seg_events)
        seg_deaths = death_by_segment.get(i, [])
        bars.append(
            SegmentRiskBar(
                label=seg.label,
                rel_start_s=seg.start_time_s - run_start,
                duration_s=seg.duration_s(),
                dtps=seg_dmg / max(seg.duration_s(), 1),
                n_deaths=len(seg_deaths),
                kind=seg.kind,
            )
        )
    return bars


def render_segment_risk_timeline(bars: list[SegmentRiskBar], key: str) -> None:
    """Render a horizontal Gantt-style strip of ``bars`` ordered by
    ``rel_start_s`` — steel for segments with no death, bronze for segments
    where at least one death occurred (same steel/bronze death-vs-survival
    semantic as ``tail_risk_chart.py``).

    WCAG 1.4.1 (Use of Color): a death segment ALSO gets a crosshatch
    ``marker.pattern`` fill, not just the bronze tint — the bar color alone
    was the only signal that a death happened here, and Plotly's hover
    tooltip (the only place the death COUNT is spelled out as text) needs a
    pointer, which a keyboard-only reader can't summon. The hatch is a
    second, color-independent channel a reader can see without hovering.

    No-op when ``bars`` is empty — callers don't need to guard.
    """
    if not bars:
        return

    import plotly.graph_objects as go
    import streamlit as st

    from .plotly_codex import CODEX_COLORWAY, apply_codex_layout

    colors = [CODEX_COLORWAY[1] if b.n_deaths else CODEX_COLORWAY[0] for b in bars]
    patterns = ["x" if b.n_deaths else "" for b in bars]
    customdata = [[b.label, b.dtps, b.n_deaths, b.kind] for b in bars]

    fig = go.Figure(
        go.Bar(
            x=[b.duration_s for b in bars],
            y=["Run"] * len(bars),
            base=[b.rel_start_s for b in bars],
            orientation="h",
            marker=dict(color=colors, pattern=dict(shape=patterns, fgcolor="#f4f1ea")),
            customdata=customdata,
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "DTPS: %{customdata[1]:,.0f}<br>"
                "Deaths: %{customdata[2]}<extra></extra>"
            ),
        )
    )
    fig.update_yaxes(visible=False, showticklabels=False)
    fig.update_xaxes(title_text="Time into run (s)")
    fig.update_layout(
        height=140,
        margin=dict(l=10, r=10, t=10, b=30),
        showlegend=False,
        bargap=0,
    )
    apply_codex_layout(fig)
    st.plotly_chart(fig, width="stretch", key=key)
    st.caption(
        "Each bar is one segment (boss or trash gap) in order — a "
        "crosshatched bronze bar marks a segment where you died. Hover for "
        "DTPS and death count."
    )
