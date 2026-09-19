"""HP-over-time chart for the key-level verdict panel.

A natural companion to ``tail_risk_chart.py`` — both describe the SAME
worst-spike sample iteration (``sample_max_hp``/``sample_damage_timeline``/
``sample_heal_timeline`` on ``KeyLevelPoint``), just as a burst-size bar
chart vs. an actual HP curve over the pull. Scoped deliberately narrow
(2026-07-28 scoping workflow, see ``docs/validation`` — search "HP fan
chart"): a single representative iteration's trace, reconstructed from data
the sweep already collects, NOT a percentile band across all iterations.
A true multi-iteration fan chart needs ``run_simulation(keep_iteration_
results=True)`` at reduced iteration count and was deliberately deferred —
this ships the zero-new-engine-work half first.

Framing rules (mirrors ``tail_risk_chart.py``'s conventions):
  - This is ONE iteration (the sweep's biggest 5s burst), not an average
    and not a guarantee — the caption says so explicitly every time.
  - The recovery slope after a dip is shaped by simf's healer coefficient
    model (baseline HPS + a threshold-gated reactive burst, budget-capped),
    not a simulated healer's judgment — named as illustrative, not measured,
    per the 2026-07-28 scoping workflow's engine-feasibility finding.
  - A died sample's curve is truncated at the real death point rather than
    continuing through the engine's cosmetic post-death HP reset (see
    ``core/metrics.py``'s ``SimResult.sample_died`` docstring) — replaying
    subsequent heal/damage events past death would fabricate a recovery
    the tank never had.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HpTrace:
    """A reconstructed HP-over-time curve for one sample iteration."""

    times_s: list[float]
    hp_pct: list[float]
    died: bool
    death_time_s: float | None


def compute_hp_trace(point) -> HpTrace | None:
    """Reconstruct an HP% curve from a ``KeyLevelPoint``'s sample timelines.

    Returns ``None`` when there's nothing plottable (no ``sample_max_hp``,
    or both timelines empty — a stub/legacy point) — callers don't need
    to guard.

    Damage/heal events are already the ENGINE'S applied deltas (heals are
    pre-clamped to available headroom in ``runner.py``; damage is the
    post-mitigation amount actually subtracted from HP), so replaying them
    in time order against ``sample_max_hp`` reconstructs the same curve
    the live sim walked — no re-derivation of mitigation math here.
    """
    if not point.sample_max_hp or point.sample_max_hp <= 0:
        return None
    damage_events = list(getattr(point, "sample_damage_timeline", []) or [])
    heal_events = list(getattr(point, "sample_heal_timeline", []) or [])
    if not damage_events and not heal_events:
        return None

    max_hp = point.sample_max_hp
    died = bool(getattr(point, "sample_died", False))
    death_time_s = getattr(point, "sample_time_to_die_s", None)

    events = [(t, -d) for t, d in damage_events] + [(t, h) for t, h in heal_events]
    events.sort(key=lambda e: e[0])

    times = [0.0]
    hp_pct = [100.0]
    hp = max_hp
    for t, delta in events:
        hp = max(0.0, min(max_hp, hp + delta))
        times.append(t)
        hp_pct.append(hp / max_hp * 100.0)
        if died and hp <= 0.0:
            break

    # Defensive floor: a died sample must visually end at 0 even if float
    # precision in this independent reconstruction never lands on exactly
    # 0 across the recorded events.
    if died and hp_pct[-1] > 0.0:
        final_t = death_time_s if death_time_s is not None else times[-1]
        times.append(max(final_t, times[-1]))
        hp_pct.append(0.0)

    # A surviving sample's last event may land well before the pull ends —
    # extend the line flat to the real duration so the chart spans the
    # whole encounter instead of stopping wherever the last event happened.
    duration_s = getattr(point, "sample_duration_s", 0.0)
    if not died and duration_s and duration_s > times[-1]:
        times.append(duration_s)
        hp_pct.append(hp_pct[-1])

    return HpTrace(times_s=times, hp_pct=hp_pct, died=died, death_time_s=death_time_s)


def render_hp_trace_chart(point, push_key: int) -> None:
    """Render the HP-over-time chart for one ``KeyLevelPoint``.

    No-op when there's nothing plottable (``compute_hp_trace`` returns
    ``None``) — callers don't need to guard.
    """
    import plotly.graph_objects as go
    import streamlit as st

    from .plotly_codex import CODEX_COLORWAY, apply_codex_layout

    trace = compute_hp_trace(point)
    if trace is None:
        return

    st.markdown(f"#### HP over time at +{push_key}")
    st.markdown(
        "The same worst 5-second-burst iteration the burst-risk bars above "
        "describe — one representative pull from this sweep's Monte Carlo "
        "runs, not an average and not every possible outcome."
    )

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=trace.times_s,
            y=trace.hp_pct,
            mode="lines",
            line={"color": CODEX_COLORWAY[0], "width": 2},
            fill="tozeroy",
            fillcolor="rgba(52,85,110,0.12)",
            name="HP",
        )
    )
    if trace.died:
        death_x = trace.death_time_s if trace.death_time_s is not None else trace.times_s[-1]
        fig.add_trace(
            go.Scatter(
                x=[death_x],
                y=[0],
                mode="markers",
                marker={"color": CODEX_COLORWAY[1], "size": 11, "symbol": "x"},
                name="Died",
            )
        )
    fig.update_yaxes(range=[0, 100], title="HP %")
    fig.update_xaxes(title="Seconds into the pull")
    fig.update_layout(height=280, margin={"l": 10, "r": 10, "t": 30, "b": 10}, showlegend=False)
    apply_codex_layout(fig)
    st.plotly_chart(fig, width="stretch", key=f"hp_trace_chart_{push_key}")
    st.caption(
        "One Monte Carlo iteration, not a percentile band across every "
        "simulated pull — read the shape as an example of a bad pull, not "
        "a guarantee. The recovery slope after a dip reflects simf's "
        "simplified healer coefficient model, not your real healer's "
        "judgment calls — trust the lowest point more than the recovery."
    )
