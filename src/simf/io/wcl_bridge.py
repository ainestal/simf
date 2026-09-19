"""Bridge: Warcraft Logs report → simf's analysis pipeline.

The local-log surface (``ui/log_view.py``) consumes the outputs of
``summarize_run`` / ``audit_replay`` / ``reconstruct_deaths`` / and a
``ChallengeModeRun`` + raw event list. The WCL API surfaces the same
information through a different shape (one GraphQL fetch returns the
fight list + events + actor metadata).

This module is the thin adapter: one ``analyze_wcl_fight`` call fetches
all events for the picked fight and assembles a ``WCLAnalysisBundle``
that ``render_log_analysis`` can render unchanged. No new analysis
logic — we route WCL events through the same in-memory primitives the
file pipeline uses (``summarize_events_direct``, ``audit_events_direct``,
``segment_run``).

Cache integration: the disk cache in ``log_analysis_cache`` accepts
arbitrary ``CacheKey`` instances. We build one with ``make_wcl_key``
keyed on ``(report_code, fight_id, target)`` and the constants /
analysis versions. Re-loading the same fight is a cache hit; switching
fights inside the same report is a cache miss.
"""

from __future__ import annotations

from dataclasses import dataclass

from simf.io.combat_log import (
    ChallengeModeRun,
    DamageTakenEvent,
    DeathRecord,
    LogSummary,
    RunSegment,
    segment_run,
    summarize_events_direct,
)
from simf.io.death_analysis import WINDOW_S, DeathEvent
from simf.io.log_analysis_cache import get_or_compute_with_key, make_wcl_key
from simf.io.mitigation_audit import AbilityMitigationStats, audit_events_direct
from simf.io.wcl_api import (
    WCLFight,
    WCLReport,
    _fetch_actor_id,
    fetch_buff_windows,
    fetch_damage_events,
    fetch_dungeon_pulls,
    fetch_interrupted_spell_ids,
    fetch_own_cast_times,
    fetch_source_debuff_windows,
    fight_to_run,
)


@dataclass
class WCLAnalysisBundle:
    """Everything ``render_log_analysis`` needs to draw a Why-died surface.

    Field shapes match what the local-log surface assembles before calling
    ``render_log_analysis``. ``segments`` is a single-element list for WCL
    fights (a WCL fight maps to one M+ run with no extracted boss/trash
    encounters); the per-segment renderer collapses gracefully when only
    one segment is present.

    ``cross_log_*`` are kept as empty so the existing call-site signature
    stays unchanged. Multi-report aggregation isn't part of this PR — the
    local surface's cross-log scan reads files on disk, which has no WCL
    analog in the v2 API today.
    """

    summary: LogSummary
    mit_stats: list[AbilityMitigationStats]
    death_events: list[DeathEvent]
    run: ChallengeModeRun
    events: list[DamageTakenEvent]
    deaths: list[DeathRecord]
    segments: list[RunSegment]


def _deaths_from_events(
    events: list[DamageTakenEvent],
    run: ChallengeModeRun,
) -> list[DeathRecord]:
    """Infer death timestamps from overkill > 0 events.

    The WCL ``DamageTaken`` event stream carries ``overkill`` per event;
    the existing event-mapper preserves it. Any event with ``overkill>0``
    is a killing blow, so its timestamp is the death timestamp. This is
    weaker than the local-log ``UNIT_DIED`` query — WCL exposes a
    dedicated ``Deaths`` event type but pulling it would double the API
    cost of the import. For survivability triage the overkill-tagged
    event is sufficient (it carries the kill blow + 5s window of context
    via the surrounding damage events).
    """
    deaths: list[DeathRecord] = []
    for ev in events:
        if ev.overkill > 0:
            deaths.append(DeathRecord(time_s=ev.time_s, rel_time_s=ev.time_s - run.start_time_s))
    return deaths


def _reconstruct_death_events(
    events: list[DamageTakenEvent],
    deaths: list[DeathRecord],
) -> list[DeathEvent]:
    """Same shape as ``death_analysis.reconstruct_deaths`` but operates on
    in-memory events (no file scan). For each ``DeathRecord``, collect the
    5-second damage window preceding it."""
    out: list[DeathEvent] = []
    for death in deaths:
        window = [e for e in events if death.time_s - WINDOW_S <= e.time_s <= death.time_s]
        total = sum(e.amount for e in window)
        max_hit = max((e.amount for e in window), default=0)
        out.append(
            DeathEvent(
                death=death,
                preceding=window,
                total_damage_window=total,
                max_hit=max_hit,
                num_hits=len(window),
            )
        )
    return out


def analyze_wcl_fight(
    report: WCLReport,
    fight: WCLFight,
    target: str,
    token: str,
    *,
    cache_dir=None,
    target_actor_id: int | None = None,
) -> WCLAnalysisBundle:
    """Fetch + analyze one WCL fight. Cached on
    ``(report_code, fight_id, target)`` against the disk analysis cache.

    ``cache_dir`` is an optional override used by tests; production callers
    leave it None to land on the standard ``~/.simf/log_cache/`` location.

    ``target_actor_id`` skips the masterData name lookup inside
    ``fetch_damage_events``. The UI picker hands this through from
    playerDetails so the realm-suffix matching ambiguity (``Somename`` vs
    ``Somename-Faketown``) can't bite.
    """
    from simf.io.log_analysis_cache import DEFAULT_CACHE_DIR

    if cache_dir is None:
        cache_dir = DEFAULT_CACHE_DIR
    key = make_wcl_key(report.code, fight.id, target)

    def _compute() -> WCLAnalysisBundle:
        events = fetch_damage_events(
            report, fight, target, token=token, target_actor_id=target_actor_id
        )
        run = fight_to_run(fight, report.start_time_ms)
        deaths = _deaths_from_events(events, run)

        summary = summarize_events_direct(events, run, deaths=deaths)
        mit_stats = audit_events_direct(events)
        death_events = _reconstruct_death_events(events, deaths)
        # WCL exposes boss-vs-trash structure inside an M+ fight via
        # ``fight.dungeonPulls`` — one entry per pull, with ``encounterID!=0``
        # marking the boss pulls. Map those into ``EncounterWindow`` objects
        # so ``segment_run`` produces the same boss + trash segment
        # hierarchy the local-log path generates from ENCOUNTER_START/END.
        # Non-M+ fights have ``dungeonPulls: null`` — ``fetch_dungeon_pulls``
        # returns ``[]`` and ``segment_run`` falls back to one whole-run
        # trash segment (correct for that shape).
        try:
            encounters = fetch_dungeon_pulls(report, fight, token=token)
        except Exception:
            # Bug-for-bug compat: a dungeonPulls fetch failure should not
            # break the whole analyze — fall back to the unsegmented view.
            encounters = []
        segments = segment_run(run, encounters=encounters)
        return WCLAnalysisBundle(
            summary=summary,
            mit_stats=mit_stats,
            death_events=death_events,
            run=run,
            events=events,
            deaths=deaths,
            segments=segments,
        )

    return get_or_compute_with_key(key, _compute, cache_dir=cache_dir)


def build_wcl_coverage_report(
    report: WCLReport,
    fight: WCLFight,
    target: str,
    token: str,
    *,
    events: list[DamageTakenEvent],
    run: ChallengeModeRun,
    class_spec: str | None,
    target_actor_id: int | None = None,
    cache_dir=None,
):
    """Build the defensive-coverage report for a WCL fight, or None.

    The WCL analog of the local-log surface's ``_cached_coverage_report``: it
    feeds the same model-independent hit-vs-coverage join
    (``coaching.build_coverage_report``), but sources the self-buff windows
    from the WCL ``Buffs`` event stream (``fetch_buff_windows``) instead of a
    file scan. ``fetch_buff_windows`` already returns the exact
    ``{spell_id: [(start_s, end_s), ...]}`` shape the join expects.

    Returns None when there's nothing to render — no spec, no registry levers
    for the spec, or the tank's WCL actor ID can't be resolved (no actor →
    can't query their buffs).

    Caching: the network part (the ``Buffs`` fetch and, for specs with a
    ``debuff_on_source`` lever, the ``Debuffs`` fetch) is wrapped in the disk
    cache on coverage-specific keys — ``::coverage::<spec>`` and
    ``::coverage-debuffs::<spec>`` suffixes on the standard WCL key — so each
    lands as its OWN entry rather than perturbing the analysis bundle. The
    cheap join runs fresh on the already-in-memory events.

    The ``Interrupts`` fetch (``fetch_interrupted_spell_ids``) is cached under
    a spec-and-target-independent ``"interrupts"`` key — evidence of what got
    kicked in this fight has nothing to do with which tank or spec is being
    analyzed, so every spec/target combination viewing the same fight shares
    one cache entry instead of refetching identical data per spec. This is
    the WCL-path parity fast-follow the local-log ``interruptible`` lever
    (``core/coaching.py``) deliberately deferred at ship time — see
    ``docs/validation/interruptible_cast_coverage_lever_2026_07_03.md``.

    ``debuff_on_source`` levers (today only Prot Warrior's Demoralizing Shout, a
    minor-tier −20% enemy debuff) are credited spawn-precisely:
    ``fetch_source_debuff_windows`` keys windows by the debuffed enemy's spawn
    (``_spawn_key`` = WCL actor id + instance), the SAME key ``_map_event``
    stamps on each hit's ``source_guid``, so a hit is credited the debuff only
    when the *exact* mob that dealt it carried it (never a blanket window).
    Specs with no such lever issue no extra query (cost stays zero). Crediting
    the hit's source requires the bundle's events to carry the spawn key —
    populated by ``_map_event`` behind ``analysis_version`` (bumped for this
    change), so a warm pre-bump bundle re-builds before the join runs.
    """
    if not class_spec:
        return None

    from simf.core import coaching
    from simf.core.bleed_detection import is_bleed
    from simf.core.constants import load_constants
    from simf.io.log_analysis_cache import DEFAULT_CACHE_DIR

    if cache_dir is None:
        cache_dir = DEFAULT_CACHE_DIR

    constants = load_constants()
    levers = coaching.levers_for_spec(class_spec, constants)
    if not levers:
        return None

    actor_id = target_actor_id
    if actor_id is None:
        actor_id = _fetch_actor_id(report.code, target, token, cache_dir=cache_dir)
    if actor_id is None:
        return None

    cfg = constants.get("coaching", {}) or {}
    top_n = int(cfg.get("top_hits_n", 8))
    min_events = int(cfg.get("min_events_for_verdict", 20))

    buff_ids = frozenset(lv.spell_id for lv in levers if lv.detect == "buff")
    debuff_levers = [lv for lv in levers if lv.detect == "debuff_on_source"]

    def _fetch_buffs() -> dict:
        if not buff_ids:
            return {}
        return fetch_buff_windows(
            report, fight, actor_id, token, ability_ids=set(buff_ids), cache_dir=cache_dir
        )

    buff_key = make_wcl_key(report.code, fight.id, f"{target}::coverage::{class_spec}")
    buff_windows = get_or_compute_with_key(buff_key, _fetch_buffs, cache_dir=cache_dir)

    # debuff_on_source levers (Demoralizing Shout) need a SEPARATE Debuffs fetch
    # on the enemy side, keyed by enemy spawn so the join is spawn-precise. Only
    # specs that have such a lever pay the extra query.
    debuff_windows: dict[int, dict[str, list]] = {}
    if debuff_levers:

        def _fetch_debuffs() -> dict:
            out: dict[int, dict[str, list]] = {}
            for lv in debuff_levers:
                out[lv.spell_id] = fetch_source_debuff_windows(
                    report, fight, actor_id, token, lv.spell_id, duration_s=lv.duration_s
                )
            return out

        debuff_key = make_wcl_key(
            report.code, fight.id, f"{target}::coverage-debuffs::{class_spec}"
        )
        debuff_windows = get_or_compute_with_key(debuff_key, _fetch_debuffs, cache_dir=cache_dir)

    def _fetch_interrupts() -> frozenset[int]:
        return fetch_interrupted_spell_ids(report, fight, token)

    interrupts_key = make_wcl_key(report.code, fight.id, "interrupts")
    interrupted_spell_ids = get_or_compute_with_key(
        interrupts_key, _fetch_interrupts, cache_dir=cache_dir
    )

    # Cooldown-aware kick-availability split: UNLIKE the interrupts fetch
    # above (party-wide evidence), this IS actor-specific — it's asking when
    # *this tank* personally cast *their* baseline interrupt, so it's keyed
    # per (target, spec) rather than shared across the fight.
    interrupt_ability = coaching.interrupt_ability_for_spec(class_spec, constants)
    own_interrupt_cast_times: tuple[float, ...] = ()
    if interrupt_ability is not None:

        def _fetch_own_casts() -> tuple[float, ...]:
            return fetch_own_cast_times(report, fight, actor_id, interrupt_ability.spell_id, token)

        casts_key = make_wcl_key(report.code, fight.id, f"{target}::coverage-kick::{class_spec}")
        own_interrupt_cast_times = get_or_compute_with_key(
            casts_key, _fetch_own_casts, cache_dir=cache_dir
        )

    return coaching.build_coverage_report(
        class_spec,
        events,
        levers=levers,
        buff_windows=buff_windows,
        debuff_windows_by_source=debuff_windows,
        run_start=run.start_time_s,
        run_end=run.end_time_s,
        is_bleed_fn=is_bleed,
        top_n=top_n,
        min_events=min_events,
        interrupted_spell_ids=interrupted_spell_ids,
        interrupt_ability=interrupt_ability,
        own_interrupt_cast_times=own_interrupt_cast_times,
    )
