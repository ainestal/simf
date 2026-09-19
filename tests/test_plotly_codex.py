"""Tests for the Codex Plotly palette adapter.

Pins the contract that every Plotly figure simf renders uses the
Codex earth-tone colorway + paper-toned layout chrome, not the Plotly
Express defaults that clash with the cream `--surface-base` page.

Three layers:
  1. ``CODEX_COLORWAY`` content invariants — distinct hexes, 3:1
     contrast against the paper tone (WCAG 1.4.11 non-text).
  2. ``apply_codex_layout`` idempotence + property assignments.
  3. Call-site wiring — pareto scatter and the damage-by-school pie
     both pull the colorway and the layout helper, with no Plotly
     defaults leaking through.
"""

from __future__ import annotations

import re

import plotly.graph_objects as go

from simf.ui.helpers.plotly_codex import CODEX_COLORWAY, apply_codex_layout

_CODEX_PAPER = "#f4f1ea"


# ─── 1. Colorway content invariants ───────────────────────────────────────────


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    # WCAG 2.2 §1.4 relative-luminance formula.
    def chan(c: int) -> float:
        s = c / 255.0
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)


def _contrast(a: str, b: str) -> float:
    la, lb = _relative_luminance(_hex_to_rgb(a)), _relative_luminance(_hex_to_rgb(b))
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def test_colorway_matches_spec_two_accents():
    """`ui-revamp/phase-2/SPEC.md` §1 committed to exactly two accents:
    steel (#34556e) and bronze (#7a3d2c). The earlier 8-hue extension
    (slate / ochre / forest / plum / sand / ink) was reverted on
    2026-05-25 because the invented earth-tones diluted the steel/
    bronze semantic. >2-series charts use ``codex_tints()`` opacity
    ramps off one of the two accents — see `codex_tints` docstring."""
    assert len(CODEX_COLORWAY) == 2, (
        f"SPEC committed to 2 accents (steel + bronze); colorway has {len(CODEX_COLORWAY)}"
    )
    hex_re = re.compile(r"^#[0-9a-fA-F]{6}$")
    for color in CODEX_COLORWAY:
        assert hex_re.match(color), f"not a 6-hex color: {color!r}"
    assert len(set(CODEX_COLORWAY)) == len(CODEX_COLORWAY), (
        f"duplicates in CODEX_COLORWAY: {CODEX_COLORWAY}"
    )


def test_colorway_contrast_on_paper():
    """WCAG 1.4.11 — non-text graphical elements need ≥3:1 contrast
    against the adjacent surface. Every series color must meet this
    against the Codex paper tone."""
    failures = []
    for color in CODEX_COLORWAY:
        c = _contrast(color, _CODEX_PAPER)
        if c < 3.0:
            failures.append((color, round(c, 2)))
    assert not failures, f"low-contrast colors vs paper {_CODEX_PAPER}: {failures}"


def test_colorway_anchor_pair_matches_accent_tokens():
    """The first two colorway entries must match the CSS accent tokens
    (`--accent-gold` steel + `--accent-warn` bronze) verbatim — the
    page already trains the user to read steel = survival accent and
    bronze = damage / warn. Chart series picks should land on the same
    pair."""
    assert CODEX_COLORWAY[0].lower() == "#34556e", "anchor[0] must be steel #34556e"
    assert CODEX_COLORWAY[1].lower() == "#7a3d2c", "anchor[1] must be bronze #7a3d2c"


# ─── 2. apply_codex_layout property assignments ──────────────────────────────


def test_apply_codex_layout_sets_colorway_and_paper():
    fig = go.Figure()
    apply_codex_layout(fig)
    assert tuple(fig.layout.colorway) == CODEX_COLORWAY, (
        f"colorway not applied: {fig.layout.colorway!r}"
    )
    assert fig.layout.paper_bgcolor == _CODEX_PAPER
    assert fig.layout.plot_bgcolor == _CODEX_PAPER


def test_apply_codex_layout_sets_font():
    fig = go.Figure()
    apply_codex_layout(fig)
    assert fig.layout.font.color == "#1a1d22"
    assert "Helvetica" in (fig.layout.font.family or "") or "system" in (
        fig.layout.font.family or ""
    )


def test_apply_codex_layout_is_idempotent():
    """Calling twice produces the same layout — important because
    rage-timeline applies it AFTER ``fig.update_layout`` and pareto
    scatter applies it after a separate `update_layout(margin=…)` —
    neither path should snowball settings if the helper is called more
    than once across the session lifecycle."""
    fig = go.Figure()
    apply_codex_layout(fig)
    first = fig.to_dict()["layout"]
    apply_codex_layout(fig)
    second = fig.to_dict()["layout"]
    assert first == second, "apply_codex_layout is not idempotent"


def test_apply_codex_layout_returns_figure_for_chaining():
    fig = go.Figure()
    returned = apply_codex_layout(fig)
    assert returned is fig, "apply_codex_layout should return the same figure object"


# ─── 3. Call-site wiring ──────────────────────────────────────────────────────


def test_every_st_plotly_chart_call_has_codex_layout():
    """Every ``st.plotly_chart(fig, ...)`` invocation across the UI
    must be preceded by ``apply_codex_layout(fig)`` so no figure renders
    with Plotly's default white background against the cream paper page.

    Source-text pin rather than runtime introspection — the pie + rage
    charts only render with a parsed log loaded (heavy AppTest fixture),
    so the wiring tripwire grep-based for cheap regression coverage. If
    a future refactor drops one of the helper calls, this test catches
    it without needing the full log-replay path."""
    from pathlib import Path

    ui_dir = Path(__file__).parent.parent / "src" / "simf" / "ui"
    chart_files = list(ui_dir.rglob("*.py"))
    offenders: list[str] = []
    for path in chart_files:
        src = path.read_text()
        if "st.plotly_chart(" not in src:
            continue
        # Crude but effective: for every plotly_chart call, the same
        # file must mention apply_codex_layout at least as many times
        # (one helper call per chart, idempotence covers double-applies).
        n_charts = src.count("st.plotly_chart(")
        n_layouts = src.count("apply_codex_layout(")
        if n_layouts < n_charts:
            offenders.append(
                f"{path.relative_to(ui_dir.parent.parent.parent)}: "
                f"{n_charts} st.plotly_chart calls but only "
                f"{n_layouts} apply_codex_layout calls"
            )
    assert not offenders, (
        "Plotly figures rendering without the Codex layout helper:\n  " + "\n  ".join(offenders)
    )


def test_pie_chart_uses_codex_tints():
    """``px.pie`` ignores ``layout.colorway`` — slice colors come from
    ``marker.colors`` instead. Post-2026-05-25 revert: the pie no
    longer reaches for 8 invented hues; it uses ``codex_tints`` to
    emit opacity ramps off the steel accent so successive slices read
    as the same hue at decreasing weight."""
    from pathlib import Path

    # The damage-by-school pie moved to log_analysis.py in the log_view split.
    src = (Path(__file__).parent.parent / "src" / "simf" / "ui" / "log_analysis.py").read_text()
    pie_section_start = src.find('px.pie(school_df, names="School"')
    assert pie_section_start >= 0, "damage-by-school pie chart moved or vanished"
    pie_section = src[pie_section_start : pie_section_start + 800]
    assert "codex_tints" in pie_section, (
        "pie chart must use codex_tints() for slice colors — the 8-hue "
        "CODEX_COLORWAY extension was reverted on 2026-05-25"
    )


def test_codex_tints_anchors_at_full_opacity_and_ramps_down():
    """`codex_tints(base, n)` must emit `n` rgba values starting at
    full opacity (slot 0 = the largest slice / first series) and ramp
    down to ~30% (the WCAG 1.4.11 floor against paper). Single-slot
    case returns a single full-opacity tint."""
    from simf.ui.helpers.plotly_codex import codex_tints

    assert codex_tints("#34556e", 0) == ()
    assert codex_tints("#34556e", 1) == ("rgba(52,85,110,1.0)",)
    tints = codex_tints("#34556e", 4)
    assert len(tints) == 4
    assert tints[0] == "rgba(52,85,110,1.0)", "first tint must be full opacity"
    # Final tint must be ≥ 0.30 alpha (the 3:1 contrast floor).
    final_alpha = float(tints[-1].rsplit(",", 1)[1].rstrip(")"))
    assert final_alpha >= 0.30, (
        f"final tint {tints[-1]} drops below the 0.30 alpha floor — would "
        f"violate WCAG 1.4.11 against #f4f1ea paper"
    )


def test_pareto_scatter_uses_codex_palette(monkeypatch):
    """Pareto-scatter helper must pull the Codex steel/slate pair —
    not the previous hardcoded Plotly blue + light grey."""
    import simf.ui.helpers.pareto_scatter as ps

    captured: dict = {}

    class _FakeExpander:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _FakeSt:
        @staticmethod
        def expander(*a, **kw):
            return _FakeExpander()

        @staticmethod
        def plotly_chart(fig, **kw):
            captured["fig"] = fig

        @staticmethod
        def caption(*a, **kw):
            pass

    # Monkey-patch streamlit import inside the helper's local scope.
    import sys

    monkeypatch.setitem(sys.modules, "streamlit", _FakeSt)

    from simf.optimizer.alternatives import Alternative

    @dataclass_skel
    class _FakeItem:
        item_id: int = 1
        name: str = "X"
        ilvl: int = 100

    alts = [
        Alternative(
            item=_FakeItem(),
            source="bag",
            per_dungeon=[],
            avg_delta_ehp=10.0,
            delta_dps=0.5,
        ),
        Alternative(
            item=_FakeItem(item_id=2, name="Y"),
            source="vault",
            per_dungeon=[],
            avg_delta_ehp=5.0,
            delta_dps=-0.2,
        ),
    ]
    ps.render_pareto_scatter(alts, slot_label="head")
    fig = captured.get("fig")
    assert fig is not None, "render_pareto_scatter did not call plotly_chart"
    # The Codex layout helper must have run — paper_bgcolor is the tell.
    assert fig.layout.paper_bgcolor == _CODEX_PAPER, "Codex layout not applied to pareto scatter"
    # Frontier + dominated cloud must both be steel-derived (full steel
    # for frontier, low-alpha steel for dominated) — i.e., RGB
    # components match the steel accent (52, 85, 110). Asserts the
    # post-2026-05-25 codex_tints() retreat from the 8-hue extension.
    series_colors = {trace.marker.color for trace in fig.data if hasattr(trace, "marker")}
    steel_rgb = "52,85,110"
    assert all(steel_rgb in c.replace(" ", "") for c in series_colors if isinstance(c, str)), (
        f"pareto scatter series colors must be steel-derived (codex_tints off CODEX_COLORWAY[0]); "
        f"got {series_colors!r}"
    )


def dataclass_skel(cls):
    """Tiny dataclass-like decorator — we don't want the test importing
    `dataclass` separately; the fake item just needs `__init__` accepting
    kwargs to spoof an ItemSpec for the Alternative.item field."""

    orig_init = cls.__init__ if "__init__" in cls.__dict__ else None

    def __init__(self, **kw):
        for k, v in cls.__dict__.items():
            if not k.startswith("_") and not callable(v):
                setattr(self, k, v)
        for k, v in kw.items():
            setattr(self, k, v)
        if orig_init is not None:
            orig_init(self)

    cls.__init__ = __init__
    return cls
