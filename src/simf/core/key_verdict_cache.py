"""Disk cache for the key-level verdict sweep (Phase 2.1's "Am I tankable
enough for +X?" answer).

`compute_key_level_verdict` sweeps ~10 sim runs (+2 through +24) and costs
~20-30s on the Pi — paid on *every* cold browser session that opens the
verdict panel and clicks Compute, because the only existing cache
(`ui/verdict.py`'s `_key_verdict_cache` session-state dict) dies on refresh.
This module persists the result to `~/.simf/key_verdict_cache/` so the
second-and-later cold load of the SAME signature — in practice, the bundled
demo character or a cold `?demo=<slug>` share-link, the viral landing
funnel — is instant. Same shape as `marginals_cache.py` (a different
expensive per-character compute with the identical "session dies on
refresh" problem); this module mirrors its structure deliberately.

Keyed by an opaque ``signature`` tuple the caller builds — this module has
no opinion on what goes into it (no `Character` / Streamlit import), same
as `marginals_cache.py`. `ui/verdict.py`'s `_key_verdict_signature` is the
real caller: `_char_marginals_signature` (Character sim-affecting fields +
`constants_version`) plus the sweep's own compute parameters (iterations /
affix / profile names), so a change to any of those is a cache miss rather
than a silently-mismatched hit.

Privacy scope is enforced entirely by the CALLER, not here: `ui/verdict.py`
only ever calls `store()` when the loaded character is the bundled demo
build (`_ss()["_loaded_demo_slug"]` set) — a real visitor's looked-up gear
is never written to this disk cache. `load()` stays unconditional since a
hit can only occur for a signature a demo load previously wrote.

Best-effort: any disk error degrades silently to "no cache" — the caller
recomputes rather than crashing. Pure stdlib (no Streamlit) aside from the
`KeyLevelVerdict`/`KeyLevelPoint` dataclass shapes it encodes/decodes, so it
unit-tests without booting an app or running a sim.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from collections.abc import Sequence
from pathlib import Path

from simf.core.key_level_verdict import KeyLevelPoint, KeyLevelVerdict

_DEFAULT_DIR = Path.home() / ".simf" / "key_verdict_cache"
_CACHE_CAP = 256  # entries; mtime-oldest eviction beyond this


def cache_dir(override: Path | None = None) -> Path:
    """Resolve the cache directory. Explicit arg > `SIMF_KEY_VERDICT_CACHE_DIR`
    env var (used by tests) > the default `~/.simf/key_verdict_cache`."""
    if override is not None:
        return override
    env = os.environ.get("SIMF_KEY_VERDICT_CACHE_DIR")
    return Path(env) if env else _DEFAULT_DIR


def _signature_key(signature: Sequence) -> str:
    """Stable 16-hex-digit key from a verdict signature (strings + ints)."""
    raw = json.dumps(list(signature), default=str)
    return hashlib.sha1(raw.encode(), usedforsecurity=False).hexdigest()[:16]


def _json_normalized_signature(signature: Sequence) -> object:
    """Round-trip ``signature`` through JSON so it compares equal to a
    stored signature read back from disk.

    Same fix as `marginals_cache._json_normalized_signature` (PR #316/#317):
    a bare ``list(signature)`` only converts the OUTER tuple — any NESTED
    tuple element (e.g. a sorted `active_buff_spell_ids` tuple, carried in
    via `_char_marginals_signature`) stays a tuple on this side while the
    same element read back from JSON is always a list. Round-tripping both
    sides through JSON keeps the comparison correct regardless of nesting.
    """
    return json.loads(json.dumps(list(signature), default=str))


def _encode_point(p: KeyLevelPoint) -> dict:
    """`KeyLevelPoint` → JSON-safe dict. Every numeric field is coerced to a
    plain `float`/`int` — `compute_key_level_verdict` builds these from
    sim results that pass through numpy (`core/runner.py`/`metrics.py`),
    same numpy-float defense as `marginals_cache._coerce_marginals`."""
    return {
        "key_level": int(p.key_level),
        "damage_multiplier": float(p.damage_multiplier),
        "death_rate": float(p.death_rate),
        "mean_dtps": float(p.mean_dtps),
        "p99_5s_window": float(p.p99_5s_window),
        "band": str(p.band),
        "mean_hrps": float(p.mean_hrps),
        "normalized_tank_score": float(p.normalized_tank_score),
        "p99_10s_window": float(p.p99_10s_window),
        "p99_15s_window": float(p.p99_15s_window),
        "sample_max_hp": float(p.sample_max_hp),
        "p5_min_hp_pct": (float(p.p5_min_hp_pct) if p.p5_min_hp_pct is not None else None),
        "sample_duration_s": float(p.sample_duration_s),
        "sample_damage_timeline": [[float(t), float(v)] for t, v in p.sample_damage_timeline],
        "sample_heal_timeline": [[float(t), float(v)] for t, v in p.sample_heal_timeline],
        "sample_died": bool(p.sample_died),
        "sample_time_to_die_s": (
            float(p.sample_time_to_die_s) if p.sample_time_to_die_s is not None else None
        ),
    }


def _decode_point(raw: dict) -> KeyLevelPoint:
    """Inverse of `_encode_point`. `.get(..., default)` on every field added
    after the dataclass's initial shape so an older cache entry (written
    before a field existed) degrades to that field's own dataclass default
    instead of `KeyError` — same tolerance `marginals_cache.load_ci` gives
    a pre-CI entry."""
    return KeyLevelPoint(
        key_level=int(raw["key_level"]),
        damage_multiplier=float(raw["damage_multiplier"]),
        death_rate=float(raw["death_rate"]),
        mean_dtps=float(raw["mean_dtps"]),
        p99_5s_window=float(raw["p99_5s_window"]),
        band=str(raw["band"]),
        mean_hrps=float(raw.get("mean_hrps", 0.0)),
        normalized_tank_score=float(raw.get("normalized_tank_score", 0.0)),
        p99_10s_window=float(raw.get("p99_10s_window", 0.0)),
        p99_15s_window=float(raw.get("p99_15s_window", 0.0)),
        sample_max_hp=float(raw.get("sample_max_hp", 0.0)),
        p5_min_hp_pct=(
            float(raw["p5_min_hp_pct"]) if raw.get("p5_min_hp_pct") is not None else None
        ),
        sample_duration_s=float(raw.get("sample_duration_s", 0.0)),
        sample_damage_timeline=[
            (float(t), float(v)) for t, v in raw.get("sample_damage_timeline", [])
        ],
        sample_heal_timeline=[(float(t), float(v)) for t, v in raw.get("sample_heal_timeline", [])],
        sample_died=bool(raw.get("sample_died", False)),
        sample_time_to_die_s=(
            float(raw["sample_time_to_die_s"])
            if raw.get("sample_time_to_die_s") is not None
            else None
        ),
    )


def _encode_verdict(v: KeyLevelVerdict) -> dict:
    return {
        "points": [_encode_point(p) for p in v.points],
        "comfortable_max": (int(v.comfortable_max) if v.comfortable_max is not None else None),
        "prog_ceiling": (int(v.prog_ceiling) if v.prog_ceiling is not None else None),
        "affix": str(v.affix),
    }


def _decode_verdict(raw: dict) -> KeyLevelVerdict:
    return KeyLevelVerdict(
        points=[_decode_point(p) for p in raw.get("points", [])],
        comfortable_max=(
            int(raw["comfortable_max"]) if raw.get("comfortable_max") is not None else None
        ),
        prog_ceiling=(int(raw["prog_ceiling"]) if raw.get("prog_ceiling") is not None else None),
        affix=str(raw.get("affix", "fortified")),
    )


def load(signature: Sequence, *, override_dir: Path | None = None) -> KeyLevelVerdict | None:
    """Return the cached `KeyLevelVerdict` for ``signature``, or ``None`` on
    any miss (no file, unreadable, corrupt JSON, malformed payload, or
    stored-signature mismatch)."""
    path = cache_dir(override_dir) / f"{_signature_key(signature)}.json"
    try:
        with path.open() as f:
            blob = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        return None
    # Collision guard — the stored signature must match this one exactly.
    if blob.get("signature") != _json_normalized_signature(signature):
        return None
    verdict_raw = blob.get("verdict")
    if not isinstance(verdict_raw, dict):
        return None
    try:
        return _decode_verdict(verdict_raw)
    except (KeyError, TypeError, ValueError):
        return None


def store(
    signature: Sequence,
    verdict: KeyLevelVerdict,
    *,
    override_dir: Path | None = None,
    cap: int = _CACHE_CAP,
) -> None:
    """Persist ``verdict`` under ``signature``. Best-effort — a disk error
    (read-only fs, permissions) is swallowed so the app never breaks on it.
    Writes atomically (temp + replace) and prunes to ``cap`` entries."""
    d = cache_dir(override_dir)
    try:
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{_signature_key(signature)}.json"
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w") as f:
            json.dump(
                {
                    "signature": list(signature),
                    "verdict": _encode_verdict(verdict),
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
