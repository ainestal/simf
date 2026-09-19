"""World-ceiling completion data — per-spec, current-season.

Surfaces below the key-level verdict headline as a real-world context
caption: simf's verdict measures *damage-stream survivability*, not
key completion. At +17+ on most tank specs, fewer than a thousand
players have timed the run all season — survivability is one gate of
several (mechanics, DPS, timer). The caption keeps the simulator
honest about that gap.

YAML lives at `src/simf/data/world_ceilings.yaml`. This module is the
typed loader. Caller layer is `src/simf/ui/app.py`
(`_render_key_level_verdict_panel`).

User-feedback origin (2026-05-27): *"the highest keys in the world
with a prot warrior are currently +20, the highest in a spanish server
in EU is +19. So our calibration of 'can I survive this' is a little
off, according to the number of people that completed keys over 16,
it's not so easy to do."*

The math is fine. The framing was dishonest. This module is the
framing fix.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

_DATA_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class WorldCeiling:
    """One spec's season-ceiling row.

    `world_max_key` — highest key timed by anyone on this spec this
    season. `broad_completion_key` — level above which completion is
    rare (~100+ players have timed it). `caveat_below` — when the
    user's verdict ceiling is below this, suppress the caption (it
    isn't earned yet). `population_descriptor` — plain-English size of
    the tail (e.g. "a few hundred", "a few dozen") for the caption
    copy when no live count is available. `broad_count` — live
    distinct-player count at `broad_completion_key` from WCL (None when
    the live layer is unavailable; the caption falls back to
    `population_descriptor`). This is a SINGLE-ENCOUNTER count — a
    floor for the cross-encounter union. `broad_count_capped` — True
    when the live count hit the WCL row cap and the number is a floor
    (caption reads "at least N+" rather than "N"). `is_cross_encounter`
    — True when `broad_count` was sourced from the cross-encounter
    pre-warm cache (CLI `simf rankings-refresh`); caption appends
    "(cross-encounter)" so the user knows the higher-precision union is
    showing. `placeholder` — true when Brutoh hasn't verified the row
    against a real character pool yet (documentation hygiene, not a
    runtime gate).
    """

    spec: str
    world_max_key: int
    broad_completion_key: int
    caveat_below: int
    population_descriptor: str
    placeholder: bool
    broad_count: int | None = None
    broad_count_capped: bool = False
    is_cross_encounter: bool = False


@lru_cache(maxsize=1)
def _load_raw() -> dict:
    path = _DATA_DIR / "world_ceilings.yaml"
    with path.open() as f:
        return yaml.safe_load(f) or {}


def _load_yaml_rows() -> dict[str, WorldCeiling]:
    """Internal: build WorldCeiling rows from just the YAML (no live
    layer). Always available, never network."""
    raw = _load_raw()
    specs = raw.get("specs") or {}
    out: dict[str, WorldCeiling] = {}
    for spec_name, row in specs.items():
        if not isinstance(row, dict):
            continue
        out[spec_name] = WorldCeiling(
            spec=spec_name,
            world_max_key=int(row.get("world_max_key", 0)),
            broad_completion_key=int(row.get("broad_completion_key", 0)),
            caveat_below=int(row.get("caveat_below", 0)),
            population_descriptor=str(row.get("population_descriptor", "")),
            placeholder=bool(row.get("placeholder", False)),
        )
    return out


@lru_cache(maxsize=1)
def load_world_ceilings() -> dict[str, WorldCeiling]:
    """All spec rows, keyed by `class_spec` string.

    Prefers live `world_max_key` and `broad_count` values from WCL's
    character rankings endpoint when credentials are configured; falls
    back to the YAML when WCL isn't reachable. Other fields
    (`broad_completion_key`, `caveat_below`, `placeholder`) always come
    from the YAML; `population_descriptor` is used only when the live
    count is unavailable.

    Result is cached for the process lifetime — safe to share across
    requests/sessions. Network and config failures are caught;
    returns the YAML rows on any error (the caption is a polish layer
    and should never break the verdict surface).
    """
    yaml_rows = _load_yaml_rows()
    try:
        # Lazy import — avoids dragging `requests` into the data layer
        # at module import time, and lets tests stub the function via
        # `simf.io.wcl_rankings.fetch_live_world_max_keys`.
        from simf.io.wcl_rankings import fetch_live_world_max_keys

        live_max = fetch_live_world_max_keys()
    except Exception:
        live_max = None

    try:
        from simf.io.wcl_rankings import fetch_live_broad_completion_counts

        spec_thresholds = {slug: row.broad_completion_key for slug, row in yaml_rows.items()}
        live_counts = fetch_live_broad_completion_counts(spec_thresholds)
    except Exception:
        live_counts = None

    # Cross-encounter union (CLI pre-warm only — never blocks the UI).
    # When `simf rankings-refresh` has populated the cache, prefer the
    # cross-encounter union over the single-encounter `broad_count`. The
    # accessor is cache-only and cheap; missing data simply returns {}.
    try:
        from simf.io.wcl_rankings import read_cached_cross_encounter_counts

        spec_thresholds = {slug: row.broad_completion_key for slug, row in yaml_rows.items()}
        cross_counts = read_cached_cross_encounter_counts(spec_thresholds)
    except Exception:
        cross_counts = {}

    # Cross-encounter world-max-key (CLI pre-warm, symmetric with
    # cross_counts). Written by `simf rankings-refresh` alongside
    # `broad_count_cross`; cache-only access from the UI render path.
    try:
        from simf.io.wcl_rankings import read_cached_cross_encounter_world_max

        cross_world_max = read_cached_cross_encounter_world_max()
    except Exception:
        cross_world_max = {}

    if not live_max and not live_counts and not cross_counts and not cross_world_max:
        return yaml_rows

    live_max = live_max or {}
    live_counts = live_counts or {}
    cross_counts = cross_counts or {}
    cross_world_max = cross_world_max or {}

    out: dict[str, WorldCeiling] = {}
    for spec_name, row in yaml_rows.items():
        new_world_max = row.world_max_key
        new_placeholder = row.placeholder
        live_world_max = live_max.get(spec_name)
        if live_world_max and live_world_max > 0:
            new_world_max = int(live_world_max)
            # Live data trumps the placeholder flag — if WCL gave us
            # a real number, this row is no longer hand-wavy.
            new_placeholder = False

        # Cross-encounter world-max layer. The cross value is computed
        # while paginating at the low broad-completion bracket — when
        # the row cap is hit, the highest `bracketData` can be hidden
        # past the cap (WCL paginates by score, not by bracket). Compose
        # with `max(cross, single)` so the cross value can ONLY ratchet
        # the world-max upward; it never regresses below the single-
        # encounter high-probe value from PR #106.
        cross_world_max_int = 0
        cross_world_max_raw = cross_world_max.get(spec_name)
        if cross_world_max_raw is not None:
            try:
                cross_world_max_int = int(cross_world_max_raw)
            except (TypeError, ValueError):
                cross_world_max_int = 0
            if cross_world_max_int > 0:
                # The compose: take the max of the single-encounter live
                # value (already in new_world_max if set) and the
                # cross-encounter value.
                new_world_max = max(int(new_world_max), cross_world_max_int)
                new_placeholder = False

        broad_count: int | None = None
        broad_count_capped = False
        is_cross_encounter = False

        # Prefer the cross-encounter union when present (CLI pre-warmed).
        cross_row = cross_counts.get(spec_name)
        cross_count_used = False
        if isinstance(cross_row, dict):
            raw_cross = cross_row.get("broad_count_cross")
            try:
                cross_int = int(raw_cross) if raw_cross is not None else 0
            except (TypeError, ValueError):
                cross_int = 0
            if cross_int > 0:
                broad_count = cross_int
                broad_count_capped = bool(cross_row.get("broad_count_cross_capped", False))
                cross_count_used = True
                new_placeholder = False

        # Fall back to the single-encounter live count when cross is absent.
        if broad_count is None:
            live_row = live_counts.get(spec_name)
            if isinstance(live_row, dict):
                raw_count = live_row.get("broad_count")
                # Tolerate corrupt cache JSON: a missing key, an explicit
                # null, a string, or any non-int value falls back to no
                # live count rather than crashing the verdict surface.
                try:
                    count_int = int(raw_count) if raw_count is not None else 0
                except (TypeError, ValueError):
                    count_int = 0
                if count_int > 0:
                    broad_count = count_int
                    broad_count_capped = bool(live_row.get("broad_count_capped", False))
                    # Live data also clears placeholder.
                    new_placeholder = False

        # Compose the `is_cross_encounter` flag: BOTH the broad count AND
        # the world-max layer must be sourced from the cross-encounter
        # pre-warm cache for the caption suffix to fire. This matches
        # the user-facing claim ("(cross-encounter)" means everything in
        # the caption is cross-sourced) — a partial state (one layer
        # cross, one layer single) is treated as single-encounter to
        # avoid over-promising.
        is_cross_encounter = bool(cross_count_used and cross_world_max_int > 0)

        out[spec_name] = WorldCeiling(
            spec=row.spec,
            world_max_key=int(new_world_max),
            broad_completion_key=row.broad_completion_key,
            caveat_below=row.caveat_below,
            population_descriptor=row.population_descriptor,
            placeholder=new_placeholder,
            broad_count=broad_count,
            broad_count_capped=broad_count_capped,
            is_cross_encounter=is_cross_encounter,
        )
    return out


def world_ceiling_for(class_spec: str) -> WorldCeiling | None:
    """Row for `class_spec`, or `None` if the spec isn't in the table.

    Callers should treat `None` as "no caption to render" — never as
    an error. Adding a new tank spec to the engine without a matching
    YAML row should silently skip the caption, not crash the verdict.
    """
    return load_world_ceilings().get(class_spec)


def reset_cache() -> None:
    """Clear the in-process `lru_cache`. Tests call this after stubbing
    the live-fetch function so the next `load_world_ceilings()` picks
    up the new behaviour."""
    load_world_ceilings.cache_clear()
    _load_raw.cache_clear()
