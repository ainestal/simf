"""AppTest verification for Phase 2.10b — `← you` indicator on the ladder.

Driving the full end-to-end UI flow (load demo → open Why-died → analyze a
real log → switch back to Gear → trigger key-level verdict → render ladder)
is fragile in AppTest: the log analysis takes 30+ seconds and is gated
behind cached file scans that don't always settle inside the AppTest
default timeout. So we test the contract instead — directly pre-populate
`st.session_state["inferred_skill_tier"]` (the bridge between the two
surfaces) and verify the ladder picks it up.

Two things this guards:
1. The ladder reads `inferred_skill_tier` and renders `← you` on the
   matching tier label. A missing or renamed key would silently drop
   the indicator.
2. The label `← you` only appears once — pinning the inferred tier on
   exactly one rung, not all four (or zero).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).parent.parent / "src" / "simf" / "ui" / "app.py"


@pytest.fixture
def app() -> AppTest:
    return AppTest.from_file(str(APP_PATH), default_timeout=30)


# ─── _pick_ladder_key — spread-aware key selection ─────────────────────────


def _vp(key: int, death_rate: float, band: str):
    """Build a verdict-point stub."""
    from simf.core.key_level_verdict import KeyLevelPoint

    return KeyLevelPoint(
        key_level=key,
        damage_multiplier=1.0,
        death_rate=death_rate,
        mean_dtps=1.0,
        p99_5s_window=1.0,
        band=band,
    )


def _verdict_with(points):
    """Build a verdict shell around explicit points."""
    from simf.core.key_level_verdict import KeyLevelVerdict

    return KeyLevelVerdict(
        points=points, comfortable_max=None, prog_ceiling=None, affix="fortified"
    )


def test_pick_ladder_key_returns_first_non_comfortable():
    """When some keys are comfortable and others not, render the ladder at the
    LOWEST non-comfortable key — that's the first place skill bites. Brutoh
    feedback (2026-05-20): rendering at push_key gives a tiny spread for
    overgeared characters."""
    from simf.ui.app import _pick_ladder_key

    verdict = _verdict_with(
        [
            _vp(10, 0.00, "comfortable"),
            _vp(12, 0.02, "comfortable"),
            _vp(14, 0.10, "progression"),
            _vp(16, 0.30, "danger"),
            _vp(18, 0.60, "danger"),
        ]
    )
    assert _pick_ladder_key(verdict) == 14


def test_pick_ladder_key_falls_back_to_top_when_all_comfortable():
    """If every key in the sweep is comfortable (player is overgeared for
    the table ceiling), render at the top of the sweep so the ladder still
    appears — the caption explains the spread will be tight."""
    from simf.ui.app import _pick_ladder_key

    verdict = _verdict_with(
        [
            _vp(10, 0.00, "comfortable"),
            _vp(14, 0.01, "comfortable"),
            _vp(18, 0.04, "comfortable"),
            _vp(20, 0.05, "comfortable"),
        ]
    )
    assert _pick_ladder_key(verdict) == 20


def test_pick_ladder_key_returns_none_for_empty_verdict():
    """Defensive — a verdict with no points means the sweep failed
    upstream; no ladder to render."""
    from simf.ui.app import _pick_ladder_key

    assert _pick_ladder_key(_verdict_with([])) is None


def test_inferred_tier_marker_renders_when_session_state_is_set(app):
    """Pre-set `inferred_skill_tier` + key-level verdict cache + skill ladder
    cache in session state, then run the app and confirm the ladder renders
    the `← you` marker on the matching tier."""
    from simf.core.character import Character
    from simf.core.constants import load_skill_tiers
    from simf.core.key_level_verdict import KeyLevelPoint, KeyLevelVerdict
    from simf.core.skill_ladder import SkillLadder, SkillLadderPoint

    # Brutoh demo char_data — mirrors the fixture in conftest. We can't
    # use the engine fixture here because session_state needs the dict
    # shape that Character.from_dict consumes.
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
    char = Character.from_dict(char_data)

    tiers = load_skill_tiers()
    push_key = 18

    # Pre-build a deterministic ladder so the AppTest doesn't have to
    # spend 5 s computing one — pin the same shape the real call would.
    ladder = SkillLadder(
        key_level=push_key,
        affix="fortified",
        points=[
            SkillLadderPoint(
                tier_id=str(t["id"]),
                label=str(t["label"]),
                modifier=float(t["modifier"]),
                death_rate=0.02 + i * 0.05,
                mean_dtps=1.0,
                p99_5s_window=1.0,
                focus=str(t["focus"]),
                description=str(t["description"]),
            )
            for i, t in enumerate(tiers)
        ],
    )

    # Verdict cache shape that `_render_key_level_verdict_panel` reads
    verdict = KeyLevelVerdict(
        points=[
            KeyLevelPoint(
                key_level=push_key,
                damage_multiplier=1.0,
                death_rate=0.0,
                mean_dtps=1.0,
                p99_5s_window=1.0,
                band="comfortable",
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
    skill_char_key = (*char_key, push_key)

    # Pre-populate the relevant slots so first paint renders the ladder
    # without having to click through the surface.
    app.session_state["char_data"] = char_data
    app.session_state["view"] = "gear"
    app.session_state["_key_verdict_cache"] = {
        "char_key": char_key,
        "iterations": 50,
        "verdict": verdict,
    }
    app.session_state["_skill_ladder_cache"] = {
        "char_key": skill_char_key,
        "ladder": ladder,
    }
    app.session_state["inferred_skill_tier"] = {
        "log_name": "WoWCombatLog-test.txt",
        "run_index": 0,
        "tier_id": "reading",
        "sb_uptime_pct": 0.42,
        "cast_count": 73,
        # v3 (2026-05-21) — DS signal coupling. SB is the floor here
        # (reading) but DS clears `in_the_zone`, so the caption surfaces
        # SB as the lower of the two.
        "ds_press_rate": 0.82,
        "ds_cast_count": 22,
        "sb_tier_id": "reading",
        "ds_tier_id": "in_the_zone",
        # PR #2 (2026-05-21) — build ceiling + bottleneck attribution.
        # Player at 42% uptime, ceiling 62%: missed-pressable gap is
        # 20pp. Caption should render the build-floor line.
        "sb_ceiling_pct": 0.62,
        "sb_ceiling_rage_starved_pct": 0.18,
        "sb_ceiling_charge_limited_pct": 0.20,
        "sb_missed_pressable_pct": 0.20,
        "top_gaps": [
            {
                "time_phrase": "1:01",
                "duration_s": 23.5,
                "damage_during_gap": 593_634.0,
            }
        ],
    }

    # The key-level verdict panel is wrapped in an expander that defaults
    # closed. Open it via session_state — Streamlit's expander state keys
    # don't have a stable public API, so we drive the render path another
    # way: trigger the ladder render branch by setting the `expanded` flag
    # on the expander widget. Easier: just verify the *intent* — the
    # ladder text + indicator string appears in the markdown stream when
    # the surface renders with our pre-set state.
    app.run()
    assert not app.exception

    body = "\n".join(str(m.value) for m in app.markdown)

    # The ladder must render — AppTest auto-expands `st.expander` widgets
    # so the gated key-level verdict + ladder both appear in the markdown
    # stream. If this fails, either the cache key shape drifted or the
    # expander gating semantics changed in Streamlit.
    assert "How your play changes the answer" in body, (
        "Ladder didn't render — verdict + ladder caches may not be wired correctly."
    )

    # Indicator must appear on exactly one rung — the one whose tier_id
    # matches the inferred bucket. Zero would mean the ladder ignores
    # `inferred_skill_tier`; more than one would mean the comparison is
    # broken (e.g. matching by label instead of id, or always-truthy).
    assert "← **you**" in body, (
        "`← you` marker is missing — inferred_skill_tier is not being read on the Gear surface."
    )
    assert body.count("← **you**") == 1, (
        f"`← you` marker should appear on exactly one rung; "
        f"found {body.count('← **you**')} occurrences."
    )

    # The marker should be on the `reading` tier (matches the inferred
    # tier_id we set). The label for that tier is "Reading the fight" —
    # find the line containing the marker and confirm.
    marker_line = next(line for line in body.splitlines() if "← **you**" in line)
    assert "Reading the fight" in marker_line, (
        f"Marker landed on the wrong rung — expected 'Reading the fight' "
        f"based on inferred tier_id='reading'; got line: {marker_line!r}"
    )

    # The trust-building caption ("Based on N SB casts (X% uptime) and N
    # Demo Shout casts (Y% of ideal cadence) in `log_name`") renders via
    # `st.caption()`, which AppTest exposes through `app.caption`, not
    # `app.markdown`. Pin both per-signal numbers and the floor hint —
    # ds_tier_id=in_the_zone while sb_tier_id=reading means SB is named
    # as the lower of the two.
    caption_body = "\n".join(str(c.value) for c in app.caption)
    assert "SB casts" in caption_body and "42%" in caption_body, (
        "Inference caption missing the Shield Block half of the trust anchor."
    )
    assert "Demo Shout" in caption_body and "82%" in caption_body, (
        "Inference caption missing the Demoralizing Shout half of the trust anchor."
    )
    assert "Shield Block uptime is the lower" in caption_body, (
        "Inference caption should name SB as the floor when sb_tier_id < ds_tier_id."
    )

    # PR #2 — build ceiling + bottleneck attribution must render below
    # the per-signal trust line. Pin the ceiling, the rage/charge floor
    # pcts (described as build floor at perfect play, NOT the player's
    # actual rage usage), and the missed-pressable pp figure.
    assert "build's SB ceiling is **62%**" in caption_body, (
        "Ceiling caption is missing — bottleneck attribution not surfaced."
    )
    assert "rage gaps cost **18%**" in caption_body, (
        "Build-floor rage attribution missing from caption."
    )
    assert "charge cooldowns cost **20%**" in caption_body, (
        "Build-floor charge attribution missing from caption."
    )
    assert "**20pp below ceiling**" in caption_body, "Missed-pressable gap is not rendered."

    # Phase 2.10g — each rung must surface the modifier (press rate) and
    # SB uptime inline so the gate is visible alongside the flavor label.
    # Elite tank ask 2026-05-22: "show me the gate, let me argue with it."
    assert "presses **100%**" in body, (
        "Top tier should surface modifier=1.00 as 'presses 100%' inline."
    )
    assert "presses **40%**" in body, (
        "Bottom tier should surface modifier=0.40 as 'presses 40%' inline."
    )
    assert "SB uptime **" in body, "Each rung should surface the per-tier mean Shield Block uptime."

    # Phase 2.10c — the anchored callout under the matched rung must
    # appear: "On your last run: 24s uncovered at 1:01 — 593,634
    # physical damage taken." Pin all three pieces (time phrase, damage
    # number, "On your last run" lede).
    assert "On your last run" in body, (
        "Anchored coaching callout is missing — top_gaps may not be wired through to the ladder."
    )
    assert "1:01" in body, "Top-gap time_phrase missing from the callout."
    assert "593,634" in body, "Top-gap damage number missing from the callout."

    # And the callout must be on the same rung as `← you` — verify by
    # checking that the marker_line (which contained `← you`) has the
    # callout text in the joined body adjacent to it. The lines render
    # as bullet rows; the callout sits as a sub-line directly under
    # the matched rung.
    rung_start = body.index("Reading the fight")
    # Bottom tier label is "Off your usual game" at push_key=18 per the
    # high-key relabel (skill_ladder.py:_label_for_key_level). At lower
    # keys this would be "Learning the buttons" — the test fixture pins
    # push_key=18 so the relabel applies.
    next_rung_start = body.find("Off your usual game", rung_start)
    rung_block = body[rung_start : next_rung_start if next_rung_start > 0 else None]
    assert "On your last run" in rung_block, (
        "Callout appeared somewhere in the body but NOT under the "
        "Reading-the-fight rung — wiring is misaligned."
    )
