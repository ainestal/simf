"""Disk-backed cache for parsed combat-log analysis bundles.

Re-parsing a 100-230 MB WoW combat log on every UI rerun dominates the
log-analysis surface's latency on Pi-class hardware. This module memo-izes
the full analysis bundle (summary + deaths + mitigation audit + run events
+ segments) to ``~/.simf/log_cache/`` keyed by:

    (abspath, file_mtime_ns, file_size, constants_version, analysis_version,
     target, run_index)

A hit on all five (path/mtime/size/both versions) returns the same payload
the producer would have computed. Any miss falls through to the producer.

Storage layout
--------------
``~/.simf/log_cache/`` holds:

  - ``<sha256>.pkl`` — one file per cache entry, pickled at HIGHEST_PROTOCOL.
  - ``_index.json`` — ordered LRU list of {key_hash, abspath, mtime_ns, size,
    constants_version, analysis_version, target, run_index, payload_bytes,
    last_accessed_ns}. Tail of the list is the least recently used.

Invalidation
------------
- file mtime/size change → entry stale, miss + overwrite.
- ``constants_version`` bump in ``data/constants.yaml`` → engine math
  changed; every entry whose stored version mismatches is unlinked on next
  access.
- ``analysis_version`` bump → analysis pipeline shape changed; every entry
  with a mismatched stored version is unlinked. This is SEPARATE from
  ``constants_version`` (independent lifecycle — see constants.yaml).

LRU eviction
------------
Runs after every successful write. Bounded by the smaller of:

  - 20 entries (``MAX_ENTRIES``), or
  - 500 MB total on-disk size (``MAX_BYTES``).

Both caps are module-level constants so tests can monkeypatch them down
to KB-scale without writing real big payloads.

Corruption
----------
A corrupt or unreadable pickle is treated like a miss — the caller's
producer runs, the corrupt file is overwritten, the UI never sees the
exception. Index entries that point at vanished files are swept lazily
on the next read.

Atomic writes
-------------
Payloads are written to ``<sha256>.pkl.tmp`` and ``os.replace``'d into
their final name so a SIGKILL mid-write can't half-corrupt an existing
entry.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import pickle
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from simf.core.constants import load_constants

# Module-level caps so tests can monkeypatch them. Both apply; the first to
# trip starts eviction. Defaults match the spec: 20 entries OR 500 MB.
MAX_ENTRIES = 20
MAX_BYTES = 500 * 1024 * 1024  # 500 MB

DEFAULT_CACHE_DIR = Path.home() / ".simf" / "log_cache"
INDEX_FILENAME = "_index.json"

# Process-wide lock so concurrent reads/writes from the same Streamlit
# process don't race on the index. Multi-process concurrency is not a
# goal here — there's exactly one Streamlit instance per user.
_LOCK = threading.Lock()


# ─── dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class CacheKey:
    """Identifies a (log, target, run) plus the engine/pipeline versions
    that produced the cached analysis. Equal keys mean a cache hit."""

    abspath: str
    mtime_ns: int
    size: int
    constants_version: int
    analysis_version: int
    target: str
    run_index: int

    def hashed(self) -> str:
        """Stable SHA-256 hex digest used as the on-disk filename."""
        raw = (
            f"{self.abspath}:{self.mtime_ns}:{self.size}:"
            f"{self.constants_version}:{self.analysis_version}:"
            f"{self.target}:{self.run_index}"
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def to_index_dict(self) -> dict:
        return {
            "abspath": self.abspath,
            "mtime_ns": self.mtime_ns,
            "size": self.size,
            "constants_version": self.constants_version,
            "analysis_version": self.analysis_version,
            "target": self.target,
            "run_index": self.run_index,
            "key_hash": self.hashed(),
        }


# ─── helpers ──────────────────────────────────────────────────────────────────


def _current_versions() -> tuple[int, int]:
    """Read both versions off ``data/constants.yaml`` via the project loader."""
    c = load_constants()
    return (
        int(c.get("constants_version", 0)),
        int(c.get("analysis_version", 0)),
    )


def _file_stat(log_path: Path) -> tuple[int, int]:
    """`(mtime_ns, size)` for a log path. Raises FileNotFoundError if missing."""
    st = log_path.stat()
    return st.st_mtime_ns, st.st_size


def make_key(
    log_path: Path,
    target: str,
    run_index: int,
    *,
    constants_version: int | None = None,
    analysis_version: int | None = None,
) -> CacheKey:
    """Build a CacheKey for a log file. Reads versions from constants.yaml
    unless explicit overrides are passed (tests / introspection)."""
    mtime_ns, size = _file_stat(log_path)
    if constants_version is None or analysis_version is None:
        cv, av = _current_versions()
        if constants_version is None:
            constants_version = cv
        if analysis_version is None:
            analysis_version = av
    return CacheKey(
        abspath=str(log_path.resolve()),
        mtime_ns=mtime_ns,
        size=size,
        constants_version=constants_version,
        analysis_version=analysis_version,
        target=target,
        run_index=run_index,
    )


def make_wcl_key(
    report_code: str,
    fight_id: int,
    target: str,
    *,
    constants_version: int | None = None,
    analysis_version: int | None = None,
) -> CacheKey:
    """Build a CacheKey for a Warcraft Logs fight.

    WCL reports are immutable — once published a report's contents don't
    change — so the (report_code, fight_id, target) tuple uniquely
    identifies a cacheable analysis bundle. We pack that into the
    file-shaped ``CacheKey`` so the same on-disk surface (index, payloads,
    LRU eviction, version sweeps) covers both local and remote sources
    without a parallel cache implementation:

      - ``abspath`` becomes the pseudo-URI ``wcl://<report_code>``.
      - ``mtime_ns`` is fixed at 0 (immutability — no mtime to track).
      - ``size`` doubles as the fight_id discriminator so two fights in
        the same report don't collide.
      - ``run_index`` is forced to 0 (one WCL fight = one run).

    Same ``constants_version`` / ``analysis_version`` invalidation as the
    file-backed key — a yaml bump nukes WCL entries too.
    """
    if constants_version is None or analysis_version is None:
        cv, av = _current_versions()
        if constants_version is None:
            constants_version = cv
        if analysis_version is None:
            analysis_version = av
    return CacheKey(
        abspath=f"wcl://{report_code}",
        mtime_ns=0,
        size=int(fight_id),
        constants_version=constants_version,
        analysis_version=analysis_version,
        target=target,
        run_index=0,
    )


# ─── index I/O ────────────────────────────────────────────────────────────────


def _ensure_dir(cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)


def _index_path(cache_dir: Path) -> Path:
    return cache_dir / INDEX_FILENAME


def _read_index(cache_dir: Path) -> list[dict]:
    """Load the LRU index. Returns ``[]`` when missing / unreadable —
    a corrupt index is recoverable; we rebuild it implicitly on the next
    write."""
    p = _index_path(cache_dir)
    if not p.exists():
        return []
    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except (OSError, json.JSONDecodeError):
        return []


def _write_index(cache_dir: Path, entries: list[dict]) -> None:
    p = _index_path(cache_dir)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(entries, f)
    os.replace(tmp, p)


def _payload_path(cache_dir: Path, key_hash: str) -> Path:
    return cache_dir / f"{key_hash}.pkl"


# ─── public API ───────────────────────────────────────────────────────────────


def get_cached(
    log_path: Path,
    target: str,
    run_index: int,
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> Any | None:
    """Return the cached analysis bundle for (log, target, run), or None.

    Thin wrapper around ``get_cached_with_key`` — resolves the file's
    stat into a ``CacheKey`` and delegates. Returns ``None`` if the file
    has vanished since the last write."""
    try:
        key = make_key(log_path, target, run_index)
    except FileNotFoundError:
        return None
    return get_cached_with_key(key, cache_dir=cache_dir)


def get_cached_with_key(
    key: CacheKey,
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> Any | None:
    """Source-agnostic cache lookup by a fully-built ``CacheKey``.

    Same hit / miss / sweep semantics as ``get_cached``; the only difference
    is that callers supply the key directly (file-stat path-resolved, or
    a synthetic key for non-file sources like Warcraft Logs).
    """
    with _LOCK:
        if not cache_dir.exists():
            return None

        index = _read_index(cache_dir)
        current_cv, current_av = _current_versions()

        # Sweep global-version invalidation first. An entry whose stored
        # `constants_version` or `analysis_version` doesn't match what
        # constants.yaml says now is dead — unlink the payload, drop from
        # index. This makes a yaml bump invalidate EVERY entry, not just
        # the one we're looking up.
        survivors: list[dict] = []
        changed = False
        for e in index:
            if (
                int(e.get("constants_version", -1)) != current_cv
                or int(e.get("analysis_version", -1)) != current_av
            ):
                _unlink_payload(cache_dir, e.get("key_hash", ""))
                changed = True
                continue
            survivors.append(e)
        if changed:
            index = survivors

        key_hash = key.hashed()
        match_idx = next((i for i, e in enumerate(index) if e.get("key_hash") == key_hash), None)
        if match_idx is None:
            if changed:
                _write_index(cache_dir, index)
            return None

        payload_file = _payload_path(cache_dir, key_hash)
        if not payload_file.exists():
            # Index says we have it; disk says we don't. Drop the entry.
            del index[match_idx]
            _write_index(cache_dir, index)
            return None

        try:
            with payload_file.open("rb") as f:
                payload = pickle.load(f)  # noqa: S301 — per-user local cache (~/.simf/log_cache), same trust boundary as the process
        except (OSError, pickle.UnpicklingError, EOFError, AttributeError, ValueError):
            # Corrupt payload — unlink + drop, force the caller to recompute.
            _unlink_payload(cache_dir, key_hash)
            del index[match_idx]
            _write_index(cache_dir, index)
            return None

        # LRU bump: move this entry to the head.
        entry = index.pop(match_idx)
        index.insert(0, entry)
        _write_index(cache_dir, index)
        return payload


def put_cached(
    log_path: Path,
    target: str,
    run_index: int,
    payload: Any,
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> None:
    """Persist ``payload`` to disk, replacing any prior entry for the same
    key, and run LRU eviction. Thin wrapper around
    ``put_cached_with_key`` — resolves the file's stat into a ``CacheKey``
    and delegates. No-op (silent) when the file has vanished or stat
    fails — the cache is a perf optimization; a write failure shouldn't
    break the UI."""
    try:
        key = make_key(log_path, target, run_index)
    except (FileNotFoundError, OSError):
        return
    put_cached_with_key(key, payload, cache_dir=cache_dir)


def put_cached_with_key(
    key: CacheKey,
    payload: Any,
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> None:
    """Source-agnostic cache write by a fully-built ``CacheKey``. Same
    atomic-replace + LRU semantics as ``put_cached``."""
    with _LOCK:
        try:
            _ensure_dir(cache_dir)
        except OSError:
            return

        key_hash = key.hashed()
        payload_file = _payload_path(cache_dir, key_hash)
        tmp = payload_file.with_suffix(payload_file.suffix + ".tmp")
        try:
            with tmp.open("wb") as f:
                pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp, payload_file)
        except (OSError, pickle.PicklingError):
            # Best-effort cleanup of the temp file.
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            return

        size_bytes = payload_file.stat().st_size

        # Update index: drop any prior entry for the same key_hash, push
        # this one at the head, then evict.
        index = _read_index(cache_dir)
        index = [e for e in index if e.get("key_hash") != key_hash]
        new_entry = key.to_index_dict()
        new_entry["payload_bytes"] = int(size_bytes)
        index.insert(0, new_entry)

        index = _evict(cache_dir, index)
        _write_index(cache_dir, index)


def get_or_compute(
    log_path: Path,
    target: str,
    run_index: int,
    producer: Callable[[], Any],
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> Any:
    """Cache-aware wrapper: return cached payload if fresh, else call
    ``producer()`` to compute, persist, and return.

    ``producer`` takes no arguments — close over your inputs at the call
    site. Exceptions from ``producer`` propagate; we never swallow
    pipeline errors."""
    cached = get_cached(log_path, target, run_index, cache_dir=cache_dir)
    if cached is not None:
        return cached
    payload = producer()
    put_cached(log_path, target, run_index, payload, cache_dir=cache_dir)
    return payload


def get_or_compute_with_key(
    key: CacheKey,
    producer: Callable[[], Any],
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> Any:
    """Source-agnostic ``get_or_compute`` — used by the WCL bridge."""
    cached = get_cached_with_key(key, cache_dir=cache_dir)
    if cached is not None:
        return cached
    payload = producer()
    put_cached_with_key(key, payload, cache_dir=cache_dir)
    return payload


# ─── eviction ─────────────────────────────────────────────────────────────────


def _evict(cache_dir: Path, index: list[dict]) -> list[dict]:
    """Trim ``index`` (tail = LRU) so neither the count nor the byte total
    exceeds the configured caps. Unlinks evicted payloads."""
    # Entry-count cap.
    while len(index) > MAX_ENTRIES:
        victim = index.pop()
        _unlink_payload(cache_dir, victim.get("key_hash", ""))

    # Byte-total cap.
    def total_bytes(entries: list[dict]) -> int:
        return sum(int(e.get("payload_bytes", 0)) for e in entries)

    while index and total_bytes(index) > MAX_BYTES:
        victim = index.pop()
        _unlink_payload(cache_dir, victim.get("key_hash", ""))

    return index


def _unlink_payload(cache_dir: Path, key_hash: str) -> None:
    """Remove a payload file if present. Swallows OS errors."""
    if not key_hash:
        return
    try:
        p = _payload_path(cache_dir, key_hash)
        if p.exists():
            p.unlink()
    except OSError:
        pass


# ─── introspection / test helpers ─────────────────────────────────────────────


def clear_cache(*, cache_dir: Path = DEFAULT_CACHE_DIR) -> None:
    """Remove every payload + the index. Used by tests; the UI doesn't
    call this — version-bump invalidation handles the housekeeping in
    production."""
    with _LOCK:
        if not cache_dir.exists():
            return
        for p in cache_dir.glob("*.pkl"):
            with contextlib.suppress(OSError):
                p.unlink()
        for p in cache_dir.glob("*.pkl.tmp"):
            with contextlib.suppress(OSError):
                p.unlink()
        idx = _index_path(cache_dir)
        if idx.exists():
            with contextlib.suppress(OSError):
                idx.unlink()


def index_snapshot(*, cache_dir: Path = DEFAULT_CACHE_DIR) -> list[dict]:
    """Read-only view of the LRU index (head = most recently used).
    For tests + admin scripts."""
    with _LOCK:
        if not cache_dir.exists():
            return []
        return _read_index(cache_dir)
