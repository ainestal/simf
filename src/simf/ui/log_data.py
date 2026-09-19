"""simf UI — L0 data/cache substrate for the Why-died log surface.

Path resolution, the ``list_logs`` picker, and every ``@st.cache_data``
pipeline cache (runs, deaths, hydration, mitigation audit, rage events,
coverage report). Render-free: the only Streamlit touch is the
``@st.cache_data`` decorator itself. Extracted from ``log_view.py``
(PR 1/3 of the log_view split) so downstream panel/flow modules import
their data layer instead of carrying it inline.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from simf.core.bleed_detection import is_bleed as _is_bleed_spell
from simf.io.character_from_combatant_info import HydrateResult, hydrate_character
from simf.io.combat_log import (
    PartyMember,
    count_party_deaths_in_run,
    detect_party_roles,
    iter_damage_events,
    iter_death_events,
    parse_cast_events,
    parse_challenge_modes,
    parse_encounters,
    parse_energize_events,
    parse_interrupted_spell_ids,
    parse_self_buff_windows,
    parse_source_debuff_windows,
    segment_run,
    summarize_run,
)
from simf.io.death_analysis import reconstruct_deaths
from simf.io.log_analysis_cache import get_or_compute
from simf.io.mitigation_audit import audit_replay

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
EXAMPLES_DIR = REPO_ROOT / "examples"
# User-writable upload sink. Kept outside the repo so uploaded logs don't
# pollute `examples/`. `list_logs()` aggregates both directories; if a
# basename collides, uploads win (the user just put it there).
UPLOADS_DIR = Path.home() / ".simf" / "logs"


def _resolve_log_path(log_name: str) -> Path:
    """Resolve a log basename to its on-disk path, preferring uploads."""
    p = UPLOADS_DIR / log_name
    if p.exists():
        return p
    return EXAMPLES_DIR / log_name


# ─── cached pipeline helpers ──────────────────────────────────────────────────


@st.cache_data(show_spinner=False)
def list_logs() -> list[str]:
    """All combat-log filenames, most-recent first.

    WoW combat logs follow `WoWCombatLog-MMDDYY_HHMMSS.txt`, so a
    reverse-alphabetical sort lands the newest at the top — which is
    also where the demo's CHALLENGE_MODE-bearing logs live in practice.

    Earlier versions of this function parsed every file to filter
    "with CHALLENGE_MODE_START first" but that cost 30s+ on Pi for
    ~80MB logs and timed out Streamlit AppTest. The empty-state copy
    further down (`No Mythic+ runs to analyze…`) handles the case
    where the user picks a runless log.
    """
    names: set[str] = set()
    if UPLOADS_DIR.exists():
        names.update(p.name for p in UPLOADS_DIR.glob("WoWCombatLog-*.txt"))
    if EXAMPLES_DIR.exists():
        names.update(p.name for p in EXAMPLES_DIR.glob("WoWCombatLog-*.txt"))
    return sorted(names, reverse=True)


@st.cache_data(show_spinner=False)
def _cached_runs(log_name: str):
    """Just the run headers — cheap (single scan of CHALLENGE_MODE_* lines)."""
    return parse_challenge_modes(_resolve_log_path(log_name))


@st.cache_data(show_spinner=False)
def _cached_run_death_count(log_name: str, run_index: int) -> int:
    """Number of Player-* UNIT_DIED events inside a specific run window.

    Separate cache from `_cached_runs` so the cheap CHALLENGE_MODE scan
    stays cheap for callers that don't care about deaths (run-list
    rendering, party-role detection cold path). Routes through
    `count_party_deaths_in_run` which inspects only UNIT_DIED lines and
    honours the run's byte offset + time bounds — same perf shape as
    `iter_death_events`, scoped to one run.
    """
    runs = _cached_runs(log_name)
    if not runs or not (0 <= run_index < len(runs)):
        return 0
    run = runs[run_index]
    return count_party_deaths_in_run(
        _resolve_log_path(log_name),
        start_time_s=run.start_time_s,
        end_time_s=run.end_time_s,
        start_byte_offset=run.start_byte_offset,
    )


# Cap enrichment so the picker dropdown never triggers an arbitrarily large
# number of CHALLENGE_MODE scans on render. See `list_logs()` above: an older
# version that filter-parsed every file cost 30s+ on Pi for ~80MB logs and
# timed out Streamlit AppTest. _cached_runs is cheaper (a single CM scan, then
# session-cached) but iterating it across every log in a busy `examples/`
# directory still adds up cold. Top-10 covers the most-recent runs — past that,
# the user is digging through history and a plain filename is fine.
_LOG_PICKER_LABEL_CAP = 10


def _format_death_badge(death_count: int) -> str:
    """Render the death-count badge for the log-picker label.

    The badge follows the outcome glyph (✓ / ✗) and gives the reader a
    one-glance "did anyone wipe in this run" signal:

      0 → "clean"            (no Player-* deaths in the run window)
      1 → "1 death"
      N → "{N} deaths"       (N > 1)

    Empty string is never returned — the badge is always informative
    when invoked. The caller decides whether to invoke (e.g. only for
    the selected row, to keep dropdown render cheap).
    """
    if death_count <= 0:
        return "clean"
    if death_count == 1:
        return "1 death"
    return f"{death_count} deaths"


def _is_bundled_example_log(log_name: str) -> bool:
    """True for a log served from the repo's bundled `examples/` dir rather
    than a real upload — mirrors `_resolve_log_path`'s own upload-wins
    precedence, so this always agrees with which file actually gets read."""
    return not (UPLOADS_DIR / log_name).exists()


def _log_picker_label(log_name: str) -> str:
    """Inline dungeon identity for the log-picker dropdown.

    Returns the bare filename (plus a "(sample)" tag — see below) for logs
    past `_LOG_PICKER_LABEL_CAP` in `list_logs()` order (newest first), or
    for logs with no parseable CHALLENGE_MODE runs. Otherwise appends the
    first run's dungeon + key, a ✓/✗ glyph for the outcome, a death-count
    badge sourced from a cheap UNIT_DIED scan over the run's byte window,
    and a "+N more" suffix when the log carries multiple runs.

    The death badge is scoped to the *first run only* so the label stays
    consistent with the rest of the existing summary (dungeon + key from
    the first run, "+N more" tagging the others). Aggregating across all
    runs would contradict the displayed run identity.

    A cold visitor with no logs of their own lands on this picker defaulted
    to the first bundled example — with no "this isn't your data" signal,
    that read as a bug or a privacy leak ("whose AnonTank1 is this?", round-1
    novice-tank review, 2026-07-05). The character picker just below cascades
    from the same log, so tagging the log here covers both fields.
    """
    sample_tag = " (sample)" if _is_bundled_example_log(log_name) else ""

    logs = list_logs()
    try:
        rank = logs.index(log_name)
    except ValueError:
        rank = _LOG_PICKER_LABEL_CAP
    if rank >= _LOG_PICKER_LABEL_CAP:
        return f"{log_name}{sample_tag}"

    runs = _cached_runs(log_name)
    if not runs:
        return f"{log_name}{sample_tag}"

    first = runs[0]
    glyph = "✓" if first.success else "✗"
    deaths = _cached_run_death_count(log_name, 0)
    badge = _format_death_badge(deaths)
    head = f"{log_name}{sample_tag} — {first.map_name} +{first.key_level} {glyph} · {badge}"
    extra = len(runs) - 1
    if extra > 0:
        return f"{head} +{extra} more"
    return head


@st.cache_data(show_spinner=False)
def _cached_party_roles(log_name: str, run_index: int) -> list[PartyMember]:
    """Party members in the picked run, with auto-detected roles.

    Single-pass scan of damage + heal events inside the run window. Returns
    tank → healer → DPS-by-damage-taken. Cached per (log, run_index) so
    back-to-back keys with different parties don't share a stale detection.

    Routes through `_cached_runs` (not a direct `parse_challenge_modes`
    call) so cold-cache cost stays at one CHALLENGE_MODE scan across all
    cached helpers — see test_log_view_cache_consolidation.
    """
    runs = _cached_runs(log_name)
    if not runs or not (0 <= run_index < len(runs)):
        return detect_party_roles(_resolve_log_path(log_name))
    run = runs[run_index]
    return detect_party_roles(
        _resolve_log_path(log_name),
        start_time_s=run.start_time_s,
        end_time_s=run.end_time_s,
        start_byte_offset=run.start_byte_offset,
    )


try:
    from simf.io import item_db as _ITEM_DB
except ImportError:  # requests not installed in this env
    _ITEM_DB = None


def _resolve_equipped_stats(items: dict) -> dict[str, int]:
    """Resolver passed to `hydrate_character`. Mirrors the SimC-paste path's
    `app._resolve_equipped_stats` (item_db lookup by item_id+bonus_ids). The
    hydrate path hands this a single `{"off_hand": ItemSpec}` to source
    `shield_armor` (not in COMBATANT_INFO's flat fields), and the full
    equipped dict only on the all-ratings-zero malformed-log fallback. Empty
    dict when item_db is unavailable (offline / requests missing)."""
    if _ITEM_DB is None:
        return {}
    return _ITEM_DB.resolve_equipped_stats(items)


@st.cache_data(show_spinner=False)
def _cached_hydrate_character(log_name: str, target: str, run_index: int) -> HydrateResult | None:
    """Cached COMBATANT_INFO → Character hydration.

    Brutoh's idea #b (2026-05-25): when a log is ACL-on, the COMBATANT_INFO
    row already carries spec / talents / gear / ratings — the user shouldn't
    have to paste a /simc export separately. This cache pins the hydrated
    result to (log, target, run) so target-picker changes flip the loaded
    character without re-scanning the file. Returns None for ACL-off logs
    or unresolvable targets; the caller treats None as "fall back to SimC
    paste".

    `_resolve_equipped_stats` is referenced from module scope (NOT a
    cache arg — st.cache_data can't hash a function) so the hydrated shield
    tank gets a real `shield_armor` instead of 0 (block value ≈ 0 → ~24pp
    over-pessimistic verdict otherwise).
    """
    runs = _cached_runs(log_name)
    if not runs or not (0 <= run_index < len(runs)):
        return hydrate_character(
            _resolve_log_path(log_name),
            target,
            resolve_stats_fn=_resolve_equipped_stats,
        )
    run = runs[run_index]
    return hydrate_character(
        _resolve_log_path(log_name),
        target,
        start_time_s=run.start_time_s,
        end_time_s=run.end_time_s,
        start_byte_offset=run.start_byte_offset,
        resolve_stats_fn=_resolve_equipped_stats,
    )


@st.cache_data(show_spinner=False)
def _cached_log_summary(log_name: str, target: str, run_index: int = -1):
    log_path = _resolve_log_path(log_name)
    # Reuse the cached CHALLENGE_MODE scan instead of re-parsing the
    # 80MB log on every helper. Cold-cache cost drops from 4 scans → 1.
    runs = _cached_runs(log_name)
    if not runs:
        return None, []
    if run_index < 0:
        run_index = len(runs) + run_index
    if not (0 <= run_index < len(runs)):
        return None, runs
    # Disk cache below the in-memory `@st.cache_data` layer — same-session
    # rerun stays in RAM; session restart hits disk; mtime/version drift
    # falls through to a fresh parse. Key prefix `summary::` so the
    # bundles for different helpers (mitigation_audit, death_analysis,
    # …) don't collide on the same (log, target, run_index) tuple.
    summary = get_or_compute(
        log_path,
        f"summary::{target}",
        run_index,
        lambda: summarize_run(log_path, target, runs[run_index]),
    )
    return summary, runs


@st.cache_data(show_spinner=False)
def _cached_death_analysis(log_name: str, target: str, run_index: int):
    runs = _cached_runs(log_name)
    if not runs or run_index >= len(runs):
        return []
    log_path = _resolve_log_path(log_name)
    return get_or_compute(
        log_path,
        f"deaths::{target}",
        run_index,
        lambda: reconstruct_deaths(log_path, target, runs[run_index]),
    )


@st.cache_data(show_spinner=False)
def _cached_all_deaths_for_target(log_name: str, target: str):
    """All reconstructed deaths for `target` across every run in `log_name`.

    Returns list[(run_label, death_events)]. Empty when the target wasn't
    auto-detected in any run of this log — cheap-path: checking party
    detection per run avoids reading an 80 MB log to confirm zero deaths.
    """
    runs = _cached_runs(log_name)
    if not runs:
        return []
    out: list[tuple[str, list]] = []
    for i, run in enumerate(runs):
        party = _cached_party_roles(log_name, i)
        if not any(m.name == target for m in party):
            continue
        des = reconstruct_deaths(_resolve_log_path(log_name), target, run)
        if des:
            dur_m = int(run.duration_s() // 60)
            timed = run.is_timed()
            if timed is True:
                outcome = "✓"
            elif timed is False:
                outcome = "✗"
            elif run.is_abandoned():
                outcome = "·"
            elif run.success:
                outcome = "·"  # completed but par_time unknown
            else:
                outcome = "·"
            label = f"{run.map_name} +{run.key_level} {outcome} ({dur_m}m)"
            out.append((label, des))
    return out


@st.cache_data(show_spinner=False)
def _cached_mitigation_audit(log_name: str, target: str, run_index: int):
    runs = _cached_runs(log_name)
    if not runs or run_index >= len(runs):
        return []
    log_path = _resolve_log_path(log_name)
    return get_or_compute(
        log_path,
        f"mitigation_audit::{target}",
        run_index,
        lambda: audit_replay(log_path, target, runs[run_index]),
    )


@st.cache_data(show_spinner=False)
def _cached_rage_events(log_name: str, target: str, run_index: int):
    """Parse the actor's SPELL_ENERGIZE (rage gains) + relevant SPELL_CAST_SUCCESS
    events (rage spenders) inside the picked run.

    Returns ``(energize_events, cast_events, run)``. Cached per
    (log_name, target, run_index) — same key shape as `_cached_run_events`
    so a fresh log selection or run pick invalidates cleanly.

    Cast events are the union of the four rage-spending Prot Warrior abilities
    modelled in `constants.yaml: rage.costs`. We pull each via a separate
    `parse_cast_events` call with `spell_id` for false-positive safety, then
    concatenate. Total scans = 4 sequential passes; on a 60 MB log that's
    ~2-3s the first time and cache hits thereafter.
    """
    runs = _cached_runs(log_name)
    if not runs or run_index >= len(runs):
        return [], [], None
    run = runs[run_index]
    log_path = _resolve_log_path(log_name)

    def _compute() -> tuple:
        energize_local = parse_energize_events(
            log_path,
            actor_name=target,
            start_time_s=run.start_time_s,
            end_time_s=run.end_time_s,
            start_byte_offset=run.start_byte_offset,
        )
        cast_specs = [
            (2565, "Shield Block"),
            (190456, "Ignore Pain"),
            (6572, "Revenge"),
            (6343, "Thunder Clap"),
        ]
        casts_local = []
        for sid, sname in cast_specs:
            casts_local.extend(
                parse_cast_events(
                    log_path,
                    source_name=target,
                    spell_name=sname,
                    spell_id=sid,
                    start_time_s=run.start_time_s,
                    end_time_s=run.end_time_s,
                    start_byte_offset=run.start_byte_offset,
                )
            )
        casts_local.sort(key=lambda c: c.time_s)
        return energize_local, casts_local

    # Disk cache + in-memory cache. The disk payload omits `run` (which is
    # a ChallengeModeRun the caller already has) so we re-attach it after
    # the lookup — the run object isn't pickle-cheap and is trivially
    # re-derivable from `_cached_runs(log_name)[run_index]`.
    energize, casts = get_or_compute(log_path, f"rage_events::{target}", run_index, _compute)
    return energize, casts, run


@st.cache_data(show_spinner=False)
def _cached_run_events(log_name: str, target: str, run_index: int):
    """One file scan: events + deaths + encounter windows for the chosen run.

    Returns (run, events, deaths, encounters, segments). Cached so the per-segment
    view does not re-read an 80MB log on every Streamlit rerun.
    """
    log_path = _resolve_log_path(log_name)
    runs = _cached_runs(log_name)
    if not runs or run_index >= len(runs):
        return None, [], [], [], []
    run = runs[run_index]

    def _compute() -> tuple:
        events_local = list(iter_damage_events(log_path, target, run.start_time_s, run.end_time_s))
        deaths_local = list(iter_death_events(log_path, target, run.start_time_s, run.end_time_s))
        encounters_local = parse_encounters(log_path, run.start_time_s, run.end_time_s)
        segments_local = segment_run(run, encounters_local)
        return run, events_local, deaths_local, encounters_local, segments_local

    return get_or_compute(log_path, f"run_events::{target}", run_index, _compute)


def _cached_coverage_report(log_name: str, target: str, run_index: int, class_spec: str | None):
    """Build the defensive-coverage report for the chosen run, or None.

    Returns None when there's nothing to render (no spec, no registry levers
    for the spec, or the run couldn't be loaded). The expensive part — parsing
    buff/debuff aura WINDOWS out of the log — is wrapped in the disk cache so
    Streamlit reruns don't re-scan an 80MB file. The cheap join itself runs
    fresh (it reads the already-cached damage events from `_cached_run_events`).
    """
    if not class_spec:
        return None
    from simf.core import coaching
    from simf.core.constants import load_constants

    constants = load_constants()
    levers = coaching.levers_for_spec(class_spec, constants)
    if not levers:
        return None
    interrupt_ability = coaching.interrupt_ability_for_spec(class_spec, constants)

    run, events, _deaths, _encs, _segs = _cached_run_events(log_name, target, run_index)
    if run is None or not events:
        return None

    log_path = _resolve_log_path(log_name)
    cfg = constants.get("coaching", {}) or {}
    top_n = int(cfg.get("top_hits_n", 8))
    min_events = int(cfg.get("min_events_for_verdict", 20))

    def _compute() -> tuple:
        buff_ids = frozenset(lv.spell_id for lv in levers if lv.detect == "buff")
        buff_windows = parse_self_buff_windows(
            log_path,
            target,
            buff_ids,
            start_time_s=run.start_time_s,
            end_time_s=run.end_time_s,
            start_byte_offset=run.start_byte_offset,
        )
        debuff_windows: dict[int, dict[str, list]] = {}
        for lv in levers:
            if lv.detect == "debuff_on_source":
                debuff_windows[lv.spell_id] = parse_source_debuff_windows(
                    log_path,
                    target,
                    lv.spell_id,
                    duration_s=lv.duration_s,
                    start_time_s=run.start_time_s,
                    end_time_s=run.end_time_s,
                    start_byte_offset=run.start_byte_offset,
                )
        # Party-wide (not target-filtered) — an interrupt by any player proves
        # the spell is interruptible, whether or not the tank was the one who
        # kicked it.
        interrupted_spell_ids = parse_interrupted_spell_ids(
            log_path,
            start_time_s=run.start_time_s,
            end_time_s=run.end_time_s,
            start_byte_offset=run.start_byte_offset,
        )
        # UNLIKE the interrupt-evidence scan above, this IS target-filtered —
        # the kick-availability split is about whether *this tank* personally
        # had their own kick up, not whether anyone in the party did.
        own_interrupt_cast_times: tuple[float, ...] = ()
        if interrupt_ability is not None:
            own_interrupt_cast_times = tuple(
                c.time_s
                for c in parse_cast_events(
                    log_path,
                    source_name=target,
                    spell_name=interrupt_ability.name,
                    spell_id=interrupt_ability.spell_id,
                    start_time_s=run.start_time_s,
                    end_time_s=run.end_time_s,
                    start_byte_offset=run.start_byte_offset,
                )
            )
        return buff_windows, debuff_windows, interrupted_spell_ids, own_interrupt_cast_times

    buff_windows, debuff_windows, interrupted_spell_ids, own_interrupt_cast_times = get_or_compute(
        log_path, f"coverage_windows::{target}::{class_spec}", run_index, _compute
    )
    return coaching.build_coverage_report(
        class_spec,
        events,
        levers=levers,
        buff_windows=buff_windows,
        debuff_windows_by_source=debuff_windows,
        run_start=run.start_time_s,
        run_end=run.end_time_s,
        is_bleed_fn=_is_bleed_spell,
        top_n=top_n,
        min_events=min_events,
        interrupted_spell_ids=interrupted_spell_ids,
        interrupt_ability=interrupt_ability,
        own_interrupt_cast_times=own_interrupt_cast_times,
    )
