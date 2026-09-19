"""Adapt Warcraft Logs damage events into engine-shaped ``ReplayData``.

The K-calibration CLI (``simf calibrate-k``) reads ``ReplayData`` produced
by ``log_replay.load_replay`` for raw ``*WoWCombatLog-*.txt`` files. Phase 4
calibration for non-Warrior tank specs (Blood DK / VDH / Brewmaster /
Guardian) is bottlenecked on receiving such .txt files from strangers,
but WCL hosts a public corpus of the same fights. This adapter turns a
single WCL fight into the same ``ReplayData`` shape so the calibrator
doesn't have to know the source.

Field mapping (DamageTakenEvent → DamageEvent) mirrors
``log_replay.load_replay``:

- ``time_s`` is rebased to run-relative (WCL gives absolute Unix; engine
  wants seconds from fight start).
- ``raw_amount`` uses ``base_amount`` (pre-mit) with a fallback to
  ``amount + blocked + absorbed + resisted`` when ``base_amount == 0`` —
  some older logs don't carry the unmitigated value.
- Self-inflicted events (Stagger DoT ticks etc.) use ``amount`` (post-mit
  HP delta) as ``raw_amount`` and bypass further DR, matching the
  local-log path.
- ``is_blockable`` is true only for physical melee/ranged.
- ``is_bleed`` runs through ``core.bleed_detection.is_bleed`` on the
  spell name — bleeds are a strict subset of DoT ticks; we don't conflate
  them.

DoT-tick classification — live-probe finding (2026-05-27):
  The .txt path drives ``is_dot_tick`` from
  ``event_type == "SPELL_PERIODIC_DAMAGE"``. ``wcl_api._map_event``
  collapses every non-melee event to ``SPELL_DAMAGE``, losing that
  distinction. WCL's raw events payload carries a ``tick: true`` boolean
  on periodic damage; ``_map_event`` reads it into
  ``DamageTakenEvent.tick_flag`` and this adapter uses it directly.
  Confirmed live on 2026-05-27 against report ExampleCode5555555 fight 1
  (Brutoh +13 Nexus-Point Xenas): ``tick`` was present-and-true on
  349/1650 (21%) Brutoh-targeted events covering Searing Rend, Arcing
  Mana, Sparkburn, etc. — exactly the periodic ability set. No
  alternative spellings (``isTick``, ``periodic``, ``isPeriodic``,
  ``attackType``) appeared in the response.
  Defensive default: if the WCL response shape ever drops or renames the
  field, ``tick_flag`` stays ``False`` and ``is_dot_tick`` is ``False``
  across the run — the K-sweep math is unaffected (the flag is a
  routing/labelling hint, not a math-affecting input for K calibration).
  No bleed-name fallback: bleeds are a strict subset of DoT ticks and
  using a bleed allowlist for DoT detection would mis-flag every other
  periodic ability.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from ..core.bleed_detection import is_bleed
from ..core.events import AttackType, DamageEvent, DamageSchool
from .combat_log import DamageTakenEvent
from .log_replay import ReplayData
from .wcl_api import (
    _get_token,
    fetch_damage_events,
    fetch_report,
    fight_to_run,
)


def _normalise_actor_name(name: str) -> str:
    """Strip realm suffix and lowercase, matching ``_fetch_actor_id``.

    WCL actor names sometimes carry the realm (``Brutoh-Stormrage``) and
    sometimes don't; the local-log path passes the full
    ``Brutoh-Uldum-EU`` form. To detect self-inflicted events
    (e.g. Stagger DoT ticks where ``source_name == target_name``) we
    compare on the bare name lowercased, the same normalisation WCL's
    masterData lookup uses.
    """
    return name.split("-")[0].lower()


def adapt_events(
    raw_events: list[DamageTakenEvent],
    *,
    target_name: str,
    run_start_time_s: float,
    buff_windows: dict[int, list[tuple[float, float]]] | None = None,
) -> tuple[list[DamageEvent], dict[str, int]]:
    """Translate a list of WCL ``DamageTakenEvent`` to engine ``DamageEvent``.

    Returns ``(events, aggregates)`` where ``aggregates`` is a dict of
    ``actual_dealt / actual_blocked / actual_absorbed / actual_resisted``
    summed straight from the input, the same way ``load_replay`` does.

    ``buff_windows`` (``{ability_id: [(start_s, end_s), ...]}`` in absolute
    seconds, from ``wcl_api.fetch_buff_windows``) stamps each non-self damage
    event's ``active_buffs`` with the tank buffs up at that instant — the input
    a window-gated mitigation layer (Vengeance Metamorphosis' armor multiplier,
    Painbringer) reads. ``None`` leaves ``active_buffs`` empty (bit-identical to
    the prior behaviour and to every spec that doesn't read it).

    Pulled out of the live-fetch helper so unit tests can hit it with
    synthetic fixtures without mocking the HTTP layer.
    """
    target_bare = _normalise_actor_name(target_name)

    def _active_at(t_s: float) -> frozenset[int]:
        if not buff_windows:
            return frozenset()
        return frozenset(
            aid
            for aid, wins in buff_windows.items()
            if any(start <= t_s <= end for start, end in wins)
        )

    events: list[DamageEvent] = []
    actual_dealt = 0
    actual_blocked = 0
    actual_absorbed = 0
    actual_resisted = 0

    for dte in raw_events:
        is_self = _normalise_actor_name(dte.source_name) == target_bare

        attack_type: AttackType
        if dte.event_type.startswith("SWING"):
            attack_type = "melee"
        elif dte.event_type == "RANGE_DAMAGE":
            attack_type = "ranged"
        else:
            attack_type = "spell"
        school = cast(DamageSchool, dte.school)

        # Pre-mit amount: prefer ``base_amount`` (== ``unmitigatedAmount``
        # in WCL); fall back to reconstructing it from the mitigated
        # amount + the absorb/block/resist pools when WCL omitted it.
        if dte.base_amount:
            raw_amount_other = float(dte.base_amount)
        else:
            raw_amount_other = float(dte.amount + dte.blocked + dte.absorbed + dte.resisted)

        if is_self:
            events.append(
                DamageEvent(
                    time_s=dte.time_s - run_start_time_s,
                    source_id=dte.source_name,
                    school=school,
                    raw_amount=float(dte.amount),
                    attack_type=attack_type,
                    is_dot_tick=True,
                    is_avoidable=False,
                    is_blockable=False,
                    is_log_replay=True,
                    log_absorbed=0.0,
                    is_self_inflicted=True,
                )
            )
        else:
            events.append(
                DamageEvent(
                    time_s=dte.time_s - run_start_time_s,
                    source_id=dte.source_name,
                    school=school,
                    raw_amount=raw_amount_other,
                    attack_type=attack_type,
                    is_dot_tick=dte.tick_flag,
                    is_avoidable=False,
                    is_blockable=(attack_type in ("melee", "ranged") and dte.school == "physical"),
                    # A same-named direct hit (e.g. a boss's direct "Searing
                    # Rend" swing alongside its DoT tick) is ordinary
                    # armor-mitigated physical damage, not a bleed — gate on
                    # tick_flag too, not name alone (2026-07-18).
                    is_bleed=is_bleed(dte.spell_name, is_periodic=dte.tick_flag),
                    is_log_replay=True,
                    log_absorbed=float(dte.absorbed),
                    active_buffs=_active_at(dte.time_s),
                )
            )
        actual_dealt += dte.amount
        actual_blocked += dte.blocked
        actual_absorbed += dte.absorbed
        actual_resisted += dte.resisted

    aggregates = {
        "actual_dealt": actual_dealt,
        "actual_blocked": actual_blocked,
        "actual_absorbed": actual_absorbed,
        "actual_resisted": actual_resisted,
    }
    return events, aggregates


def wcl_to_replay_data(
    report_code: str,
    fight_id: int,
    target_name: str,
    *,
    target_actor_id: int | None = None,
    buff_ability_ids: set[int] | None = None,
    cache_dir: Path | None = None,
) -> ReplayData:
    """Fetch one WCL fight and return engine-shaped ``ReplayData``.

    Mirrors ``log_replay.load_replay``: same ``ReplayData`` fields, same
    per-event semantics. Callers (the K-calibrator in particular) don't
    need to know the source — drop the result into ``replays`` alongside
    local-log results and the K sweep math stays unchanged.

    Network: this hits WCL via ``wcl_api.fetch_report`` +
    ``fetch_damage_events`` and requires credentials in either env vars
    (``WCL_CLIENT_ID`` / ``WCL_CLIENT_SECRET``) or
    ``~/.simf/wcl_config.yaml``. ``is_configured()`` will report cleanly
    when they're missing.

    Args:
        report_code: WCL report code (e.g. ``"abc123XYZ"``). Use
            ``wcl_api.url_to_code`` to extract from a share URL.
        fight_id: WCL fight ID inside the report (the ``#fight=N``
            fragment in a share URL).
        target_name: Character name (with or without realm suffix; the
            adapter normalises for self-inflicted detection).
        target_actor_id: Optional WCL actor ID short-circuit for the
            masterData name lookup. Pass ``url_to_source_id(url)`` when
            available.
        cache_dir: Optional opt-in on-disk cache directory (see
            ``wcl_api._gql``'s docstring) — omit for the production
            calibrate-k --wcl-url path, which stays live/uncached.

    Raises:
        ValueError: fight_id not present in the report, or target_name
            not found among the report's actors.
        RuntimeError: missing WCL credentials (from ``_get_token``).
    """
    token = _get_token()
    report = fetch_report(report_code, token, cache_dir=cache_dir)
    fight = next((f for f in report.fights if f.id == fight_id), None)
    if fight is None:
        ids = ", ".join(str(f.id) for f in report.fights)
        raise ValueError(f"Fight {fight_id} not in report {report_code}. Available: [{ids}]")

    raw_events = fetch_damage_events(
        report, fight, target_name, token, target_actor_id=target_actor_id, cache_dir=cache_dir
    )
    run = fight_to_run(fight, report.start_time_ms)

    # Window-gated mitigation layers (Vengeance Metamorphosis armor, Painbringer)
    # need the tank's buff windows. Only fetched when the caller names the buffs
    # to gate on — keeps the default path (one extra query avoided) unchanged.
    buff_windows = None
    if buff_ability_ids:
        from .wcl_api import _fetch_actor_id, fetch_buff_windows

        sid = target_actor_id
        if sid is None:
            sid = _fetch_actor_id(report_code, target_name, token, cache_dir=cache_dir)
        if sid is not None:
            buff_windows = fetch_buff_windows(
                report, fight, sid, token, ability_ids=set(buff_ability_ids), cache_dir=cache_dir
            )

    events, agg = adapt_events(
        raw_events,
        target_name=target_name,
        run_start_time_s=run.start_time_s,
        buff_windows=buff_windows,
    )

    return ReplayData(
        run=run,
        duration_s=run.duration_s(),
        events=events,
        actual_dealt=agg["actual_dealt"],
        actual_blocked=agg["actual_blocked"],
        actual_absorbed=agg["actual_absorbed"],
        actual_resisted=agg["actual_resisted"],
        event_count=len(events),
    )


__all__ = ["adapt_events", "wcl_to_replay_data"]
