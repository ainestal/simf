"""Render-layer smoke for the defensive-coverage coaching section.

Stubs `st.*`, feeds `render_defensive_coverage` synthetic `CoverageReport`s,
and asserts the emitted markdown/captions match the answer-first + honesty
contract: no % in the headline, uncovered hits listed first, continuous tools
shown as a separate uptime line, a "no simulation" honesty caption, and the
soft (non-blaming) path when no major cooldowns were detected.
"""

from __future__ import annotations

from simf.core import coaching
from simf.core.coaching import Lever
from simf.io.combat_log import DamageTakenEvent
from simf.ui import log_coaching


class _StubStreamlit:
    def __init__(self) -> None:
        self.markdowns: list[str] = []
        self.captions: list[str] = []
        self.infos: list[str] = []

    def markdown(self, content, *a, **k):
        self.markdowns.append(content)

    def caption(self, content, *a, **k):
        self.captions.append(content)

    def info(self, content, *a, **k):
        self.infos.append(content)


def _ev(amount, t, *, school="physical", spell="Hit", sid=1, src="Mob", guid="g"):
    return DamageTakenEvent(
        time_s=t,
        event_type="SPELL_DAMAGE",
        source_name=src,
        spell_name=spell,
        school=school,
        amount=amount,
        base_amount=amount,
        overkill=0,
        blocked=0,
        absorbed=0,
        resisted=0,
        is_critical=False,
        is_glancing=False,
        source_guid=guid,
        spell_id=sid,
    )


SW = Lever(871, "Shield Wall", "all", "coverage", "buff")
SB = Lever(132404, "Shield Block", "physical", "continuous", "buff")


def _report(events, *, levers, buff_windows=None, run_end=300.0, min_events=1):
    return coaching.build_coverage_report(
        "protection_warrior",
        events,
        levers=levers,
        buff_windows=buff_windows or {},
        debuff_windows_by_source={},
        run_start=0.0,
        run_end=run_end,
        is_bleed_fn=lambda _n, _p=True: False,
        top_n=8,
        min_events=min_events,
    )


def _render(report):
    stub = _StubStreamlit()
    # render_defensive_coverage now lives in log_coaching and reads `st` from
    # that module's globals, so the stub must be swapped there (not on the
    # log_view facade that merely re-exports the function).
    real = log_coaching.st
    log_coaching.st = stub
    try:
        log_coaching.render_defensive_coverage(report)
    finally:
        log_coaching.st = real
    return stub


def test_none_renders_nothing():
    stub = _render(None)
    assert stub.markdowns == [] and stub.captions == [] and stub.infos == []


def test_section_header_always_present_when_data():
    rep = _report([_ev(100, 10), _ev(90, 200)], levers=[SW], buff_windows={871: [(5.0, 15.0)]})
    stub = _render(rep)
    assert any("Defensive coverage on your biggest hits" in m for m in stub.markdowns)


def test_headline_has_no_percent_and_lists_uncovered_first():
    # hit at 10s covered by Shield Wall, hit at 200s uncovered
    rep = _report([_ev(100, 10), _ev(90, 200)], levers=[SW], buff_windows={871: [(5.0, 15.0)]})
    stub = _render(rep)
    assert "%" not in stub.markdowns[1]  # markdowns[0] is the header, [1] the headline
    # the uncovered (⚠️) row must appear before the covered (🛡) row in the table
    table = next(m for m in stub.markdowns if "no cooldown up" in m or "🛡" in m)
    assert table.index("no cooldown up") < table.index("🛡")


def test_continuous_uptime_line_present_and_no_benchmark():
    rep = _report(
        [_ev(100, 10)], levers=[SW, SB], buff_windows={871: [(5.0, 15.0)], 132404: [(0.0, 150.0)]}
    )
    stub = _render(rep)
    cont = [c for c in stub.captions if "Continuous active mitigation" in c]
    assert cont and "Shield Block 50%" in cont[0]
    # no "achievable"/"should"/benchmark framing anywhere
    allcaps = " ".join(stub.captions).lower()
    assert "achievable" not in allcaps and "should have" not in allcaps


def test_honesty_caption_states_no_simulation():
    rep = _report([_ev(100, 10), _ev(90, 200)], levers=[SW], buff_windows={871: [(5.0, 15.0)]})
    stub = _render(rep)
    assert any("no simulation" in c.lower() for c in stub.captions)


def test_no_data_is_soft_info_not_a_blame():
    # no detected coverage levers → soft st.info, never "you ate N/N"
    rep = _report([_ev(100, 10), _ev(90, 20)], levers=[SW], buff_windows={})
    stub = _render(rep)
    assert stub.infos, "expected a soft st.info for the no-data path"
    assert "ate" not in stub.infos[0].lower()
    assert not any("ate" in m.lower() for m in stub.markdowns)


DEMO = coaching.Lever(
    1160, "Demoralizing Shout", "all", "coverage", "debuff_on_source", 8.0, tier="minor"
)


def _ev_g(amount, t, guid):
    e = _ev(amount, t)
    object.__setattr__(e, "source_guid", guid)
    return e


def test_partial_tier_renders_distinct_from_clean_soak():
    """A minor-only (Demo Shout) hit renders '🟡 partial', never '🛡' clean."""
    covered = _ev_g(100, 10, "m")  # Shield Wall
    partial = _ev_g(90, 40, "m")  # Demo Shout only
    rep = coaching.build_coverage_report(
        "protection_warrior",
        [covered, partial],
        levers=[SW, DEMO],
        buff_windows={871: [(5.0, 15.0)]},
        debuff_windows_by_source={1160: {"m": [(35.0, 48.0)]}},
        run_start=0.0,
        run_end=300.0,
        is_bleed_fn=lambda _n, _p=True: False,
        top_n=8,
        min_events=1,
    )
    stub = _render(rep)
    table = next(m for m in stub.markdowns if "🟡" in m or "🛡" in m)
    assert "🟡 partial: Demoralizing Shout" in table
    assert "🛡 Shield Wall" in table
    # the partial row must NOT be dressed as a clean shield soak
    assert "🛡 Demoralizing Shout" not in table


def test_no_data_with_continuous_renders_uptime_and_credits():
    """Fix for the discarded-continuous over-blame: the no-coverage path still
    shows continuous uptime and does not say 'weren't pressed'."""
    rep = _report(
        [_ev(100, 10), _ev(90, 20)],
        levers=[SB],
        buff_windows={132404: [(0.0, 150.0)]},
        min_events=1,
    )
    stub = _render(rep)
    # soft info headline, continuous uptime caption still rendered
    assert stub.infos
    assert any("Continuous active mitigation" in c and "Shield Block" in c for c in stub.captions)
    assert not any("weren't pressed" in (c.lower()) for c in stub.captions + stub.infos)


def test_too_short_run_only_caption():
    rep = _report([_ev(100, 10)], levers=[SW], buff_windows={871: [(5.0, 15.0)]}, min_events=20)
    stub = _render(rep)
    assert any("Too few hits" in c for c in stub.captions)
    # no coverage table emitted
    assert not any("no cooldown up" in m for m in stub.markdowns)


# Cooldown-bearing lever → the availability split renders per-hit labels.
SW_CD = Lever(871, "Shield Wall", "all", "coverage", "buff", cooldown_s=240)


def test_split_labels_ready_vs_kit_on_cooldown():
    # Shield Wall cast at t=5 → DETECTED. The t=10 hit is on CD (kit spent); the
    # t=300 hit is past the 240 CD (ready — the actionable miss).
    rep = _report(
        [_ev(500, 300), _ev(450, 10)],
        levers=[SW_CD],
        buff_windows={871: [(5.0, 9.0)]},
        run_end=400.0,
    )
    stub = _render(rep)
    table = next(m for m in stub.markdowns if "ready:" in m or "kit on cooldown" in m)
    # The off-cooldown hit names the lever to pre-press; the spent hit reads soft.
    assert "ready: Shield Wall" in table
    assert "kit on cooldown" in table
    # Honesty: a spent-kit hit must not be tagged with the blaming "no cooldown up".
    assert "no cooldown up" not in table


def test_partial_row_surfaces_a_ready_major():
    """A partial hit (Demo Shout only) where a real major sat off cooldown must
    name the ready major in its row — a '🟡 partial' must not hide it."""
    # Shield Wall cast at t=0 (detected, past its 240 CD by t=300); Demo up at t=300.
    partial = _ev_g(500, 300, "m")
    rep = coaching.build_coverage_report(
        "protection_warrior",
        [partial],
        levers=[SW_CD, DEMO],
        buff_windows={871: [(0.0, 8.0)]},
        debuff_windows_by_source={1160: {"m": [(295.0, 310.0)]}},
        run_start=0.0,
        run_end=400.0,
        is_bleed_fn=lambda _n, _p=True: False,
        top_n=8,
        min_events=1,
    )
    stub = _render(rep)
    table = next(m for m in stub.markdowns if "🟡" in m)
    assert "🟡 partial: Demoralizing Shout" in table
    assert "Shield Wall ready" in table


def test_interruptible_hit_gets_a_distinct_badge_alongside_its_coverage_status():
    """A hit proven interruptible elsewhere in the run gets its own badge —
    independent of, and rendered alongside, its normal coverage status."""
    ev = _ev(500_000, 10, spell="Arcane Bolt", sid=1279627)
    rep = coaching.build_coverage_report(
        "protection_warrior",
        [ev],
        levers=[SW],
        # Shield Wall detected (cast at t=50) but its window doesn't cover t=10 —
        # keeps this hit "uncovered" while has_data=True so the table renders.
        buff_windows={871: [(50.0, 60.0)]},
        debuff_windows_by_source={},
        run_start=0.0,
        run_end=300.0,
        is_bleed_fn=lambda _n, _p=True: False,
        top_n=8,
        min_events=1,
        interrupted_spell_ids=frozenset({1279627}),
    )
    stub = _render(rep)
    table = next(m for m in stub.markdowns if "Arcane Bolt" in m)
    assert "⚡ interruptible" in table
    # The uncovered badge still renders too — the two signals are additive.
    assert "no cooldown up" in table


def test_non_interruptible_hit_has_no_lightning_badge():
    ev = _ev(500_000, 10, spell="Arcane Bolt", sid=1279627)
    rep = _report(
        [ev], levers=[SW], buff_windows={871: [(50.0, 60.0)]}
    )  # default interrupted_spell_ids=frozenset()
    stub = _render(rep)
    table = next(m for m in stub.markdowns if "Arcane Bolt" in m)
    assert "⚡" not in table


# ── kick-availability split (cooldown-aware, PR follow-up 2026-07-03) ────────

PUMMEL = coaching.InterruptAbility(6552, "Pummel", 15.0)


def _report_with_interrupt(events, *, levers, buff_windows=None, own_casts=()):
    return coaching.build_coverage_report(
        "protection_warrior",
        events,
        levers=levers,
        buff_windows=buff_windows or {},
        debuff_windows_by_source={},
        run_start=0.0,
        run_end=300.0,
        is_bleed_fn=lambda _n, _p=True: False,
        top_n=8,
        min_events=1,
        interrupted_spell_ids=frozenset({1279627}),
        interrupt_ability=PUMMEL,
        own_interrupt_cast_times=own_casts,
    )


def test_kick_ready_badge_renders():
    ev = _ev(500_000, 100, spell="Arcane Bolt", sid=1279627)
    rep = _report_with_interrupt([ev], levers=[SW], buff_windows={871: [(50.0, 60.0)]})
    stub = _render(rep)
    table = next(m for m in stub.markdowns if "Arcane Bolt" in m)
    assert "🟢 your Pummel was ready" in table


def test_kick_on_cooldown_badge_renders():
    ev = _ev(500_000, 100, spell="Arcane Bolt", sid=1279627)
    rep = _report_with_interrupt(
        [ev], levers=[SW], buff_windows={871: [(50.0, 60.0)]}, own_casts=(90.0,)
    )
    stub = _render(rep)
    table = next(m for m in stub.markdowns if "Arcane Bolt" in m)
    assert "⚪ your Pummel was on cooldown" in table


def test_kick_availability_summary_caption_renders():
    ready = _ev(500_000, 100, spell="Arcane Bolt", sid=1279627)
    on_cd = _ev(400_000, 200, spell="Arcane Bolt", sid=1279627)
    rep = coaching.build_coverage_report(
        "protection_warrior",
        [ready, on_cd],
        levers=[SW],
        buff_windows={871: [(50.0, 60.0)]},
        debuff_windows_by_source={},
        run_start=0.0,
        run_end=300.0,
        is_bleed_fn=lambda _n, _p=True: False,
        top_n=8,
        min_events=1,
        interrupted_spell_ids=frozenset({1279627}),
        interrupt_ability=PUMMEL,
        own_interrupt_cast_times=(190.0,),  # 10s before the t=200 hit → on CD
    )
    stub = _render(rep)
    summary = [c for c in stub.captions if "your own Pummel" in c]
    assert summary, f"expected a kick-availability summary caption, got: {stub.captions}"
    assert "off cooldown for 1" in summary[0]
    assert "on cooldown for 1" in summary[0]


def test_no_kick_note_or_caption_when_not_assessable():
    """No interrupt_ability known for the spec → the base interruptible badge
    still renders, but with no kick-specific note, and no summary caption."""
    ev = _ev(500_000, 10, spell="Arcane Bolt", sid=1279627)
    rep = coaching.build_coverage_report(
        "protection_warrior",
        [ev],
        levers=[SW],
        buff_windows={871: [(50.0, 60.0)]},
        debuff_windows_by_source={},
        run_start=0.0,
        run_end=300.0,
        is_bleed_fn=lambda _n, _p=True: False,
        top_n=8,
        min_events=1,
        interrupted_spell_ids=frozenset({1279627}),
        # no interrupt_ability passed
    )
    stub = _render(rep)
    table = next(m for m in stub.markdowns if "Arcane Bolt" in m)
    assert "⚡ interruptible" in table
    assert "was ready" not in table and "was on cooldown" not in table
    assert not any("your own" in c for c in stub.captions)
