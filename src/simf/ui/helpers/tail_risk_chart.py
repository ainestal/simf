"""Tail-risk surfacing for the key-level verdict panel.

Top-5 #2 from the 2026-07-06 retrospective (see ROADMAP.md's Active
Triage Queue): ``death_rate`` + mean ΔeHP are point estimates — a
Monte Carlo sim's actual edge over a static eHP calculator is the
*tail*. `p99_10s_window` / `p99_15s_window` (worst-case burst size)
and `p5_min_hp_pct` (how low you actually got, not just how big one
burst was) already exist on ``SimResult`` / ``KeyLevelPoint``; this
module is the surfacing job.

Framing rules (calibration-scientist review, 2026-07-06 — see
``docs/validation`` for the retrospective's engine-review threads):
  - "at least N%", never "equal to N%" — a p99 window is the value the
    worst 1% *exceed*, not a fixed number they hit exactly.
  - Always say "before healing" — the window is gross post-mitigation
    damage; it does not net out the ~10-15s the healer has to react.
  - Hedge at the same level as the mean-ΔeHP number next to it, not
    more or less — this inherits the same per-spec ``calibrated`` flag
    and has *fewer* moving parts than death_rate (no HealingProfile
    dependence), so it doesn't need its own scarier disclaimer.
  - Round to whole percent. At the sweep's 200-iteration default the
    p99 tail is ~2 samples; 15s of a percent point is fake precision.

No chart animation is added here (static Plotly bar, no
``layout.transition``) — this repo has no ``prefers-reduced-motion``
gate anywhere yet (verified 2026-07-06), so the safe default for a
new chart is to introduce zero motion rather than add a gate for
motion that doesn't exist.

Two pure-data functions own the math so they're unit-testable without
an AppTest; the render function is thin Plotly wiring, mirroring the
``pareto_scatter`` / ``upgrade_curve_chart`` helper split.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WindowRiskBar:
    """One rolling-window burst-risk bar, expressed as % of max HP.

    ``peak_incoming_hps`` (Batch C, 2026-07-07) is a one-line derived
    value from the SAME p99 window-sum this bar already renders —
    ``window_sum / window_s``. It is plumbing, not a new measurement:
    ``p99_10s_window`` is a per-iteration sliding-window MAX DAMAGE SUM
    (not a rate) at the 99th percentile across Monte Carlo iterations
    (see ``core/runner.py``'s ``compute_window_max`` +
    ``np.percentile(w10, 99)``), so dividing by the window length gives
    exactly "peak incoming HPS" the healer would have to out-throughput.
    It inherits the same "at least" / "before healing" hedging as
    ``pct_of_max_hp`` — it's the same gross, pre-heal quantity in
    different units, not a new claim.
    """

    label: str
    pct_of_max_hp: float
    window_s: float
    peak_incoming_hps: float


def compute_window_risk_bars(point) -> list[WindowRiskBar]:
    """Build the 5s/10s/15s p99 window bars from a ``KeyLevelPoint``.

    Returns ``[]`` when ``sample_max_hp`` is unavailable or zero (a
    stub/legacy point, or a degenerate zero-HP character) — dividing
    by it would either crash or fabricate a meaningless ratio, and an
    empty chart is a cleaner failure than either.
    """
    if not point.sample_max_hp or point.sample_max_hp <= 0:
        return []
    max_hp = point.sample_max_hp
    windows: list[tuple[str, float, float]] = [
        ("5s p99", 5.0, point.p99_5s_window),
        ("10s p99", 10.0, point.p99_10s_window),
        ("15s p99", 15.0, point.p99_15s_window),
    ]
    return [
        WindowRiskBar(
            label=label,
            pct_of_max_hp=window_sum / max_hp * 100,
            window_s=window_s,
            peak_incoming_hps=window_sum / window_s,
        )
        for label, window_s, window_sum in windows
    ]


def render_tail_risk_panel(point, push_key: int) -> None:
    """Render the burst-risk sentence(s) + VaR-style bar chart for one
    ``KeyLevelPoint``.

    ``push_key`` is only used for the sentence copy (which key level
    this is describing) — callers pick the point the same way the
    skill ladder does (``_pick_ladder_key``), so the most informative
    key gets the tail-risk read, not necessarily the top of the sweep.

    No-op when there's nothing plottable (``compute_window_risk_bars``
    returns empty) — callers don't need to guard.
    """
    import pandas as pd
    import plotly.express as px
    import streamlit as st

    from .plotly_codex import CODEX_COLORWAY, apply_codex_layout

    bars = compute_window_risk_bars(point)
    if not bars:
        return

    burst_pct = bars[1].pct_of_max_hp  # 10s p99 — the retrospective's headline number
    burst_hps = bars[1].peak_incoming_hps
    burst_window_s = int(bars[1].window_s)
    st.markdown(f"#### Worst-case burst risk at +{push_key}")
    st.markdown(
        f"In your worst 1-in-100 pulls, a single {burst_window_s}-second burst "
        f"takes **at least {burst_pct:.0f}% of your max HP** — before healing. "
        f"That's **at least {burst_hps:,.0f} HPS** the healer has to "
        f"out-throughput for those {burst_window_s} seconds. "
        "A Monte Carlo estimate from this sweep, not a guaranteed number."
    )
    if point.p5_min_hp_pct is not None:
        margin_pct = point.p5_min_hp_pct * 100
        st.markdown(
            f"In your worst 1-in-20 pulls, your HP actually dropped to "
            f"**{margin_pct:.0f}% or lower** (or you died) — the nadir after "
            "that pull's modeled heals, not a pre-heal reading."
        )

    df = pd.DataFrame(
        {"Window": [b.label for b in bars], "% of max HP": [b.pct_of_max_hp for b in bars]}
    )
    fig = px.bar(df, x="Window", y="% of max HP")
    fig.update_traces(marker_color=CODEX_COLORWAY[1])  # bronze — damage/burst semantic
    fig.add_hline(
        y=100,
        line_dash="dash",
        line_color=CODEX_COLORWAY[0],
        annotation_text="Your max HP",
        annotation_position="top left",
    )
    fig.update_layout(height=280, margin=dict(l=10, r=10, t=30, b=10), showlegend=False)
    apply_codex_layout(fig)
    st.plotly_chart(fig, width="stretch", key=f"tail_risk_chart_{push_key}")
    st.caption(
        "Each bar is the 99th-percentile worst burst in a rolling window of "
        "that length — how bad your worst 1-in-100 pull gets as the clock "
        "widens from 5 to 15 seconds. Not net of healing."
    )
