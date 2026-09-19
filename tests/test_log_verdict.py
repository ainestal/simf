"""Tests for log_verdict() — plain-English summary of death reconstructions."""

from simf.io.combat_log import DamageTakenEvent, DeathRecord
from simf.io.death_analysis import DeathEvent, log_verdict


def _ev(time_s, spell, source, school, amount, current_hp=None, max_hp=None):
    return DamageTakenEvent(
        time_s=time_s,
        event_type="SPELL_DAMAGE",
        source_name=source,
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
        current_hp=current_hp,
        max_hp=max_hp,
    )


def _death_event(spells_sources_schools_amounts):
    """Helper: build a DeathEvent with the given preceding damage events."""
    preceding = [
        _ev(0.0, sp, src, sch, amt) for (sp, src, sch, amt) in spells_sources_schools_amounts
    ]
    death = DeathRecord(time_s=5.0, rel_time_s=5.0)
    return DeathEvent(
        death=death,
        preceding=preceding,
        total_damage_window=sum(e.amount for e in preceding),
        max_hit=max((e.amount for e in preceding), default=0),
        num_hits=len(preceding),
    )


def test_no_deaths_returns_none():
    assert log_verdict([]) is None


def test_single_death_physical_warrior():
    de = _death_event(
        [
            ("Crushing Slam", "Boss", "physical", 8_000_000),
            ("Auto Attack", "Boss", "physical", 1_500_000),
        ]
    )
    v = log_verdict([de], class_spec="protection_warrior")
    assert v is not None
    assert v.n_deaths == 1
    assert "Crushing Slam" in v.headline
    assert "physical" in v.headline.lower()
    assert "Boss" in v.headline
    assert "Shield Block" in v.suggested_fix  # warrior-specific phys advice


def test_single_death_magic_paladin():
    de = _death_event(
        [
            ("Shadow Bolt Volley", "Caster", "shadow", 6_000_000),
            ("Soul Drain", "Caster", "shadow", 2_000_000),
        ]
    )
    v = log_verdict([de], class_spec="protection_paladin")
    assert v is not None
    assert "Shadow Bolt Volley" in v.headline
    assert "Ardent Defender" in v.suggested_fix


def test_multi_death_headline_pluralizes():
    de = _death_event([("Big Hit", "Boss", "physical", 5_000_000)])
    v = log_verdict([de, de, de], class_spec="protection_warrior")
    assert v.n_deaths == 3
    assert "3 times" in v.headline


def test_unknown_spec_falls_back_to_generic_advice():
    de = _death_event([("Crushing Slam", "Boss", "physical", 5_000_000)])
    v = log_verdict([de], class_spec="nonexistent_spec")
    assert v is not None
    assert "spec's" in v.suggested_fix  # generic phrasing


def test_top_share_calculation():
    """80% from one source → top_share_pct ~= 0.80."""
    de = _death_event(
        [
            ("Crushing", "BossA", "physical", 8_000_000),
            ("Other", "BossA", "physical", 2_000_000),
        ]
    )
    v = log_verdict([de], class_spec="protection_warrior")
    assert abs(v.top_share_pct - 0.8) < 0.01


def test_no_damage_events_in_window():
    """Death with empty preceding window returns a degenerate verdict, not a crash."""
    death = DeathRecord(time_s=5.0, rel_time_s=5.0)
    de = DeathEvent(death=death, preceding=[], total_damage_window=0, max_hit=0, num_hits=0)
    v = log_verdict([de])
    assert v is not None
    assert "no damage events" in v.headline.lower()


def test_no_spec_provided_uses_generic_advice():
    de = _death_event([("Hit", "Boss", "physical", 5_000_000)])
    v = log_verdict([de])  # class_spec=None
    assert v is not None
    assert "spec's" in v.suggested_fix


def test_unexplained_death_demotes_verdict():
    """Regression for the Skyreach +14 case: a real void/fall/reset kill
    whose logged damage leaves ~half the HP pool unaccounted for must NOT
    be reported as "top killer was X, Y% of fatal-window damage" — that
    percentage is arithmetically correct but causally wrong.
    """
    death = DeathRecord(time_s=105.0, rel_time_s=105.0)
    preceding = [
        # Last logged damage leaves the tank at 49.6% HP; nothing further
        # is logged before UNIT_DIED — the real kill blow never emits a
        # DAMAGE line at all.
        _ev(
            100.0, "Auto Attack", "Ranjit", "physical", 50_000, current_hp=496_000, max_hp=1_000_000
        ),
    ]
    de = DeathEvent(
        death=death,
        preceding=preceding,
        total_damage_window=sum(e.amount for e in preceding),
        max_hit=max(e.amount for e in preceding),
        num_hits=len(preceding),
    )

    v = log_verdict([de], class_spec="protection_warrior")

    assert v is not None
    assert v.n_deaths == 1
    # Does NOT name a top killer with a share percentage.
    assert "Ranjit" not in v.headline
    assert "top killer" not in v.headline.lower()
    assert v.top_share_pct == 0.0
    assert "Ranjit" not in v.top_killer
    # Demoted trust-voice headline: names the gap and defers cause.
    assert "accounts for" in v.headline.lower()
    assert "can't name the cause" in v.headline.lower()


def test_fully_explained_death_still_names_killer():
    """Positive control: when logged damage genuinely reconciles the drop
    to zero, the ordinary top-killer verdict is unchanged.
    """
    death = DeathRecord(time_s=100.0, rel_time_s=100.0)
    preceding = [
        _ev(
            98.0, "Crushing Slam", "Boss", "physical", 400_000, current_hp=600_000, max_hp=1_000_000
        ),
        _ev(100.0, "Auto Attack", "Boss", "physical", 650_000),
    ]
    de = DeathEvent(
        death=death,
        preceding=preceding,
        total_damage_window=sum(e.amount for e in preceding),
        max_hit=max(e.amount for e in preceding),
        num_hits=len(preceding),
    )

    v = log_verdict([de], class_spec="protection_warrior")

    assert v is not None
    assert "top killer was" in v.headline.lower()
    assert "Boss" in v.headline
    assert v.top_share_pct > 0.0


def test_unexplained_death_ignored_when_no_hp_data():
    """Logs without the advanced-logging HP fields (WCL, ACL-off) have no
    way to reconcile — the guard must default to the pre-existing
    behaviour rather than demoting every such verdict.
    """
    de = _death_event(
        [
            ("Crushing Slam", "Boss", "physical", 8_000_000),
            ("Auto Attack", "Boss", "physical", 1_500_000),
        ]
    )
    v = log_verdict([de], class_spec="protection_warrior")
    assert v is not None
    assert "top killer was" in v.headline.lower()
