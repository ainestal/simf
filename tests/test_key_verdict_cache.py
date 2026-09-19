"""Tests for the key-level-verdict disk cache (`core.key_verdict_cache`).

Mirrors `test_marginals_cache.py`'s structure — same round-trip / collision
/ corruption / eviction / env-var coverage, adapted for the `KeyLevelVerdict`
dataclass shape instead of a bare marginals dict. Pure stdlib against a tmp
dir — no Streamlit, no sim.
"""

from __future__ import annotations

import json

import pytest

from simf.core import key_verdict_cache
from simf.core.key_level_verdict import KeyLevelPoint, KeyLevelVerdict

# Mirrors the shape `ui.verdict._key_verdict_signature` builds: the shared
# marginals-signature base (spec / race / talents / stat ratings /
# constants_version) plus this sweep's own compute parameters (iterations /
# affix / profile names).
_SIG = (
    "protection_warrior",
    "earthen",
    "kiratank-defensive",
    34176,
    5015,
    2182,
    2318,
    1391,
    1608,
    296,
    0,
    18,
    200,
    "fortified",
    "m+_boss_tankbuster",
    "m+_high_key_healer",
)


def _verdict() -> KeyLevelVerdict:
    return KeyLevelVerdict(
        points=[
            KeyLevelPoint(
                key_level=14,
                damage_multiplier=1.26,
                death_rate=0.03,
                mean_dtps=31_000.0,
                p99_5s_window=120_000.0,
                band="comfortable",
                mean_hrps=3_500.0,
                normalized_tank_score=0.82,
                p99_10s_window=180_000.0,
                p99_15s_window=210_000.0,
                sample_max_hp=450_000.0,
                p5_min_hp_pct=0.35,
                sample_duration_s=300.0,
                sample_damage_timeline=[(1.0, 5_000.0), (2.5, 3_200.0)],
                sample_heal_timeline=[(1.5, 4_000.0)],
                sample_died=False,
                sample_time_to_die_s=None,
            ),
            KeyLevelPoint(
                key_level=18,
                damage_multiplier=1.72,
                death_rate=0.30,
                mean_dtps=49_000.0,
                p99_5s_window=210_000.0,
                band="danger",
                sample_died=True,
                sample_time_to_die_s=87.5,
            ),
        ],
        comfortable_max=14,
        prog_ceiling=16,
        affix="fortified",
    )


@pytest.fixture
def cache(tmp_path):
    return tmp_path / "key_verdict_cache"


def test_round_trip_returns_equivalent_verdict(cache):
    verdict = _verdict()
    key_verdict_cache.store(_SIG, verdict, override_dir=cache)
    got = key_verdict_cache.load(_SIG, override_dir=cache)
    assert got == verdict


def test_round_trip_preserves_every_point_field(cache):
    """Not just equality — the encode/decode must be lossless field-by-field,
    including the timeline tuples and the `None`-able tail-risk fields."""
    verdict = _verdict()
    key_verdict_cache.store(_SIG, verdict, override_dir=cache)
    got = key_verdict_cache.load(_SIG, override_dir=cache)
    assert got is not None
    p0, p1 = got.points
    assert p0.sample_damage_timeline == [(1.0, 5_000.0), (2.5, 3_200.0)]
    assert p0.sample_heal_timeline == [(1.5, 4_000.0)]
    assert isinstance(p0.sample_damage_timeline[0], tuple)
    assert p0.p5_min_hp_pct == 0.35
    assert p1.sample_died is True
    assert p1.sample_time_to_die_s == 87.5
    assert p1.p5_min_hp_pct is None
    assert got.comfortable_max == 14
    assert got.prog_ceiling == 16
    assert got.affix == "fortified"


def test_miss_when_nothing_stored(cache):
    assert key_verdict_cache.load(_SIG, override_dir=cache) is None


def test_signature_change_is_a_miss(cache):
    """A different trailing element (e.g. iterations) → different key → the
    stale entry is never served — same self-invalidating shape as
    marginals_cache's constants_version check."""
    key_verdict_cache.store(_SIG, _verdict(), override_dir=cache)
    bumped = (*_SIG[:-4], 300, *_SIG[-3:])
    assert key_verdict_cache.load(bumped, override_dir=cache) is None
    # The original signature still hits.
    assert key_verdict_cache.load(_SIG, override_dir=cache) is not None


def test_collision_guard_rejects_mismatched_signature(cache):
    key_verdict_cache.store(_SIG, _verdict(), override_dir=cache)
    path = next(cache.glob("*.json"))
    blob = json.loads(path.read_text())
    blob["signature"] = ["different_character", "human", "other", 1, 2, 3]
    path.write_text(json.dumps(blob))
    assert key_verdict_cache.load(_SIG, override_dir=cache) is None


def test_corrupt_json_is_a_graceful_miss(cache):
    key_verdict_cache.store(_SIG, _verdict(), override_dir=cache)
    next(cache.glob("*.json")).write_text("{not valid json")
    assert key_verdict_cache.load(_SIG, override_dir=cache) is None


def test_malformed_verdict_payload_is_a_graceful_miss(cache):
    """A `verdict` key that isn't a dict, or one missing a required field,
    must degrade to None — never raise up into the caller."""
    cache.mkdir(parents=True)
    path = cache / f"{key_verdict_cache._signature_key(_SIG)}.json"
    path.write_text(json.dumps({"signature": list(_SIG), "verdict": "not-a-dict"}))
    assert key_verdict_cache.load(_SIG, override_dir=cache) is None

    path.write_text(
        json.dumps({"signature": list(_SIG), "verdict": {"points": [{"band": "comfortable"}]}})
    )
    assert key_verdict_cache.load(_SIG, override_dir=cache) is None


def test_numpy_floats_are_coerced_for_json(cache):
    np = pytest.importorskip("numpy")
    verdict = KeyLevelVerdict(
        points=[
            KeyLevelPoint(
                key_level=14,
                damage_multiplier=np.float64(1.26),
                death_rate=np.float64(0.03),
                mean_dtps=np.float64(31_000.0),
                p99_5s_window=np.float64(120_000.0),
                band="comfortable",
            )
        ],
        comfortable_max=14,
        prog_ceiling=None,
        affix="fortified",
    )
    key_verdict_cache.store(_SIG, verdict, override_dir=cache)
    got = key_verdict_cache.load(_SIG, override_dir=cache)
    assert got is not None
    assert isinstance(got.points[0].death_rate, float)
    assert got.points[0].death_rate == 0.03


def test_prune_evicts_oldest_beyond_cap(cache):
    import os
    import time

    cache.mkdir(parents=True)
    verdict = _verdict()
    base = time.time()
    for i in range(8):
        sig = (*_SIG[:-1], f"profile-{i}")
        key_verdict_cache.store(sig, verdict, override_dir=cache, cap=10_000)
    files = list(cache.glob("*.json"))
    for i, p in enumerate(sorted(files)):
        os.utime(p, (base + i, base + i))
    key_verdict_cache.store((*_SIG[:-1], "profile-final"), verdict, override_dir=cache, cap=5)
    assert len(list(cache.glob("*.json"))) == 5


def test_store_is_best_effort_on_unwritable_dir(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a dir")
    key_verdict_cache.store(_SIG, _verdict(), override_dir=blocker / "sub")
    assert key_verdict_cache.load(_SIG, override_dir=blocker / "sub") is None


def test_env_var_overrides_default_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMF_KEY_VERDICT_CACHE_DIR", str(tmp_path / "envdir"))
    assert key_verdict_cache.cache_dir() == tmp_path / "envdir"
    verdict = _verdict()
    key_verdict_cache.store(_SIG, verdict)
    assert key_verdict_cache.load(_SIG) == verdict


def test_verdict_panel_wiring_exists():
    """Tripwire: `_render_key_level_verdict_panel_body` must consult the
    disk cache (load + store), gated on the demo-slug flag for the write —
    same tripwire shape as `test_marginals_cache.py`'s
    `test_marginals_for_wiring_exists`, guarding wiring that AppTest-level
    tests exercise but a source-text check catches even faster."""
    from pathlib import Path

    src = (Path(__file__).parent.parent / "src" / "simf" / "ui" / "verdict.py").read_text()
    body = src.split("def _render_key_level_verdict_panel_body(")[1].split("\ndef ")[0]
    assert "key_verdict_cache.load(" in body
    assert "key_verdict_cache.store(" in body
    assert '_ss().get("_loaded_demo_slug")' in body
