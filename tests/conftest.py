"""Pytest config + engine fixtures for simf.

Fixtures defined here are auto-discovered by every test in `tests/`. They
target the **engine** (Character, DamageEvent, HealingProfile) — anything UI
or Streamlit-specific belongs in a more local conftest because the UI is
under review and will be replaced.

When adding a new engine test, prefer these fixtures over inlined Character
construction. The Brutoh stat block is the canonical M+ tank for calibration
and appears in 3+ existing tests as `_make_brutoh()` — those can migrate to
the `brutoh` fixture incrementally.

Adds a ``slow`` marker that is skipped by default. Run slow tests with
``pytest --run-slow``. Used by tractability benchmarks that take seconds.
"""

from __future__ import annotations

import os

import pytest

from simf.core.character import Character
from simf.core.events import AttackType, DamageEvent, DamageSchool
from simf.core.profiles import HealingProfile

# AppTest smoke tests hit a 30 s timeout. The sim-derived stat-weights compute
# (Phase 2.7) takes ~15 s on its own and longer under xdist contention, which
# pushes the whole UI render past the timeout. Tell `_marginals_for` in the
# UI to use the closed-form path instead — survivability-weight correctness
# has its own dedicated tests in `tests/test_survivability_weights.py` that
# call `compute_survivability_marginals` directly.
os.environ.setdefault("SIMF_FAST_MARGINALS", "1")


def pytest_addoption(parser):
    parser.addoption(
        "--run-slow",
        action="store_true",
        default=False,
        help="Run tests marked as slow (benchmarks).",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: marks tests as slow (deselect by default)")
    config.addinivalue_line(
        "markers",
        "live_wcl: opt out of the autouse fetch_live_world_max_keys stub "
        "(tests that mock the live path themselves)",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-slow"):
        return
    skip_slow = pytest.mark.skip(reason="needs --run-slow")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)


@pytest.fixture(autouse=True)
def _disable_live_wcl_rankings(request, monkeypatch):
    """Disable the live WCL rankings layer for every test by default.

    The world-ceilings loader (`simf.data.world_ceilings`) consults
    `simf.io.wcl_rankings.fetch_live_world_max_keys` to refresh the
    `world_max_key` field from the live WCL API. On dev hosts with
    `~/.simf/wcl_config.yaml` configured, that would make the test
    suite hit the network — non-hermetic and slow. Tests that
    explicitly need to exercise the live path opt out by patching
    `fetch_live_world_max_keys` themselves (see
    `tests/test_wcl_rankings.py`).

    Marker `live_wcl` lets a test opt out of this guard if it really
    wants to talk to WCL (none currently do).
    """
    if "live_wcl" in request.keywords:
        return
    monkeypatch.setattr(
        "simf.io.wcl_rankings.fetch_live_world_max_keys",
        lambda *a, **kw: None,
        raising=False,
    )
    monkeypatch.setattr(
        "simf.io.wcl_rankings.fetch_live_broad_completion_counts",
        lambda *a, **kw: None,
        raising=False,
    )
    monkeypatch.setattr(
        "simf.io.wcl_rankings.read_cached_cross_encounter_counts",
        lambda *a, **kw: {},
        raising=False,
    )
    # The world_ceilings loader caches its first-load result; reset so
    # the stub takes effect even if a prior test populated the cache.
    try:
        from simf.data.world_ceilings import reset_cache

        reset_cache()
    except Exception:  # noqa: S110 — best-effort fallback
        pass


@pytest.fixture(autouse=True)
def _isolate_key_verdict_cache_dir(monkeypatch, tmp_path):
    """Every test gets its own key-level-verdict disk cache directory.

    `ui/verdict.py`'s panel unconditionally checks `core.key_verdict_cache`
    on every render once a character is loaded — there's no
    `SIMF_FAST_MARGINALS`-style short-circuit for it (that env var bypasses
    `_marginals_for` entirely, so `marginals_cache` never touches the real
    default dir during a test run either — see that fixture's absence being
    the reason this one exists explicitly). Without this redirect, the many
    AppTest UI tests that load a character and render the gear surface
    would read from and write to the REAL `~/.simf/key_verdict_cache/` on
    whatever machine runs the suite: non-hermetic, and a real risk of a
    stale prior-run entry leaking a false "already computed" cache hit into
    an unrelated test. Tests exercising the cache module directly
    (`test_key_verdict_cache.py`) pass their own `override_dir=` and are
    unaffected; a test that wants to assert on THIS directory's contents
    can just use the `tmp_path` fixture itself (same value, per-test).
    """
    monkeypatch.setenv("SIMF_KEY_VERDICT_CACHE_DIR", str(tmp_path / "key_verdict_cache"))


# ─── Character factories ──────────────────────────────────────────────────────


@pytest.fixture
def brutoh() -> Character:
    """The canonical Brutoh stat block — Prot Warrior, Earthen, ~+15 M+ gear.

    Mirrors examples/brutoh.simc so engine tests run against the same numbers
    the calibration was tuned on. haste/crit/versatility are in real level-90
    rating units (44/46/54 per pct — see the 2026-06-28 recalibration,
    docs/calibration.md) as of 2026-07-12; this fixture was found still
    carrying the pre-rescale self-fit values (rating_per_pct=100 for all
    three) — same in-game %, wrong units — while adding the Riposte
    crit->parry mechanic (character.py:base_parry). That path has no
    diminishing-returns curve to mask an oversized crit_rating the way
    crit_pct()/haste_pct()/versatility_pct() do, so the stale ~2.2x-inflated
    crit_rating suddenly produced a real, large effect it never had before.
    mastery_rating is untouched by the rescale (per-spec, stays ~100).
    """
    return Character(
        name="Brutoh",
        race="earthen",
        class_spec="protection_warrior",
        talents="kiratank-defensive",
        strength=2182,
        stamina=34176,
        armor_from_gear=5015,
        haste_rating=1020,
        crit_rating=640,
        mastery_rating=1608,
        versatility_rating=160,
        max_hp_override=751872,
    )


def make_warrior(
    *,
    name: str = "test_warrior",
    race: str = "earthen",
    strength: int = 2000,
    stamina: int = 30000,
    armor_from_gear: int = 5000,
    haste_rating: int = 0,
    crit_rating: int = 0,
    mastery_rating: int = 0,
    versatility_rating: int = 0,
    max_hp_override: int | None = 700_000,
    talents: str = "kiratank-defensive",
) -> Character:
    """Factory for Prot Warrior Characters — defaults are easy to override per test."""
    return Character(
        name=name,
        race=race,
        class_spec="protection_warrior",
        talents=talents,
        strength=strength,
        stamina=stamina,
        armor_from_gear=armor_from_gear,
        haste_rating=haste_rating,
        crit_rating=crit_rating,
        mastery_rating=mastery_rating,
        versatility_rating=versatility_rating,
        max_hp_override=max_hp_override,
    )


def make_paladin(
    *,
    armor_from_gear: int = 5000,
    versatility_rating: int = 0,
    mastery_rating: int = 0,
    max_hp_override: int | None = 8_000_000,
) -> Character:
    """Factory for Prot Paladin Characters."""
    return Character(
        name="test_pal",
        race="human",
        class_spec="protection_paladin",
        talents="default-paladin",
        strength=2000,
        stamina=80000,
        armor_from_gear=armor_from_gear,
        haste_rating=0,
        crit_rating=0,
        mastery_rating=mastery_rating,
        versatility_rating=versatility_rating,
        max_hp_override=max_hp_override,
    )


# ─── DamageEvent factories ────────────────────────────────────────────────────


def phys_event(
    *,
    time_s: float = 0.0,
    amount: float = 1_000_000.0,
    is_avoidable: bool = False,
    is_blockable: bool = False,
    attack_type: AttackType = "melee",
) -> DamageEvent:
    """Physical melee/ranged hit — defaults to unavoidable, unblockable."""
    return DamageEvent(
        time_s=time_s,
        source_id="test",
        school="physical",
        raw_amount=amount,
        attack_type=attack_type,
        is_avoidable=is_avoidable,
        is_blockable=is_blockable,
    )


def magic_event(
    *,
    time_s: float = 0.0,
    amount: float = 1_000_000.0,
    school: DamageSchool = "shadow",
) -> DamageEvent:
    """Magic spell hit — armor doesn't apply, school defaults to shadow."""
    return DamageEvent(
        time_s=time_s,
        source_id="test",
        school=school,
        raw_amount=amount,
        attack_type="spell",
        is_avoidable=False,
        is_blockable=False,
    )


# ─── HealingProfile factory ───────────────────────────────────────────────────


def simple_healer(
    *,
    baseline_hps_abs: float = 0.0,
    baseline_hps_pct_of_dtps: float = 0.0,
) -> HealingProfile:
    """Minimal HealingProfile — no externals, no reactive, just a flat HPS floor."""
    return HealingProfile(
        profile="test",
        baseline_hps_pct_of_dtps=baseline_hps_pct_of_dtps,
        baseline_hps_abs=baseline_hps_abs,
    )
