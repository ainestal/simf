"""Unit tests for ``scripts/compute_season2_school_mix.py``."""

from __future__ import annotations

from types import SimpleNamespace

from scripts.compute_season2_school_mix import _parse_fight_arg, compute_school_mix


def _fake_event(
    school: str, raw_amount: float, *, is_self_inflicted: bool = False
) -> SimpleNamespace:
    return SimpleNamespace(
        school=school, raw_amount=raw_amount, is_self_inflicted=is_self_inflicted
    )


def _fake_replay(events: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(events=events)


def test_parse_fight_arg_splits_code_fight_player():
    assert _parse_fight_arg("ExampleCode4444444:102:AnonPlayerX5") == (
        "ExampleCode4444444",
        102,
        "AnonPlayerX5",
    )


def test_compute_school_mix_aggregates_across_fights(monkeypatch):
    import scripts.compute_season2_school_mix as mod

    fake_replays = {
        ("codeA", 1, "Alpha"): _fake_replay(
            [_fake_event("physical", 600.0), _fake_event("fire", 400.0)]
        ),
        ("codeB", 2, "Beta"): _fake_replay(
            [_fake_event("physical", 400.0), _fake_event("shadow", 600.0)]
        ),
    }

    def _fake_wcl_to_replay_data(report_code, fight_id, player_name, **kwargs):
        return fake_replays[(report_code, fight_id, player_name)]

    monkeypatch.setattr(mod, "wcl_to_replay_data", _fake_wcl_to_replay_data)

    fractions, per_fight = compute_school_mix([("codeA", 1, "Alpha"), ("codeB", 2, "Beta")])

    assert fractions["physical"] == 0.5
    assert fractions["fire"] == 0.2
    assert fractions["shadow"] == 0.3
    assert sum(fractions.values()) == 1.0
    assert len(per_fight) == 2
    assert per_fight[0]["event_count"] == 2
    assert per_fight[0]["player"] == "Alpha"


def test_compute_school_mix_excludes_self_inflicted_events(monkeypatch):
    """Brewmaster Stagger ticks are self-inflicted and carry a POST-mitigation
    amount (see `wcl_replay.adapt_events`) — a different basis than every
    other event, and a delayed echo of damage already counted once. They must
    not pollute a pre-mitigation "fraction of damage by school" sum."""
    import scripts.compute_season2_school_mix as mod

    monkeypatch.setattr(
        mod,
        "wcl_to_replay_data",
        lambda *a, **k: _fake_replay(
            [
                _fake_event("physical", 800.0),
                _fake_event("fire", 200.0),
                _fake_event("physical", 5000.0, is_self_inflicted=True),
            ]
        ),
    )

    fractions, per_fight = compute_school_mix([("codeA", 1, "Alpha")])

    assert fractions["physical"] == 0.8
    assert fractions["fire"] == 0.2
    assert per_fight[0]["event_count"] == 2


def test_compute_school_mix_empty_events_returns_no_fractions(monkeypatch):
    import scripts.compute_season2_school_mix as mod

    monkeypatch.setattr(mod, "wcl_to_replay_data", lambda *a, **k: _fake_replay([]))

    fractions, per_fight = compute_school_mix([("codeA", 1, "Alpha")])

    assert fractions == {}
    assert per_fight[0]["event_count"] == 0
