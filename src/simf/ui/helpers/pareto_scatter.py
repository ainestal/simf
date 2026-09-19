"""Phase 6.4 — Pareto scatter of slot-dialog alternatives.

Each slot the user opens already produces a ranked list of
``Alternative`` rows with both ``avg_delta_ehp`` (survivability) and
``delta_dps`` (DPS) attached. The Pareto scatter is the
visualisation surface for that same data: x = ΔDPS, y = ΔeHP, one
point per candidate, with the Pareto frontier highlighted so the
user can instantly see the swaps that aren't dominated by another
on both axes.

The chart is a *complement* to the existing list, not a replacement.
It lives inside a default-closed expander in the slot dialog so the
default view stays exactly as Brutoh / engaged_tank / novice_tank
all already see it; opening the expander is opt-in for power users
who want the bird's-eye trade-off picture.

Two pure-data functions (no Streamlit imports) own the math so they
can be unit-tested without spinning up an AppTest. The render
function is thin Plotly wiring around the math.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParetoPoint:
    """One alternative laid out for the scatter.

    ``label`` is the row caption shown in the existing alternatives
    list — usually item name + ilvl. ``source`` mirrors the row's
    source tag (``bag`` / ``vault`` / ``m+ Algaz``). The two deltas
    are the same fields ``Alternative`` exposes, copied here so the
    helper has no dependency on the optimizer module shape.
    """

    label: str
    source: str
    delta_ehp: float
    delta_dps: float


def compute_pareto_frontier(points: list[ParetoPoint]) -> list[ParetoPoint]:
    """Return the subset of ``points`` that lies on the Pareto frontier.

    A point ``p`` is on the frontier when no other point dominates it —
    no other ``q`` satisfies ``q.delta_ehp >= p.delta_ehp`` AND
    ``q.delta_dps >= p.delta_dps`` with strict inequality on at least
    one axis.

    Ties (two points with identical (ΔeHP, ΔDPS)) both stay on the
    frontier — the strict-inequality clause prevents either from
    knocking the other off. This is the right semantic for swap
    options: two items with the same delta profile but different
    labels (one from bag, one from a M+ drop) are both legitimate
    picks; the user picks based on availability, not the chart.

    Returned points are sorted by ΔeHP descending so the caller can
    render the frontier as an upper-right hull line and the highest-
    eHP frontier pick reads first.
    """
    if not points:
        return []
    frontier: list[ParetoPoint] = []
    for p in points:
        dominated = False
        for q in points:
            if q is p:
                continue
            if (
                q.delta_ehp >= p.delta_ehp
                and q.delta_dps >= p.delta_dps
                and (q.delta_ehp > p.delta_ehp or q.delta_dps > p.delta_dps)
            ):
                dominated = True
                break
        if not dominated:
            frontier.append(p)
    frontier.sort(key=lambda p: (-p.delta_ehp, -p.delta_dps))
    return frontier


def alternatives_to_points(alts: list) -> list[ParetoPoint]:
    """Adapter: convert ``optimizer.alternatives.Alternative`` rows
    into the chart-friendly ``ParetoPoint`` shape.

    Kept separate from the frontier math so the helper module
    doesn't import from ``optimizer`` — keeps the dependency arrow
    pointing the right way (UI → optimizer, never UI → optimizer →
    UI) and lets the frontier algorithm be re-used for other
    candidate sources (vault rows, trinket-swap candidates) later.
    """
    points: list[ParetoPoint] = []
    for a in alts:
        item = a.item
        label = getattr(item, "name", None) or f"item:{getattr(item, 'item_id', '?')}"
        ilvl = getattr(item, "ilvl", None)
        if ilvl:
            label = f"{label} ({ilvl})"
        points.append(
            ParetoPoint(
                label=label,
                source=a.source,
                delta_ehp=float(a.avg_delta_ehp),
                delta_dps=float(a.delta_dps),
            )
        )
    return points


def render_pareto_scatter(alts: list, slot_label: str) -> None:
    """Render the Pareto scatter for a slot's alternatives.

    Wraps the math in a default-closed ``st.expander`` so the slot
    dialog's primary affordance stays the ranked list above. Opens
    a Plotly scatter when expanded: x = ΔDPS, y = ΔeHP, hover label =
    item name + source. Frontier points get a brighter color +
    larger marker; dominated points are muted but still rendered so
    the user can see the cloud the frontier sits on.

    Lives in ``ui/helpers/`` rather than inline in ``app.py`` so
    the Plotly import isn't paid by every other surface and the
    component is unit-testable via AppTest by importing one symbol.
    """
    import plotly.express as px
    import streamlit as st

    from .plotly_codex import CODEX_COLORWAY, apply_codex_layout, codex_tints

    points = alternatives_to_points(alts)
    if len(points) < 2:
        return  # one point doesn't tell a story; skip the chart entirely

    with st.expander(
        f"View as scatter — ΔDPS vs ΔeHP ({slot_label})",
        expanded=False,
    ):
        frontier = compute_pareto_frontier(points)
        frontier_labels = {p.label for p in frontier}
        rows = [
            {
                "label": p.label,
                "source": p.source,
                "ΔDPS": p.delta_dps,
                "ΔeHP": p.delta_ehp,
                "on_frontier": p.label in frontier_labels,
            }
            for p in points
        ]
        # Codex committed to two accents only — steel (survival) and
        # bronze (damage). Frontier picks anchor at full steel; the
        # dominated cloud uses a steel tint (~31% alpha — the WCAG
        # 1.4.11 floor against paper) so both clusters read as the same
        # hue at different weights, not as different accents (which
        # would imply different semantics).
        frontier_steel, dominated_steel = codex_tints(CODEX_COLORWAY[0], 2)
        fig = px.scatter(
            rows,
            x="ΔDPS",
            y="ΔeHP",
            color="on_frontier",
            hover_data=["label", "source"],
            color_discrete_map={True: frontier_steel, False: dominated_steel},
            labels={"on_frontier": "Pareto frontier"},
        )
        # Origin lines so "Δ < 0" reads as visually-below-the-axis,
        # not just "small number on the y-axis."
        fig.add_hline(y=0, line_color="#666", line_width=1, opacity=0.4)
        fig.add_vline(x=0, line_color="#666", line_width=1, opacity=0.4)
        fig.update_layout(height=360, margin=dict(l=10, r=10, t=10, b=10))
        apply_codex_layout(fig)
        st.plotly_chart(fig, width="stretch")
        st.caption(
            "Each dot is a swap candidate; the upper-right cloud is your "
            "Pareto frontier — picks no other candidate dominates on both "
            "axes. Hover for the item name and source."
        )
