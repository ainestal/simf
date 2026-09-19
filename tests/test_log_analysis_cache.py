"""Tests for the disk-backed log-analysis cache.

Covers the eight invalidation / eviction / robustness cases the spec
calls out, plus a round-trip on a real dataclass shape so we don't
regress picklability of the production payload.
"""

from __future__ import annotations

import os
import pickle
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from simf.io import log_analysis_cache
from simf.io.log_analysis_cache import (
    clear_cache,
    get_cached,
    get_or_compute,
    index_snapshot,
    make_key,
    put_cached,
)


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    """Fresh on-disk cache for every test. Pointed at tmp_path so the
    real ``~/.simf/log_cache/`` is never touched by the suite."""
    d = tmp_path / "log_cache"
    return d  # not created — exercises lazy mkdir


@pytest.fixture
def log_file(tmp_path: Path) -> Path:
    """A tiny synthetic combat-log surrogate. Content doesn't matter —
    the cache keys off the file's mtime/size, not its contents, and
    the producer is a closure under our control."""
    p = tmp_path / "WoWCombatLog-fake.txt"
    p.write_text("CHALLENGE_MODE_START fake\n" * 10)
    return p


@dataclass
class Payload:
    """Stand-in for the production analysis bundle — same dataclass shape,
    enough fields to exercise pickle round-trip."""

    summary: str
    deaths: list[int]
    notes: dict[str, float]


def _make_payload(tag: str = "v1") -> Payload:
    return Payload(summary=f"summary-{tag}", deaths=[1, 2, 3], notes={"a": 1.5, "b": 2.5})


def _patch_versions(monkeypatch, cv: int, av: int) -> None:
    """Pin both versions to deterministic values for the test."""
    monkeypatch.setattr(
        log_analysis_cache,
        "_current_versions",
        lambda: (cv, av),
    )


# ─── hit / miss basics ────────────────────────────────────────────────────────


def test_cache_hit_returns_stored_value_without_recomputing(monkeypatch, log_file, cache_dir):
    """Same (path, mtime, size, both versions, target, run) returns the
    stored payload and the producer is NOT called the second time."""
    _patch_versions(monkeypatch, cv=22, av=1)
    calls = {"n": 0}

    def producer():
        calls["n"] += 1
        return _make_payload("first")

    first = get_or_compute(log_file, "Brutoh", 0, producer, cache_dir=cache_dir)
    assert calls["n"] == 1
    assert first == _make_payload("first")

    second = get_or_compute(log_file, "Brutoh", 0, producer, cache_dir=cache_dir)
    assert calls["n"] == 1, "Producer should NOT run on a cache hit"
    assert second == first


def test_get_cached_returns_none_on_first_lookup(monkeypatch, log_file, cache_dir):
    _patch_versions(monkeypatch, cv=22, av=1)
    assert get_cached(log_file, "Brutoh", 0, cache_dir=cache_dir) is None


# ─── invalidation: per-file ───────────────────────────────────────────────────


def test_mtime_change_invalidates_entry(monkeypatch, log_file, cache_dir):
    """Touching the log file shifts mtime_ns → the cache misses."""
    _patch_versions(monkeypatch, cv=22, av=1)
    put_cached(log_file, "Brutoh", 0, _make_payload("orig"), cache_dir=cache_dir)
    assert get_cached(log_file, "Brutoh", 0, cache_dir=cache_dir) == _make_payload("orig")

    # Bump mtime by a real-clock second to ensure mtime_ns moves even on
    # filesystems with coarse mtime resolution (NFS, FAT-mounted shares).
    time.sleep(1.0)
    log_file.touch()
    assert get_cached(log_file, "Brutoh", 0, cache_dir=cache_dir) is None


def test_size_change_invalidates_entry(monkeypatch, log_file, cache_dir):
    """Appending bytes to the log moves the size → cache misses even if
    mtime resolution somehow didn't advance."""
    _patch_versions(monkeypatch, cv=22, av=1)
    put_cached(log_file, "Brutoh", 0, _make_payload("orig"), cache_dir=cache_dir)

    # Force size drift while keeping mtime_ns pinned to the original to
    # isolate the size-only invalidation path. os.utime restores the
    # original mtime after the append.
    original_stat = log_file.stat()
    with log_file.open("a") as f:
        f.write("more data")
    os.utime(log_file, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    assert log_file.stat().st_mtime_ns == original_stat.st_mtime_ns, (
        "test precondition — mtime should be pinned"
    )
    assert log_file.stat().st_size != original_stat.st_size

    assert get_cached(log_file, "Brutoh", 0, cache_dir=cache_dir) is None


# ─── invalidation: global version bumps ───────────────────────────────────────


def test_constants_version_bump_invalidates_every_entry(monkeypatch, tmp_path, cache_dir):
    """A constants_version bump unlinks EVERY stored entry, not just the
    one we happen to ask about next."""
    _patch_versions(monkeypatch, cv=22, av=1)
    # Three independent (log, target, run) triples populate three entries.
    paths: list[Path] = []
    for i in range(3):
        p = tmp_path / f"log_{i}.txt"
        p.write_text(f"log {i}")
        paths.append(p)
        put_cached(p, f"Brutoh{i}", 0, _make_payload(f"e{i}"), cache_dir=cache_dir)

    assert len(index_snapshot(cache_dir=cache_dir)) == 3

    # Bump constants_version. The next access — even for a *different*
    # entry — must wipe out everything.
    _patch_versions(monkeypatch, cv=23, av=1)
    assert get_cached(paths[0], "Brutoh0", 0, cache_dir=cache_dir) is None

    # And the other two should now be gone from the index as a side effect.
    surviving = index_snapshot(cache_dir=cache_dir)
    assert surviving == []
    # Payload files are unlinked too.
    pkl_files = list(cache_dir.glob("*.pkl"))
    assert pkl_files == []


def test_analysis_version_bump_invalidates_every_entry(monkeypatch, tmp_path, cache_dir):
    """The analysis_version dimension is independent — bumping it (with
    constants_version frozen) must invalidate every entry too."""
    _patch_versions(monkeypatch, cv=22, av=1)
    paths: list[Path] = []
    for i in range(3):
        p = tmp_path / f"log_{i}.txt"
        p.write_text(f"log {i}")
        paths.append(p)
        put_cached(p, f"Brutoh{i}", 0, _make_payload(f"e{i}"), cache_dir=cache_dir)
    assert len(index_snapshot(cache_dir=cache_dir)) == 3

    _patch_versions(monkeypatch, cv=22, av=2)
    assert get_cached(paths[1], "Brutoh1", 0, cache_dir=cache_dir) is None
    assert index_snapshot(cache_dir=cache_dir) == []


# ─── LRU eviction ─────────────────────────────────────────────────────────────


def test_lru_eviction_at_entry_cap(monkeypatch, tmp_path, cache_dir):
    """With MAX_ENTRIES = 3 (monkeypatched), writing 4 entries drops the
    oldest. The most recently written entry stays at the head."""
    _patch_versions(monkeypatch, cv=22, av=1)
    monkeypatch.setattr(log_analysis_cache, "MAX_ENTRIES", 3)

    paths: list[Path] = []
    for i in range(4):
        p = tmp_path / f"log_{i}.txt"
        p.write_text(f"log {i}")
        paths.append(p)
        put_cached(p, f"Brutoh{i}", 0, _make_payload(f"e{i}"), cache_dir=cache_dir)

    index = index_snapshot(cache_dir=cache_dir)
    assert len(index) == 3
    # The oldest (index 0) was evicted; the newest (index 3) is at the head.
    targets_remaining = {e["target"] for e in index}
    assert "Brutoh0" not in targets_remaining
    assert "Brutoh3" in targets_remaining
    assert index[0]["target"] == "Brutoh3", "MRU should be at the head"


def test_lru_eviction_at_byte_cap(monkeypatch, tmp_path, cache_dir):
    """Even when the entry count is well under MAX_ENTRIES, exceeding
    MAX_BYTES evicts oldest entries until total payload bytes fit."""
    _patch_versions(monkeypatch, cv=22, av=1)
    monkeypatch.setattr(log_analysis_cache, "MAX_ENTRIES", 100)
    # Force a tiny byte cap. Each pickled Payload is ~150-200 bytes, so
    # 400 bytes holds ~2 entries; the 3rd write must evict the first.
    monkeypatch.setattr(log_analysis_cache, "MAX_BYTES", 400)

    paths: list[Path] = []
    for i in range(3):
        p = tmp_path / f"log_{i}.txt"
        p.write_text(f"log {i}")
        paths.append(p)
        put_cached(p, f"Brutoh{i}", 0, _make_payload(f"e{i}"), cache_dir=cache_dir)

    index = index_snapshot(cache_dir=cache_dir)
    total_bytes = sum(int(e.get("payload_bytes", 0)) for e in index)
    assert total_bytes <= 400, f"byte cap busted: {total_bytes}"
    # The oldest entry should be gone; the newest is at the head.
    targets_remaining = {e["target"] for e in index}
    assert "Brutoh0" not in targets_remaining
    assert "Brutoh2" in targets_remaining

    # And the evicted entry's payload file is unlinked, not just dropped
    # from the index — otherwise the cache dir grows unbounded.
    surviving_hashes = {e["key_hash"] for e in index}
    pkl_hashes = {p.stem for p in cache_dir.glob("*.pkl")}
    assert pkl_hashes == surviving_hashes


# ─── directory + corruption robustness ────────────────────────────────────────


def test_missing_cache_dir_created_lazily(monkeypatch, log_file, cache_dir):
    """Cache dir does not exist at start; first put_cached() creates it."""
    _patch_versions(monkeypatch, cv=22, av=1)
    assert not cache_dir.exists()
    # get_cached on a missing dir is a clean miss (no raise).
    assert get_cached(log_file, "Brutoh", 0, cache_dir=cache_dir) is None
    assert not cache_dir.exists(), "get_cached should not create the dir"

    put_cached(log_file, "Brutoh", 0, _make_payload("x"), cache_dir=cache_dir)
    assert cache_dir.exists()
    assert get_cached(log_file, "Brutoh", 0, cache_dir=cache_dir) == _make_payload("x")


def test_corrupt_cache_file_falls_through_to_recompute(monkeypatch, log_file, cache_dir):
    """A garbled pickle on disk must NOT raise to the UI. The cache
    transparently recomputes via the producer and overwrites the file."""
    _patch_versions(monkeypatch, cv=22, av=1)
    put_cached(log_file, "Brutoh", 0, _make_payload("good"), cache_dir=cache_dir)

    # Find the entry's payload file and corrupt it in-place.
    index = index_snapshot(cache_dir=cache_dir)
    assert len(index) == 1
    payload_path = cache_dir / f"{index[0]['key_hash']}.pkl"
    assert payload_path.exists()
    payload_path.write_bytes(b"not a pickle, absolutely garbage \x00\x01\x02")

    # get_cached should swallow the corruption and return None.
    assert get_cached(log_file, "Brutoh", 0, cache_dir=cache_dir) is None
    # The bad file should be unlinked.
    assert not payload_path.exists()

    # Now get_or_compute should silently recompute and write the new payload.
    calls = {"n": 0}

    def producer():
        calls["n"] += 1
        return _make_payload("recovered")

    out = get_or_compute(log_file, "Brutoh", 0, producer, cache_dir=cache_dir)
    assert calls["n"] == 1
    assert out == _make_payload("recovered")

    # And the next access is a hit again.
    again = get_or_compute(log_file, "Brutoh", 0, producer, cache_dir=cache_dir)
    assert calls["n"] == 1
    assert again == _make_payload("recovered")


# ─── round-trip + key ────────────────────────────────────────────────────────


def test_make_key_includes_stat_and_versions(monkeypatch, log_file):
    """The key carries the resolved abspath + the live versions."""
    _patch_versions(monkeypatch, cv=22, av=1)
    key = make_key(log_file, "Brutoh", 3)
    assert key.target == "Brutoh"
    assert key.run_index == 3
    assert key.constants_version == 22
    assert key.analysis_version == 1
    assert key.abspath == str(log_file.resolve())
    assert key.size == log_file.stat().st_size
    assert key.mtime_ns == log_file.stat().st_mtime_ns


def test_payload_pickles_at_highest_protocol(monkeypatch, log_file, cache_dir):
    """The payload file actually exists, parses with pickle, and round-trips."""
    _patch_versions(monkeypatch, cv=22, av=1)
    payload = _make_payload("rt")
    put_cached(log_file, "Brutoh", 0, payload, cache_dir=cache_dir)
    index = index_snapshot(cache_dir=cache_dir)
    pkl_path = cache_dir / f"{index[0]['key_hash']}.pkl"
    raw = pkl_path.read_bytes()
    # HIGHEST_PROTOCOL bytes-start markers on CPython 3.13 → protocol 5
    # (the on-disk first byte is 0x80 0x05). Don't assert the exact byte
    # (Python could bump), just confirm we round-trip what we wrote.
    round_tripped = pickle.loads(raw)  # noqa: S301 — same local-cache trust boundary the source module documents
    assert round_tripped == payload


def test_clear_cache_wipes_everything(monkeypatch, log_file, cache_dir):
    """``clear_cache`` deletes index + every payload file."""
    _patch_versions(monkeypatch, cv=22, av=1)
    put_cached(log_file, "Brutoh", 0, _make_payload("x"), cache_dir=cache_dir)
    assert len(index_snapshot(cache_dir=cache_dir)) == 1

    clear_cache(cache_dir=cache_dir)
    assert index_snapshot(cache_dir=cache_dir) == []
    assert list(cache_dir.glob("*.pkl")) == []
