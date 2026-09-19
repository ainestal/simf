"""Codex-palette adapter for Plotly figures.

Every Plotly figure in simf — pie chart of damage-by-school, Pareto
scatter in the slot dialog, rage timeline — defaulted to Plotly Express'
qualitative palette (bright primary colors tuned for white dashboards).
On the Codex `--surface-base #f4f1ea` cream paper background those reads
as discordant — bright cyan / orange / lime against a warm earth-tone
surround.

This module supplies the Codex two-accent palette and an
``apply_codex_layout(fig)`` helper so every figure site can land the
same earth-tone chrome in one call. The colorway is the SPEC's
committed pair (steel + bronze, see `ui-revamp/phase-2/SPEC.md` §1)
plus a small set of opacity tints derived from those two — so a >2-
series chart degrades to readable shades of the same hue rather than
inventing third / fourth / fifth accents the design study never picked.

For damage-by-school pies with up to 8 schools, the Codex answer is to
stack physical vs. magical (one steel, one bronze) rather than reach
for new hues. Where a multi-series fallback is unavoidable, opacity
tints off steel give visual differentiation without breaking the
two-accent semantic.

Contrast: both accents meet WCAG 1.4.11 (3:1) against ``#f4f1ea``
for non-text graphical elements. Verified in
``tests/test_plotly_codex.py``.

Reduced motion: the app-wide `prefers-reduced-motion` CSS guard
(``simf.ui.css._render_reduced_motion_css``) only reaches CSS
``transition``/``animation`` properties — it does NOT reach Plotly's
own animation engine (``layout.transition``, ``animation_frame``),
which Plotly drives via its own JS, not CSS. No figure built through
this module uses that today; if a future chart adds
``fig.update_layout(transition=...)``, it needs its own
reduced-motion check independent of the CSS guard.
"""

from __future__ import annotations

# Codex committed accents — `ui-revamp/phase-2/SPEC.md` §1 + tokens.css.
# Steel = survival / eHP up / mitigation. Bronze = death / eHP down /
# damage. No third accent. Plotly assigns colorway[0] to the first
# series; both ends of the semantic axis are visible in any two-series
# chart.
CODEX_COLORWAY: tuple[str, ...] = (
    "#34556e",  # steel — accent-gold / accent-good (survival, eHP up)
    "#7a3d2c",  # bronze — accent-warn (death, eHP down, damage)
)


def codex_tints(base: str, n: int) -> tuple[str, ...]:
    """Emit `n` opacity tints off a Codex accent — for charts with more
    than 2 categories where the design answer (stack physical vs.
    magical) isn't available. Returns rgba strings ramping from full
    opacity down to ~30%, so successive series degrade to readable
    shades of the same hue rather than to invented earth-tones.

    Example: ``codex_tints("#34556e", 4)`` →
        ("rgba(52,85,110,1.0)", "rgba(52,85,110,0.77)",
         "rgba(52,85,110,0.54)", "rgba(52,85,110,0.31)")
    """
    if n <= 0:
        return ()
    h = base.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    if n == 1:
        return (f"rgba({r},{g},{b},1.0)",)
    # Linear ramp from 1.0 down to 0.31 (3:1 contrast floor against paper).
    step = (1.0 - 0.31) / (n - 1)
    return tuple(f"rgba({r},{g},{b},{round(1.0 - step * i, 2)})" for i in range(n))


# Codex surface + text token mirrors. Kept in sync with the CSS tokens
# in `src/simf/ui/app.py` :root block — if those move, update both.
_CODEX_PAPER = "#f4f1ea"
_CODEX_INK = "#1a1d22"
_CODEX_INK_55 = "rgba(26, 29, 34, 0.55)"
_CODEX_HAIRLINE = "rgba(26, 29, 34, 0.14)"


def apply_codex_layout(fig) -> None:
    """Apply the Codex color palette + paper/ink chrome to a Plotly figure.

    Sets:
      • ``layout.colorway`` to ``CODEX_COLORWAY`` so default series picks
        land on the steel/bronze pair.
      • ``layout.paper_bgcolor`` + ``layout.plot_bgcolor`` to the Codex
        paper tone so the chart blends with the page (no white card
        glowing out of the cream surround).
      • ``layout.font.color`` to ink and ``layout.font.family`` to the
        system stack matching the page body.
      • Axis line/grid/tick colors to the same hairline + ink tokens
        the page rules use.

    Idempotent — calling twice produces the same layout. The caller
    invokes this AFTER constructing the figure and BEFORE handing it
    to ``st.plotly_chart``. Edits ``fig`` in place; return value is the
    same figure for fluent chaining.
    """
    fig.update_layout(
        colorway=list(CODEX_COLORWAY),
        paper_bgcolor=_CODEX_PAPER,
        plot_bgcolor=_CODEX_PAPER,
        font={
            "color": _CODEX_INK,
            "family": (
                "-apple-system, BlinkMacSystemFont, 'Segoe UI', "
                "Roboto, 'Helvetica Neue', Arial, sans-serif"
            ),
        },
    )
    fig.update_xaxes(
        linecolor=_CODEX_HAIRLINE,
        gridcolor=_CODEX_HAIRLINE,
        tickcolor=_CODEX_HAIRLINE,
        zerolinecolor=_CODEX_HAIRLINE,
    )
    fig.update_yaxes(
        linecolor=_CODEX_HAIRLINE,
        gridcolor=_CODEX_HAIRLINE,
        tickcolor=_CODEX_HAIRLINE,
        zerolinecolor=_CODEX_HAIRLINE,
    )
    return fig
