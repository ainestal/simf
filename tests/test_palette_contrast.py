"""WCAG 2.2 AA contrast guard for the Codex palette (a11y pass, 2026-06-14).

The public cold-share surface renders captions/meta in `--text-muted` and
supporting body in `--text-secondary` on the `--surface-base` cream paper.
A review found `--text-muted` had drifted to 3.7:1 (below the 4.5:1 AA floor
for normal text) while a stale comment claimed 6.3:1. This parses the LIVE
token values out of app.py's CSS and recomputes the real contrast, so the
content tokens can't silently regress below AA again.
"""

from __future__ import annotations

import re
from pathlib import Path

APP = (Path(__file__).resolve().parents[1] / "src" / "simf" / "ui" / "app.py").read_text()


def _lin(c: float) -> float:
    c /= 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(rgb: tuple[float, float, float]) -> float:
    r, g, b = rgb
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def _contrast(fg: tuple, bg: tuple) -> float:
    l1, l2 = _luminance(fg), _luminance(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def _hex(token: str) -> tuple[int, int, int]:
    m = re.search(rf"{re.escape(token)}:\s*#([0-9a-fA-F]{{6}})", APP)
    assert m, f"token {token} (#hex) not found in app.py CSS"
    h = m.group(1)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _rgba_over(token: str, bg: tuple[int, int, int]) -> tuple[float, float, float]:
    """Resolve an rgba(r,g,b,a) token alpha-blended over an opaque bg."""
    m = re.search(rf"{re.escape(token)}:\s*rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)", APP)
    assert m, f"token {token} (rgba) not found in app.py CSS"
    r, g, b, a = int(m[1]), int(m[2]), int(m[3]), float(m[4])
    return tuple(fg * a + bg[i] * (1 - a) for i, fg in enumerate((r, g, b)))


def test_content_text_tokens_clear_wcag_aa_on_paper():
    """Every token that carries ESSENTIAL text on the public surface must clear
    4.5:1 against the cream page background."""
    paper = _hex("--surface-base")
    # --text-primary is an opaque hex; the rest are alpha over paper.
    assert _contrast(_hex("--text-primary"), paper) >= 7.0  # AAA — body/headings
    for token in ("--text-secondary", "--text-muted"):
        ratio = _contrast(_rgba_over(token, paper), paper)
        assert ratio >= 4.5, f"{token} is {ratio:.2f}:1 on paper — below WCAG AA (4.5:1)"


def test_text_muted_specifically_passes_aa():
    """Regression for the 2026-06-14 finding: --text-muted (captions/meta) was
    3.7:1. It must stay >= 4.5:1."""
    ratio = _contrast(_rgba_over("--text-muted", _hex("--surface-base")), _hex("--surface-base"))
    assert ratio >= 4.5, f"--text-muted regressed to {ratio:.2f}:1 (need >= 4.5:1)"


def test_warning_banner_text_passes_aa():
    """Round-1 accessibility audit (2026-07-05): st.warning() text measured
    4.24:1 on its own composited background — Streamlit's stock theme color,
    never overridden by this app before, pixel-verified live against a real
    calibration-caveat banner. Below the 4.5:1 AA floor on the one surface
    most likely to carry text a user needs to read correctly. Overridden to
    #7c5b04; this pins it against the exact composited background measured
    live (the tinted alert background over the cream page)."""
    override = _hex('[data-testid="stAlertContentWarning"] { color')
    # The composited background measured live: rgba(255,255,18,0.1) over
    # --surface-base — hardcoded here (not re-derived from the token) since
    # Streamlit owns that background color, not this app's palette.
    composited_bg = (245, 242, 212)
    ratio = _contrast(override, composited_bg)
    assert ratio >= 4.5, f"warning-banner text is {ratio:.2f}:1 (need >= 4.5:1)"
