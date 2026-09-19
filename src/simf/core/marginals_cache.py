"""Disk cache for sim-derived survivability marginals.

`compute_survivability_marginals` costs ~10-20s on the Pi and is paid on
*every* cold demo load — the landing funnel — because the session-state cache
(`_surv_marginals_cache`) dies on browser refresh. This module persists the
result to `~/.simf/marginals_cache/` so the second-and-later cold load on a
given machine is instant.

Keyed by the SAME signature the UI's session cache uses
(`_char_marginals_signature` — spec / race / talents / stat ratings /
`constants_version`), so a `constants.yaml` bump invalidates every entry for
free. The stored signature is re-checked on read to defend against the
(astronomically unlikely) 16-hex-digit hash collision.

Best-effort: any disk error degrades silently to "no cache" — the caller
recomputes rather than crashing. Pure stdlib (no Streamlit), so it unit-tests
without booting an app.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from collections.abc import Sequence
from pathlib import Path

_DEFAULT_DIR = Path.home() / ".simf" / "marginals_cache"
_CACHE_CAP = 256  # entries; mtime-oldest eviction beyond this


def cache_dir(override: Path | None = None) -> Path:
    """Resolve the cache directory. Explicit arg > `SIMF_MARGINALS_CACHE_DIR`
    env var (used by tests) > the default `~/.simf/marginals_cache`."""
    if override is not None:
        return override
    env = os.environ.get("SIMF_MARGINALS_CACHE_DIR")
    return Path(env) if env else _DEFAULT_DIR


def _signature_key(signature: Sequence) -> str:
    """Stable 16-hex-digit key from a marginals signature (strings + ints)."""
    raw = json.dumps(list(signature), default=str)
    return hashlib.sha1(raw.encode(), usedforsecurity=False).hexdigest()[:16]


def _coerce_marginals(marginals: dict) -> dict:
    """Force numpy floats to plain floats so json.dump succeeds and the
    reloaded value is identical in shape to a fresh `ehp_marginals()` dict."""
    out: dict = {}
    for stat, v in marginals.items():
        if isinstance(v, dict):
            out[str(stat)] = {str(k): float(x) for k, x in v.items()}
        else:
            out[str(stat)] = float(v)
    return out


def _coerce_ci(ci: dict | None) -> dict | None:
    """Same numpy-float defense as `_coerce_marginals`, for the CI's
    ``{stat: {school: (lo, hi) | None}}`` shape. Tuples become JSON arrays;
    `load_ci` converts them back."""
    if ci is None:
        return None
    out: dict = {}
    for stat, by_school in ci.items():
        out[str(stat)] = {
            str(school): [float(bound[0]), float(bound[1])] if bound is not None else None
            for school, bound in by_school.items()
        }
    return out


def _decode_ci(raw: dict | None) -> dict | None:
    if raw is None:
        return None
    out: dict = {}
    for stat, by_school in raw.items():
        out[stat] = {
            school: (bound[0], bound[1]) if bound is not None else None
            for school, bound in by_school.items()
        }
    return out


def _json_normalized_signature(signature: Sequence) -> object:
    """Round-trip ``signature`` through JSON so it compares equal to a
    stored signature read back from disk.

    A plain ``list(signature)`` only converts the OUTER tuple to a list —
    any NESTED tuple element (e.g. ``_char_marginals_signature``'s
    ``active_buff_spell_ids`` field, a sorted tuple) stays a tuple, while
    the same element read back from JSON is always a list (JSON has no
    tuple type). ``() != []`` in Python, so that single nested element
    made every comparison fail — a real regression (PR #316) that turned
    the disk cache into a permanent miss for every character, silently,
    because a miss degrades to "recompute" rather than raising. Only
    caught because a later test (PR #317) actually exercised the round
    trip; the bug shipped invisibly for every character in between.
    Round-tripping both sides through JSON, instead of patching the
    top-level ``list(...)`` call, means any future nested element is
    automatically comparison-safe too.
    """
    return json.loads(json.dumps(list(signature), default=str))


def load(signature: Sequence, *, override_dir: Path | None = None) -> dict | None:
    """Return the cached marginals for ``signature``, or None on any miss
    (no file, unreadable, corrupt JSON, or stored-signature mismatch)."""
    path = cache_dir(override_dir) / f"{_signature_key(signature)}.json"
    try:
        with path.open() as f:
            blob = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        return None
    # Collision guard — the stored signature must match this one exactly.
    if blob.get("signature") != _json_normalized_signature(signature):
        return None
    marginals = blob.get("marginals")
    return marginals if isinstance(marginals, dict) else None


def load_ci(signature: Sequence, *, override_dir: Path | None = None) -> dict | None:
    """Return the cached bootstrap CI for ``signature``, or ``None`` on any
    miss — including a cache entry written before the CI existed (no ``ci``
    key at all) or one stored with ``ci_resamples=0``. Same file as
    ``load()``; a separate read (not returned alongside) keeps `load()`'s
    long-standing "returns the marginals dict" contract byte-for-byte for
    every existing caller/test."""
    path = cache_dir(override_dir) / f"{_signature_key(signature)}.json"
    try:
        with path.open() as f:
            blob = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        return None
    if blob.get("signature") != _json_normalized_signature(signature):
        return None
    return _decode_ci(blob.get("ci"))


def store(
    signature: Sequence,
    marginals: dict,
    *,
    ci: dict | None = None,
    override_dir: Path | None = None,
    cap: int = _CACHE_CAP,
) -> None:
    """Persist ``marginals`` (and optionally ``ci``) under ``signature``.
    Best-effort — a disk error (read-only fs, permissions) is swallowed so
    the app never breaks on it. Writes atomically (temp + replace) and
    prunes to ``cap`` entries."""
    d = cache_dir(override_dir)
    try:
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{_signature_key(signature)}.json"
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w") as f:
            json.dump(
                {
                    "signature": list(signature),
                    "marginals": _coerce_marginals(marginals),
                    "ci": _coerce_ci(ci),
                },
                f,
            )
        tmp.replace(path)  # atomic on POSIX
        _prune(d, cap)
    except (OSError, TypeError, ValueError):
        pass


def _prune(d: Path, cap: int) -> None:
    """Evict the oldest entries (by mtime) beyond ``cap``."""
    files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime)
    if len(files) <= cap:
        return
    for p in files[: len(files) - cap]:
        with contextlib.suppress(OSError):
            p.unlink()
