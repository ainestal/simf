"""Regression: the M+ run outcome label must not claim TIMED for a
completed-over-time run.

WoW's ``CHALLENGE_MODE_END.success`` field encodes "completed?", not
"timed?" — a completed-over-time run is ``success=1`` with the real
duration. The old UI mapped ``success=True`` → ``TIMED ✓`` unconditionally,
which mislabeled depleted-but-completed runs. This module pins the
four-state outcome (TIMED / OVER TIME / ABANDONED / COMPLETED-unknown)
and the abandoned-vs-depleted distinction.

Triggering case: Algeth'ar Academy +14, log dated 2026-05-18.
Run duration 31:52 (1 912 867 ms); par time 31:00 (1 860 000 ms). Log
records ``CHALLENGE_MODE_END,2526,1,14,1912867,...``. With par_time
populated from dungeons.yaml, ``is_timed()`` is False (over par) and
the label should read OVER TIME ✗, not TIMED ✓.
"""

from __future__ import annotations

from simf.io.combat_log import ChallengeModeRun
from simf.ui.log_view import _format_run_outcome


def _mkrun(success, duration_ms, par_time_ms=None):
    return ChallengeModeRun(
        map_id=2526,
        map_name="Algeth'ar Academy",
        key_level=14,
        affixes=[9, 10, 147],
        start_time_s=0.0,
        end_time_s=duration_ms / 1000.0 if duration_ms else None,
        success=success,
        duration_ms=duration_ms,
        par_time_ms=par_time_ms,
    )


def test_completed_over_par_renders_as_over_time():
    """The original bug: AA +14 finished 52s past the 31:00 par. WoW
    reports success=1 because the key was completed, but the run was
    NOT timed. Label must say OVER TIME, not TIMED."""
    run = _mkrun(success=True, duration_ms=1_912_867, par_time_ms=1_860_000)
    assert run.is_timed() is False
    assert _format_run_outcome(run) == "OVER TIME ✗"


def test_completed_under_par_renders_as_timed():
    """A genuinely timed run — duration ≤ par — gets the ✓."""
    run = _mkrun(success=True, duration_ms=1_500_000, par_time_ms=1_860_000)
    assert run.is_timed() is True
    assert _format_run_outcome(run) == "TIMED ✓"


def test_abandoned_all_zeros_pattern_is_abandoned_not_depleted():
    """WoW writes ``CHALLENGE_MODE_END,<id>,0,0,0,...`` when the group
    leaves without finishing the key. Old code labeled this DEPLETED
    which is wrong on two counts: (a) depleted means completed past
    the timer, not abandoned; (b) ``success=0`` here doesn't mean
    "depleted," it means "abandoned." Label must say ABANDONED."""
    run = _mkrun(success=False, duration_ms=0, par_time_ms=1_860_000)
    assert run.is_abandoned() is True
    assert _format_run_outcome(run) == "ABANDONED"


def test_completed_unknown_par_renders_as_completed_no_glyph():
    """When ``par_time_ms`` is None (dungeon not yet verified against
    a real depleted log), the label drops the ✓/✗ and says COMPLETED.
    Better to not claim timing than to lie."""
    run = _mkrun(success=True, duration_ms=1_700_000, par_time_ms=None)
    assert run.is_timed() is None
    assert _format_run_outcome(run) == "COMPLETED"


def test_incomplete_no_end_renders_as_incomplete():
    """CHALLENGE_MODE_START with no matching END (log truncated mid-run
    or session still live) → success is None. Label says INCOMPLETE so
    the user knows the data is partial, not that the key was abandoned."""
    run = _mkrun(success=None, duration_ms=None, par_time_ms=1_860_000)
    assert run.is_abandoned() is False
    assert run.is_timed() is None
    assert _format_run_outcome(run) == "INCOMPLETE"


def test_par_time_loaded_from_dungeons_yaml_for_a_verified_map(monkeypatch):
    """``_par_time_for_map`` populates ``par_time_ms`` from whatever the LIVE
    catalog (``load_dungeon_catalog()``, the ``dungeons:`` key) says for a
    given map_id — decoupled from any specific real dungeon/season, since
    which map_ids carry a verified par shifts over time (e.g. the Season 2
    promotion of 2026-08-30 replaced every Season 1 map_id with a fresh, not-
    yet-verified Season 2 catalog — see ``docs/validation/
    s2_cross_spec_keylevel_check_2026_08_30.md``)."""
    import simf.core.constants as constants_mod

    monkeypatch.setattr(
        constants_mod,
        "load_dungeon_catalog",
        lambda: [{"id": "fake_dungeon", "map_id": 2526, "par_time_ms": 1_860_000}],
    )
    from simf.io.combat_log import _par_time_for_map

    assert _par_time_for_map(2526) == 1_860_000


def test_par_time_returns_none_for_unverified_maps(monkeypatch):
    """A map present in the catalog with ``par_time_ms: null`` returns None —
    the UI must then fall back to "COMPLETED" without a timing claim."""
    import simf.core.constants as constants_mod

    monkeypatch.setattr(
        constants_mod,
        "load_dungeon_catalog",
        lambda: [{"id": "fake_dungeon", "map_id": 2805, "par_time_ms": None}],
    )
    from simf.io.combat_log import _par_time_for_map

    assert _par_time_for_map(2805) is None


def test_par_time_returns_none_for_a_map_not_in_the_live_catalog():
    """A map_id absent from the live catalog entirely (e.g. an OLD log from a
    dungeon archived out of ``dungeons:`` by a season promotion — see
    ``season_1_catalog:`` in ``dungeons.yaml``) must degrade to None, not
    raise — the caller already treats None as "don't make a timing claim"."""
    from simf.io.combat_log import _par_time_for_map

    assert _par_time_for_map(999_999) is None


def test_par_time_returns_none_for_unknown_maps():
    """A map_id we don't track at all returns None too."""
    from simf.io.combat_log import _par_time_for_map

    assert _par_time_for_map(99_999_999) is None
