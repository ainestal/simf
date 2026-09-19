"""Disk cache for sim-derived survivability marginals (2026-06-13).

The marginals compute costs 10-20s on the Pi and is paid on every cold demo
load; this disk cache makes the 2nd+ cold load on a machine instant. Tests
exercise the pure module against a tmp dir — no Streamlit, no sim.
"""

from __future__ import annotations

import json

import pytest

from simf.core import marginals_cache

# Mirrors the shape of ui.app._char_marginals_signature: spec, race, talents
# (str), stat ratings (int), and the trailing constants_version (int).
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
)
_MARGINALS = {
    "stamina": {"p": 1.23, "m": 1.10},
    "armor_from_gear": {"p": 4.56, "m": 0.0},
    "haste_rating": {"p": 0.0, "m": 0.0},
}


@pytest.fixture
def cache(tmp_path):
    return tmp_path / "marginals_cache"


def test_round_trip_returns_stored_marginals(cache):
    marginals_cache.store(_SIG, _MARGINALS, override_dir=cache)
    got = marginals_cache.load(_SIG, override_dir=cache)
    assert got == _MARGINALS


def test_round_trip_survives_a_nested_tuple_signature_element(cache):
    """Regression for a real bug (PR #316 → caught by PR #317's tests,
    fixed same session): `_char_marginals_signature` gained a nested-tuple
    element (`tuple(sorted(char.active_buff_spell_ids or ()))`). A bare
    `list(signature)` only converts the OUTER tuple to a list — the nested
    tuple element stays a tuple, while the same position read back from
    JSON is always a list (JSON has no tuple type). `() != []` in Python,
    so the stored-signature comparison in `load`/`load_ci` failed for
    EVERY signature carrying that element — a permanent, silent cache
    miss (a miss degrades to "recompute," never raises) that shipped
    invisibly until a later test happened to exercise the round trip.
    Both an empty and a non-empty nested tuple must round-trip.
    """
    sig_empty_tuple = (*_SIG, ())
    marginals_cache.store(sig_empty_tuple, _MARGINALS, override_dir=cache)
    assert marginals_cache.load(sig_empty_tuple, override_dir=cache) == _MARGINALS

    sig_nonempty_tuple = (*_SIG, (202770, 451230))
    marginals_cache.store(sig_nonempty_tuple, _MARGINALS, override_dir=cache)
    assert marginals_cache.load(sig_nonempty_tuple, override_dir=cache) == _MARGINALS


def test_miss_when_nothing_stored(cache):
    assert marginals_cache.load(_SIG, override_dir=cache) is None


def test_constants_version_bump_is_a_miss(cache):
    """A different constants_version (last tuple element) → different key →
    the stale entry is never served."""
    marginals_cache.store(_SIG, _MARGINALS, override_dir=cache)
    bumped = (*_SIG[:-1], _SIG[-1] + 1)
    assert marginals_cache.load(bumped, override_dir=cache) is None
    # The original signature still hits.
    assert marginals_cache.load(_SIG, override_dir=cache) == _MARGINALS


def test_collision_guard_rejects_mismatched_signature(cache):
    """If two signatures ever shared a hash, the stored-signature re-check must
    refuse to serve the wrong character's marginals."""
    marginals_cache.store(_SIG, _MARGINALS, override_dir=cache)
    # Overwrite the on-disk signature with a different one, keeping the file.
    path = next(cache.glob("*.json"))
    blob = json.loads(path.read_text())
    blob["signature"] = ["different_character", "human", "other", 1, 2, 3, 4, 5, 6, 7, 8, 9]
    path.write_text(json.dumps(blob))
    assert marginals_cache.load(_SIG, override_dir=cache) is None


def test_corrupt_json_is_a_graceful_miss(cache):
    marginals_cache.store(_SIG, _MARGINALS, override_dir=cache)
    next(cache.glob("*.json")).write_text("{not valid json")
    assert marginals_cache.load(_SIG, override_dir=cache) is None


def test_numpy_floats_are_coerced_for_json(cache):
    """compute_survivability_marginals can emit numpy floats; storing must not
    raise and the reload must be plain floats."""
    np = pytest.importorskip("numpy")
    marg = {"stamina": {"p": np.float64(1.5), "m": np.float64(2.5)}}
    marginals_cache.store(_SIG, marg, override_dir=cache)
    got = marginals_cache.load(_SIG, override_dir=cache)
    assert got == {"stamina": {"p": 1.5, "m": 2.5}}
    assert isinstance(got["stamina"]["p"], float)


def test_prune_evicts_oldest_beyond_cap(cache):
    """Keep the dir bounded — oldest entries (by mtime) evicted past the cap."""
    import os
    import time

    cache.mkdir(parents=True)
    # Seed cap+3 files with strictly increasing mtimes.
    keys = []
    base = time.time()
    for i in range(8):
        sig = (*_SIG[:-1], 1000 + i)
        marginals_cache.store(sig, _MARGINALS, override_dir=cache, cap=10_000)
        keys.append(sig)
    files = list(cache.glob("*.json"))
    for i, p in enumerate(sorted(files)):
        os.utime(p, (base + i, base + i))
    # Now store one more with cap=5 → prune to 5 newest.
    marginals_cache.store((*_SIG[:-1], 9999), _MARGINALS, override_dir=cache, cap=5)
    assert len(list(cache.glob("*.json"))) == 5


def test_store_is_best_effort_on_unwritable_dir(tmp_path):
    """A disk error must not propagate — the caller recomputes instead."""
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a dir")
    # Asking to use a path under a regular file → mkdir raises internally; store swallows it.
    marginals_cache.store(_SIG, _MARGINALS, override_dir=blocker / "sub")
    assert marginals_cache.load(_SIG, override_dir=blocker / "sub") is None


def test_env_var_overrides_default_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMF_MARGINALS_CACHE_DIR", str(tmp_path / "envdir"))
    assert marginals_cache.cache_dir() == tmp_path / "envdir"
    marginals_cache.store(_SIG, _MARGINALS)
    assert marginals_cache.load(_SIG) == _MARGINALS


_CI = {
    "stamina": {"p": None, "m": None},
    "armor_from_gear": {"p": (400.0, 600.0), "m": None},
    "haste_rating": {"p": None, "m": None},
}


def test_ci_round_trips_through_store_and_load_ci(cache):
    marginals_cache.store(_SIG, _MARGINALS, ci=_CI, override_dir=cache)
    assert marginals_cache.load_ci(_SIG, override_dir=cache) == _CI
    # load() keeps its long-standing bare-marginals-dict contract untouched.
    assert marginals_cache.load(_SIG, override_dir=cache) == _MARGINALS


def test_load_ci_is_none_when_never_stored(cache):
    marginals_cache.store(_SIG, _MARGINALS, override_dir=cache)  # no ci= passed
    assert marginals_cache.load_ci(_SIG, override_dir=cache) is None


def test_load_ci_is_none_on_old_format_entry_missing_ci_key(cache):
    """A cache entry written before this feature existed has no `ci` key at
    all — must degrade to None, not KeyError."""
    cache.mkdir(parents=True)
    path = cache / f"{marginals_cache._signature_key(_SIG)}.json"
    path.write_text(json.dumps({"signature": list(_SIG), "marginals": _MARGINALS}))
    assert marginals_cache.load_ci(_SIG, override_dir=cache) is None
    assert marginals_cache.load(_SIG, override_dir=cache) == _MARGINALS


def test_ci_numpy_floats_are_coerced_for_json(cache):
    np = pytest.importorskip("numpy")
    ci = {"stamina": {"p": (np.float64(1.5), np.float64(2.5)), "m": None}}
    marginals_cache.store(_SIG, _MARGINALS, ci=ci, override_dir=cache)
    got = marginals_cache.load_ci(_SIG, override_dir=cache)
    assert got == {"stamina": {"p": (1.5, 2.5), "m": None}}
    assert isinstance(got["stamina"]["p"][0], float)


def test_marginals_for_wiring_exists():
    """Tripwire: _marginals_for must consult the disk cache (load + store).
    SIMF_FAST_MARGINALS short-circuits the compute in tests, so this guards the
    wiring that can't be exercised end-to-end without a real 10-20s sim."""
    from pathlib import Path

    # `_marginals_for` moved out of app.py into `simf.ui.marginals` (panel
    # split, PR 2); read the wiring from its new home.
    src = (Path(__file__).parent.parent / "src" / "simf" / "ui" / "marginals.py").read_text()
    body = src.split("def _marginals_for(")[1].split("\ndef ")[0]
    assert "marginals_cache.load(" in body
    assert "marginals_cache.store(" in body


def test_marginals_for_wires_ci_alongside_marginals():
    """Same tripwire shape as test_marginals_for_wiring_exists, for the
    2026-07-08 bootstrap-CI addition: a disk-cache hit must also read the
    CI (load_ci), and a fresh compute must persist it (ci=) — otherwise
    the CI silently never survives past the first cold load."""
    from pathlib import Path

    src = (Path(__file__).parent.parent / "src" / "simf" / "ui" / "marginals.py").read_text()
    body = src.split("def _marginals_for(")[1].split("\ndef ")[0]
    assert "marginals_cache.load_ci(" in body
    assert "ci=ci" in body
    assert "def _marginals_ci_for(" in src
