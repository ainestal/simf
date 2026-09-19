"""Tests for the "what this build asks of your healer" headline callout
(Batch C, 2026-07-07 lhf pass — see ROADMAP.md).

The key-level verdict headline is 100% about damage-stream survivability
and never mentions the healer. This callout anchors ``mean_dtps`` at the
SAME key level the headline just claimed.

IMPORTANT — two scope changes from the original ask, both deliberate:

1. ROADMAP's Batch C item asked for this number to be compared against
   the measured healer-throughput reference rate from the token-bucket
   healer cap (PR #288, ``m+_high_key_healer.yaml``'s
   ``healer_budget_refill_pct_of_max_hp_per_s``). That comparison was
   built, then DROPPED after an empirical check — running the actual
   sim, not just reading the YAML/docstrings — showed ``mean_hrps``
   (the number the comparison was built around) isn't comparable to
   that reference rate at all: it nets out not just the tank's
   self-heals but ALSO the sim's own modeled healer output (baseline
   HPS + reactive bursts, both folded into
   ``IterationResult.healing_total`` since the engine's first commit).

2. The FIRST version of this callout still used ``mean_hrps`` for the
   headline number itself, just without the comparison. A
   calibration-scientist review independently reproduced the same
   scope problem and found it's worse than first measured: on Brutoh,
   ``mean_hrps`` runs ~12x SMALLER than the real external-heal-received
   rate, AND is INVERSELY correlated with key level across the real
   sweep range (the modeled baseline healer scales with DTPS, so the
   residual shrinks as keys get harder — a reader would see "safer
   keys ask more of my healer," backwards from reality). It also
   collided with this same PR's tail-risk panel, which states an
   unrelated "peak incoming HPS" number on a sound gross/pre-heal basis
   landing an order of magnitude higher — two "healer HPS" numbers on
   one trust surface with different bases. This callout now uses
   ``mean_dtps`` instead: raw post-mitigation damage taken per second,
   before self-sustain, which is already glossed that way elsewhere on
   this same panel and correctly increases with key level. See
   ``_render_healer_ask_caption``'s docstring in ``simf.ui.verdict`` for
   the full writeup.

These tests pin the shipped (DTPS-based, comparison-free) behavior:
  1. The caption renders the plain DTPS number anchored at the claimed
     key, framed as damage to help absorb/out-heal (not a literal
     required-HPS claim), with no "exceeds"/"within" framing and no
     reference-rate number attached.
  2. Silent (no caption) when there's no claimed key or no matching
     sweep point.
  3. Source-grep tripwire: the panel actually wires the callout in.
  4. The generic Tank Score/HRPS/DTPS caption further down the panel
     was NOT folded into this callout (it no longer defines HRPS, so
     there's nothing to fold). Separately (2026-07-08), that caption's
     own HRPS description was fixed to stop overclaiming HRPS as "the
     healing per second your healer needs" — see
     ``test_generic_tank_score_caption_no_longer_overclaims_hrps_as_healer_ask``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from simf.ui.verdict import _render_healer_ask_caption

REPO_ROOT = Path(__file__).resolve().parents[1]
VERDICT_PY = REPO_ROOT / "src" / "simf" / "ui" / "verdict.py"


@dataclass
class _StubPoint:
    """Minimal KeyLevelPoint-shape stub — only the fields the callout reads."""

    key_level: int
    mean_dtps: float


class _StubVerdict:
    """Minimal stand-in for `KeyLevelVerdict` — just what
    `_verdict_claimed_key` + the callout read. Mirrors the stub in
    `test_world_ceiling_caption.py`."""

    def __init__(self, *, comfortable_max, displayable_prog, points):
        self.comfortable_max = comfortable_max
        self._displayable_prog = displayable_prog
        self.points = points

    def displayable_prog_ceiling(self):
        return self._displayable_prog


def test_caption_states_the_dtps_number_at_the_claimed_key() -> None:
    """Concrete numbers: a +18 claimed key with mean_dtps=62,334 renders
    that exact figure, framed as damage to absorb/out-heal — not a
    literal "required HPS" claim, and no comparison-framing language."""
    point = _StubPoint(key_level=18, mean_dtps=62_334.0)
    verdict = _StubVerdict(comfortable_max=15, displayable_prog=18, points=[point])

    with patch("simf.ui.verdict.st") as st_mock:
        rendered = _render_healer_ask_caption(verdict)

    assert rendered is True
    assert st_mock.caption.called
    text = st_mock.caption.call_args[0][0]
    assert "+18" in text
    assert "62,334 DTPS" in text
    # Framed as damage to out-heal, not "this IS the healer's required HPS"
    # (that overclaim is exactly what mean_hrps had and was dropped for).
    assert "HPS" not in text
    assert "typical" not in text.lower()
    assert "exceeds" not in text.lower()
    assert "budget" not in text.lower()


def test_caption_anchors_on_comfortable_max_when_every_key_is_safe() -> None:
    """When the sweep has no progression ceiling, `_verdict_claimed_key`
    falls back to `comfortable_max` — the callout must follow the same
    key, not silently pick a different row."""
    point = _StubPoint(key_level=20, mean_dtps=67_932.0)
    verdict = _StubVerdict(comfortable_max=20, displayable_prog=None, points=[point])

    with patch("simf.ui.verdict.st") as st_mock:
        rendered = _render_healer_ask_caption(verdict)

    assert rendered is True
    text = st_mock.caption.call_args[0][0]
    assert "+20" in text
    assert "67,932 DTPS" in text


def test_dtps_increases_with_key_level_unlike_the_dropped_hrps_design() -> None:
    """Sanity check on the exact failure mode that got mean_hrps cut:
    a higher key level must render a higher (or equal), never lower,
    headline number. This is trivially true for mean_dtps by
    construction (it's just relayed), but pins the property explicitly
    since it's the whole reason the metric was swapped."""
    low = _StubPoint(key_level=10, mean_dtps=45_829.0)
    high = _StubPoint(key_level=20, mean_dtps=67_932.0)

    with patch("simf.ui.verdict.st") as st_mock:
        _render_healer_ask_caption(
            _StubVerdict(comfortable_max=10, displayable_prog=None, points=[low])
        )
        low_text = st_mock.caption.call_args[0][0]
    with patch("simf.ui.verdict.st") as st_mock:
        _render_healer_ask_caption(
            _StubVerdict(comfortable_max=20, displayable_prog=None, points=[high])
        )
        high_text = st_mock.caption.call_args[0][0]

    assert "45,829" in low_text
    assert "67,932" in high_text


def test_silent_when_no_claimed_key() -> None:
    """Empty/undergeared sweep (`_verdict_claimed_key` returns None) ->
    no key to anchor on, stay silent rather than guess."""
    verdict = _StubVerdict(comfortable_max=None, displayable_prog=None, points=[])

    with patch("simf.ui.verdict.st") as st_mock:
        rendered = _render_healer_ask_caption(verdict)

    assert rendered is False
    assert not st_mock.caption.called


def test_silent_when_no_matching_point() -> None:
    """Claimed key doesn't match any swept point (shouldn't normally
    happen, but defensive) -> silent, not a crash."""
    point = _StubPoint(key_level=99, mean_dtps=40_000.0)
    verdict = _StubVerdict(comfortable_max=15, displayable_prog=18, points=[point])

    with patch("simf.ui.verdict.st") as st_mock:
        rendered = _render_healer_ask_caption(verdict)

    assert rendered is False
    assert not st_mock.caption.called


def test_callout_wired_into_verdict_panel() -> None:
    """Source-grep tripwire — the panel must actually call the helper
    right after the headline/detail block. A refactor that drops the
    call site would silently remove the healer-ask callout from
    production without failing any render-level test (mirrors the
    pattern in test_verdict_skill_caveat.py)."""
    text = VERDICT_PY.read_text()
    assert "_render_healer_ask_caption(verdict)" in text


def test_generic_tank_score_caption_no_longer_folded_into_headline_callout() -> None:
    """The bottom Tank Score caption must NOT be folded into (or
    cross-reference) the headline callout — that callout is about
    mean_dtps now, not mean_hrps, so there's nothing to fold. This is
    the un-fold half of the DTPS pivot: the caption doesn't gate on a
    healer_ask_rendered branch."""
    text = VERDICT_PY.read_text()
    assert "healer_ask_rendered" not in text


def test_generic_tank_score_caption_no_longer_overclaims_hrps_as_healer_ask() -> None:
    """2026-07-08 fix: the Tank Score caption used to describe HRPS as
    "the healing per second your healer needs after your self-sustain"
    — a healer-facing HPS claim that's ~20x too small and, on the real
    sweep range, falls as key level rises (backwards from reality),
    because `compute_hrps` also nets out the sim's own modeled healer
    output. HRPS still feeds the Tank Score weighting internally
    (unchanged); this only fixes what the caption tells the reader it
    means."""
    text = VERDICT_PY.read_text()
    assert "healing per second your healer needs" not in text
    # Split across two substring checks, not one — the actual source
    # wraps this across two adjacent string literals (only merged at
    # Python-parse time), so a single multi-line substring wouldn't
    # match the raw file text (same wrinkle noted on the test above).
    assert "death rate (40%), an internal " in text
    assert "healing-throughput composite (30%)" in text
