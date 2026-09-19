"""Regression test: HRPS surfacing removed from the key-level verdict panel.

Item 2 of the 2026-07-08 review triage (`verdict.py:164-174`'s own
docstring flagged this exact gap as "KNOWN, NOT FIXED HERE — flagged for
a future ticket. This is that ticket.").

``mean_hrps`` (``core/normalized_score.py: compute_hrps``) nets out not
just the tank's self-heals but ALSO the sim's own modeled healer output,
which makes it run ~20x too small AND fall as key level rises (while
DTPS correctly rises) — backwards from what a reader would expect from
a number captioned "the healing per second your healer needs after
your self-sustain." This pins two removals:

  1. The per-key ladder row no longer prints a raw "HRPS {number}"
     token — it now shows death% / DTPS / Tank Score / band only.
  2. The Tank Score caption no longer tells the reader HRPS is a
     healer-facing HPS figure; it now names HRPS as an internal
     healing-throughput composite.

Neither ``mean_hrps`` itself nor the Tank Score weights change here —
this is a surfacing-only fix, confirmed by keeping a large, obviously
wrong ``mean_hrps`` value on the stub point and asserting it never
reaches the rendered text.
"""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).parent.parent / "src" / "simf" / "ui" / "app.py"


def _build_app() -> AppTest:
    return AppTest.from_file(str(APP_PATH), default_timeout=30)


def _seed_key_verdict_cache(app: AppTest, *, mean_hrps: float) -> None:
    """Pre-populate the verdict cache the same way
    ``test_skill_inference_app.py`` does, so ``app.run()`` renders the
    panel without running the real (slow) Monte Carlo sweep."""
    from simf.core.key_level_verdict import KeyLevelPoint, KeyLevelVerdict

    char_data = {
        "name": "Brutoh",
        "race": "earthen",
        "class_spec": "protection_warrior",
        "talents": "kiratank-defensive",
        "strength": 2182,
        "stamina": 34176,
        "armor_from_gear": 5015,
        "haste_rating": 2318,
        "crit_rating": 1391,
        "mastery_rating": 1608,
        "versatility_rating": 296,
        "max_hp_override": 751872,
    }

    from simf.core.character import Character

    char = Character.from_dict(char_data)
    push_key = 18

    verdict = KeyLevelVerdict(
        points=[
            KeyLevelPoint(
                key_level=push_key,
                damage_multiplier=1.0,
                death_rate=0.0,
                mean_dtps=48_902.0,
                p99_5s_window=1.0,
                band="comfortable",
                # Deliberately large + wrong-shaped so a regression that
                # re-surfaces this raw number would be impossible to miss.
                mean_hrps=mean_hrps,
                normalized_tank_score=0.81,
            )
        ],
        comfortable_max=push_key,
        prog_ceiling=None,
        affix="fortified",
    )

    char_key = (
        char.class_spec,
        round(char.max_hp(), 0),
        round(char.total_armor(), 0),
        round(char.versatility_pct(), 4),
    )

    app.session_state["char_data"] = char_data
    app.session_state["view"] = "gear"
    app.session_state["_key_verdict_cache"] = {
        "char_key": char_key,
        "iterations": 50,
        "verdict": verdict,
    }


def test_per_key_ladder_row_has_no_hrps_token() -> None:
    """The rendered per-key bullet row must contain DTPS and Tank Score
    but never an "HRPS" token — pins the deletion at verdict.py's row
    f-string."""
    app = _build_app()
    _seed_key_verdict_cache(app, mean_hrps=3_505.0)
    app.run()
    assert not app.exception

    body = "\n".join(str(m.value) for m in app.markdown)
    # Sanity: the ladder row we're targeting actually rendered.
    assert "+18" in body
    assert "DTPS" in body
    assert "Tank Score" in body
    # The regression this test guards against.
    assert "HRPS" not in body
    # The deliberately-wrong stub value must not leak through under any
    # other formatting either.
    assert "3,505" not in body


def test_tank_score_caption_no_longer_overclaims_hrps_as_healer_ask() -> None:
    """The generic Tank Score caption must stop telling the reader HRPS
    is "the healing per second your healer needs" and must instead
    name it as an internal composite term."""
    app = _build_app()
    _seed_key_verdict_cache(app, mean_hrps=3_505.0)
    app.run()
    assert not app.exception

    caption_body = "\n".join(str(c.value) for c in app.caption)
    assert "Tank Score" in caption_body
    assert "healing per second your healer needs" not in caption_body
    assert "internal healing-throughput composite" in caption_body
