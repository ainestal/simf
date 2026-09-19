"""Disposition ledger UI helper — pure-data tests.

Pins the pure math (`compute_disposition_segments` /
`compute_healed_back_shares`). Render-layer coverage is a live
Streamlit screenshot check (see the PR description) — mirrors the
`tail_risk_chart` helper's test split (`test_tail_risk_chart.py`).
"""

from __future__ import annotations

from dataclasses import dataclass

from simf.ui.helpers.disposition_ledger import (
    DispositionSegment,
    compute_disposition_segments,
    compute_healed_back_shares,
)


@dataclass
class _FakeResult:
    """Minimal SimResult-shape stub — only the fields the ledger reads."""

    disposition_instrumented: bool = False
    disposition_avoided_share: float = 0.0
    disposition_blocked_share: float = 0.0
    disposition_armor_share: float = 0.0
    disposition_vers_share: float = 0.0
    disposition_dr_layers_share: float = 0.0
    disposition_absorbed_ip_share: float = 0.0
    disposition_absorbed_healer_share: float = 0.0
    disposition_dealt_share: float = 0.0
    disposition_healed_self_share: float = 0.0
    disposition_healed_external_share: float = 0.0


def test_empty_when_not_instrumented():
    """Non-warrior specs must render nothing, not a bar of structural
    zeros — a zero-filled bar would misrepresent absence-of-measurement
    as a real 'this build mitigates nothing' finding."""
    result = _FakeResult(disposition_instrumented=False, disposition_dealt_share=1.0)
    assert compute_disposition_segments(result) == []
    assert compute_healed_back_shares(result) is None


def test_segments_ordered_and_labeled():
    result = _FakeResult(
        disposition_instrumented=True,
        disposition_avoided_share=0.05,
        disposition_blocked_share=0.10,
        disposition_armor_share=0.20,
        disposition_vers_share=0.05,
        disposition_dr_layers_share=0.15,
        disposition_absorbed_ip_share=0.05,
        disposition_absorbed_healer_share=0.05,
        disposition_dealt_share=0.35,
    )
    segments = compute_disposition_segments(result)
    assert [s.key for s in segments] == [
        "avoided",
        "blocked",
        "armor",
        "vers",
        "dr_layers",
        "absorbed_ip",
        "absorbed_healer",
        "dealt",
    ]
    # Every segment carries a real label (no blank/placeholder strings).
    assert all(s.label for s in segments)
    # Every segment carries a color string (hex or rgba) suitable for
    # a Plotly marker_color.
    assert all(s.color for s in segments)


def test_segments_shares_sum_to_one():
    """Mirrors the engine-side conservation invariant one layer up — the
    8 shares SimResult reports must still sum to ~1.0 by the time they
    reach the UI (a UI-side unit-mismatch bug would break this even if
    the engine test suite is clean)."""
    result = _FakeResult(
        disposition_instrumented=True,
        disposition_avoided_share=0.12,
        disposition_blocked_share=0.08,
        disposition_armor_share=0.30,
        disposition_vers_share=0.05,
        disposition_dr_layers_share=0.10,
        disposition_absorbed_ip_share=0.03,
        disposition_absorbed_healer_share=0.02,
        disposition_dealt_share=0.30,
    )
    segments = compute_disposition_segments(result)
    assert abs(sum(s.share for s in segments) - 1.0) < 1e-9


def test_dealt_segment_uses_bronze_damage_accent():
    from simf.ui.helpers.plotly_codex import CODEX_COLORWAY

    result = _FakeResult(disposition_instrumented=True, disposition_dealt_share=1.0)
    segments = compute_disposition_segments(result)
    dealt = next(s for s in segments if s.key == "dealt")
    assert dealt.color == CODEX_COLORWAY[1]


def test_healed_back_shares_pass_through_when_instrumented():
    result = _FakeResult(
        disposition_instrumented=True,
        disposition_healed_self_share=0.1,
        disposition_healed_external_share=0.6,
    )
    assert compute_healed_back_shares(result) == (0.1, 0.6)


# ─── Hue-separation fix (round-1 triage item 2) ───────────────────────────


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    # WCAG 2.2 §1.4 relative-luminance formula — same formula
    # `tests/test_plotly_codex.py` holds CODEX_COLORWAY to.
    def chan(c: int) -> float:
        s = c / 255.0
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)


def _contrast(a: str, b: str) -> float:
    la, lb = _relative_luminance(_hex_to_rgb(a)), _relative_luminance(_hex_to_rgb(b))
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _rgb_distance(a: str, b: str) -> float:
    ra, ga, ba = _hex_to_rgb(a)
    rb, gb, bb = _hex_to_rgb(b)
    return ((ra - rb) ** 2 + (ga - gb) ** 2 + (ba - bb) ** 2) ** 0.5


def test_reduction_colors_are_seven_distinct_hexes():
    from simf.ui.helpers.disposition_ledger import _REDUCTION_COLORS

    assert len(_REDUCTION_COLORS) == 7
    assert len(set(_REDUCTION_COLORS)) == 7, "duplicate colors among the 7 reduction buckets"


def test_reduction_colors_are_not_a_single_hue_opacity_ramp():
    """The bug this fix closes: `codex_tints(base, 7)` emits ONE hue at 7
    alpha steps — every entry shares the exact same (r, g, b) ratios,
    only opacity differs. Real hue separation means the hue angle must
    actually vary across entries, not just lightness/opacity."""
    import colorsys

    from simf.ui.helpers.disposition_ledger import _REDUCTION_COLORS

    hues = []
    for hexcol in _REDUCTION_COLORS:
        r, g, b = (c / 255.0 for c in _hex_to_rgb(hexcol))
        h, _l, _s = colorsys.rgb_to_hls(r, g, b)
        hues.append(h * 360)

    hue_spread = max(hues) - min(hues)
    assert hue_spread > 10.0, (
        f"reduction-bucket hues span only {hue_spread:.1f} degrees — "
        "reads as one hue with brightness/opacity steps, not real hue separation"
    )


def test_reduction_colors_stay_in_the_steel_family_not_bronze():
    """Preserves the module's two-accent design language: reduction layers
    must read as cool/steel tones, clearly distinct from the warm bronze
    'Landed as damage' accent — never close enough to be confused with it."""
    from simf.ui.helpers.disposition_ledger import _REDUCTION_COLORS
    from simf.ui.helpers.plotly_codex import CODEX_COLORWAY

    bronze = CODEX_COLORWAY[1]
    for hexcol in _REDUCTION_COLORS:
        assert _rgb_distance(hexcol, bronze) > 60.0, (
            f"{hexcol} sits too close to the bronze accent {bronze} — "
            "could be misread as the damage segment"
        )


def test_reduction_colors_each_clear_wcag_contrast_floor_on_paper():
    """Every reduction-bucket color independently meets the same 3:1 WCAG
    1.4.11 non-text contrast floor `CODEX_COLORWAY` itself is held to
    (`tests/test_plotly_codex.py::test_colorway_contrast_on_paper`)."""
    from simf.ui.helpers.disposition_ledger import _REDUCTION_COLORS

    paper = "#f4f1ea"
    failures = [
        (color, round(_contrast(color, paper), 2))
        for color in _REDUCTION_COLORS
        if _contrast(color, paper) < 3.0
    ]
    assert not failures, f"low-contrast reduction colors vs paper {paper}: {failures}"


def test_segments_use_reduction_colors_in_order():
    from simf.ui.helpers.disposition_ledger import _REDUCTION_COLORS

    result = _FakeResult(
        disposition_instrumented=True,
        disposition_avoided_share=1 / 8,
        disposition_blocked_share=1 / 8,
        disposition_armor_share=1 / 8,
        disposition_vers_share=1 / 8,
        disposition_dr_layers_share=1 / 8,
        disposition_absorbed_ip_share=1 / 8,
        disposition_absorbed_healer_share=1 / 8,
        disposition_dealt_share=1 / 8,
    )
    segments = compute_disposition_segments(result)
    reduction_colors = [s.color for s in segments if s.key != "dealt"]
    assert reduction_colors == list(_REDUCTION_COLORS)


# ─── Legend order fix (round-1 triage item 3) ─────────────────────────────


def test_stacked_bar_legend_reads_top_to_bottom_in_chain_order(monkeypatch):
    """Plotly's implicit default for `barmode="stack"` reverses legend
    order relative to trace-add order — correct for a vertical stack read
    bottom-up, backwards for this horizontal bar's left-to-right chain
    read. `_render_stacked_bar` must pin `legend.traceorder="normal"` so
    the legend lists avoided -> ... -> dealt in the same order the bar
    does."""
    import sys

    import simf.ui.helpers.disposition_ledger as dl

    captured: dict = {}

    class _FakeSt:
        @staticmethod
        def plotly_chart(fig, **kw):
            captured["fig"] = fig

    monkeypatch.setitem(sys.modules, "streamlit", _FakeSt)

    result = _FakeResult(
        disposition_instrumented=True,
        disposition_avoided_share=0.5,
        disposition_dealt_share=0.5,
    )
    segments = compute_disposition_segments(result)
    dl._render_stacked_bar(segments, key_level=15)

    fig = captured.get("fig")
    assert fig is not None, "_render_stacked_bar did not call st.plotly_chart"
    assert fig.layout.legend.traceorder == "normal"
    # Traces themselves must still be added in chain order (the fix
    # relies on this to make "normal" mean the right thing). Names go
    # through `_legend_label` now (2026-07-10 share-suffix fix), not the
    # bare `s.label`.
    assert [t.name for t in fig.data] == [dl._legend_label(s) for s in segments]


# ─── Legend share-suffix + caption-copy fixes (2026-07-10 readability workshop) ──


def _seg(share: float, label: str = "Avoided") -> DispositionSegment:
    return DispositionSegment(key="avoided", label=label, share=share, color="#182449")


def test_legend_label_bare_below_threshold():
    from simf.ui.helpers.disposition_ledger import _MIN_LABELED_SHARE, _legend_label

    seg = _seg(_MIN_LABELED_SHARE - 0.001)
    assert _legend_label(seg) == seg.label
    assert "%" not in _legend_label(seg)


def test_legend_label_includes_rounded_percent_at_threshold_and_above():
    from simf.ui.helpers.disposition_ledger import _MIN_LABELED_SHARE, _legend_label

    seg = _seg(_MIN_LABELED_SHARE)
    assert _legend_label(seg) == "Avoided · 5%"

    seg_big = _seg(0.234, label="Armor")
    assert _legend_label(seg_big) == "Armor · 23%"


def test_render_stacked_bar_uses_legend_label_for_every_trace(monkeypatch):
    """End-to-end check that `_render_stacked_bar` actually calls
    `_legend_label` per segment rather than some other formatting —
    guards against the helper existing but not being wired in."""
    import sys

    import simf.ui.helpers.disposition_ledger as dl

    captured: dict = {}

    class _FakeSt:
        @staticmethod
        def plotly_chart(fig, **kw):
            captured["fig"] = fig

    monkeypatch.setitem(sys.modules, "streamlit", _FakeSt)

    result = _FakeResult(
        disposition_instrumented=True,
        disposition_avoided_share=0.5,
        disposition_dealt_share=0.5,
    )
    segments = compute_disposition_segments(result)
    dl._render_stacked_bar(segments, key_level=None)
    fig = captured["fig"]
    names = [t.name for t in fig.data]
    assert names == [dl._legend_label(s) for s in segments]
    # And concretely: the two non-zero segments actually carry their
    # rounded percent suffix (not just "whatever `_legend_label` says").
    assert "Avoided · 50%" in names
    assert "Landed as damage · 50%" in names


def test_what_this_shows_caption_is_a_single_sentence_at_a_key_level():
    from simf.ui.helpers.disposition_ledger import _what_this_shows_caption

    caption = _what_this_shows_caption(15)
    # 2 periods total: one closing the bolded "What this shows." label,
    # one closing the single content sentence after it — never a second
    # content sentence tacked on.
    assert caption.count(".") == 2
    assert "At +15" in caption
    assert "left to right" in caption
    # The multiplicative-chain caveat and the not-a-log-read disclaimer
    # must NOT be in the always-shown caption anymore — they moved to
    # `_FOR_THE_CURIOUS_HTML`.
    assert "3-5x" not in caption
    assert "Why did I die" not in caption


def test_what_this_shows_caption_glosses_calibration_baseline_inline():
    from simf.ui.helpers.disposition_ledger import _what_this_shows_caption

    caption = _what_this_shows_caption(None)
    assert "calibration baseline" in caption
    # The gloss must sit in the SAME sentence as the term, not merely
    # exist somewhere else on the page.
    assert "calibration baseline —" in caption or "calibration baseline -" in caption


def test_for_the_curious_html_carries_the_demoted_caveats():
    from simf.ui.helpers.disposition_ledger import _FOR_THE_CURIOUS_HTML

    assert "<details" in _FOR_THE_CURIOUS_HTML
    assert "<summary" in _FOR_THE_CURIOUS_HTML
    assert "3-5x" in _FOR_THE_CURIOUS_HTML
    assert "not a read of your own combat logs" in _FOR_THE_CURIOUS_HTML
    assert "Why did I die" in _FOR_THE_CURIOUS_HTML


def test_for_the_curious_html_is_not_a_real_st_expander():
    """A real `st.expander` can't nest inside the outer key-level-verdict
    expander this panel already renders inside — must be raw `<details>`,
    never a second `st.expander(...)` CALL in the source. Checked as
    ``"st.expander("`` (with the opening paren) rather than a bare
    substring, since the module's own docstring/comments mention
    ``st.expander`` by name (without calling it) to explain why."""
    import inspect

    import simf.ui.helpers.disposition_ledger as dl

    source = inspect.getsource(dl)
    assert "st.expander(" not in source


def test_provenance_caption_names_real_iterations_and_seed():
    from simf.ui.helpers.disposition_ledger import (
        _LEDGER_ITERATIONS,
        _LEDGER_SEED,
        _provenance_caption,
    )

    caption = _provenance_caption()
    assert str(_LEDGER_ITERATIONS) in caption
    assert str(_LEDGER_SEED) in caption
