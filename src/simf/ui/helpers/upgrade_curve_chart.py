"""Thin Plotly render for the per-piece upgrade curves in the slot dialog.

The math (``optimizer.item_upgrade.ehp_curve``) is pure and unit-tested; this
module is just the Streamlit/Plotly wiring around it, mirroring the
``pareto_scatter`` helper's split. Each curve is one candidate's ΔeHP measured
against the *currently equipped* piece across a range of item levels, so the
point where a line crosses zero is where that piece would overtake what the
player wears today — the "how far do I have to upgrade this before it's worth
it?" answer.
"""

from __future__ import annotations

from collections.abc import Sequence


def render_upgrade_curves(
    curves: Sequence[tuple[str, list]],
    *,
    crest_note: str | None = None,
) -> None:
    """Draw ΔeHP-vs-equipped across item level, one line per candidate.

    ``curves`` is a list of ``(label, points)`` where ``points`` is a list of
    ``item_upgrade.CurvePoint``. A dashed y=0 line marks the equipped baseline.
    No-op when there's nothing plottable, so callers can hand it whatever they
    have without guarding.
    """
    import pandas as pd
    import plotly.express as px
    import streamlit as st

    from .plotly_codex import CODEX_COLORWAY, apply_codex_layout, codex_tints

    rows: list[dict] = []
    labels: list[str] = []
    for label, points in curves:
        if label not in labels:
            labels.append(label)
        for p in points or []:
            rows.append(
                {
                    "Item level": p.ilvl,
                    "ΔeHP vs equipped": round(p.delta_ehp),
                    "Item": label,
                }
            )
    if not rows:
        return

    # Multiple candidates → steel opacity tints (one hue at decreasing weight),
    # the Codex answer for >2-series charts where there's no physical/magical
    # split to map onto the steel/bronze accents. One series → full steel.
    df = pd.DataFrame(rows)
    fig = px.line(
        df,
        x="Item level",
        y="ΔeHP vs equipped",
        color="Item",
        markers=True,
        color_discrete_sequence=list(codex_tints(CODEX_COLORWAY[0], len(labels))),
    )
    # Equipped baseline — anything above this line beats current gear.
    fig.add_hline(
        y=0,
        line_dash="dash",
        line_color="rgba(26, 29, 34, 0.45)",
        annotation_text="your current gear",
        annotation_position="top left",
    )
    fig.update_layout(
        height=320,
        margin=dict(l=8, r=8, t=8, b=8),
        legend_title_text="",
        legend=dict(orientation="h", yanchor="bottom", y=-0.35, xanchor="left", x=0),
    )
    fig.update_xaxes(dtick=3)
    # Codex paper/ink chrome so the chart blends with the cream page instead of
    # rendering Plotly's default white card + blue series. MUST precede
    # st.plotly_chart (see tests/test_plotly_codex.py call-site contract).
    apply_codex_layout(fig)
    st.plotly_chart(fig, use_container_width=True)
    if crest_note:
        st.caption(crest_note)
