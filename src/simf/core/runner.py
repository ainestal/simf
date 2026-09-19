import random
from dataclasses import replace as dc_replace

import numpy as np

from .character import Character
from .constants import load_constants
from .cooldown_planner import (
    CooldownPlan,
    cd_slots_for,
    consume_due_presses,
    planned_abilities,
)
from .metrics import (
    SRD_DEFAULT_WINDOW_S,
    SRD_M_PLUS_WINDOW_S,
    IterationResult,
    SimResult,
    compute_tmi,
    compute_window_max,
)
from .mitigation import MitigationState, apply_mitigation
from .policy import make_policy
from .profiles import DamageProfile, HealingExternal, HealingProfile
from .timeline import events_in_window, generate_events


def _tile_externals(externals: list[HealingExternal], duration_s: float) -> list[HealingExternal]:
    """Repeat the externals schedule across the full run duration."""
    if not externals:
        return []
    schedule_period = max(e.time_s for e in externals) + 30.0  # 30s gap before next cycle
    tiled: list[HealingExternal] = []
    cycle = 0
    while cycle * schedule_period < duration_s:
        offset = cycle * schedule_period
        for ext in externals:
            t = ext.time_s + offset
            if t >= duration_s:
                continue
            tiled.append(dc_replace(ext, time_s=t))
        cycle += 1
    tiled.sort(key=lambda e: e.time_s)
    return tiled


def run_simulation(
    character: Character,
    damage_profile: DamageProfile | None,
    healing_profile: HealingProfile,
    iterations: int = 2000,
    seed: int = 42,
    events_override: list | None = None,
    duration_override: float | None = None,
    target_error: float = 0.0,
    min_iterations: int = 50,
    convergence_check_every: int = 50,
    talent_set_override: set[str] | None = None,
    compute_metrics: bool = True,
    compute_tmi_metrics: bool = True,
    cooldown_plan: CooldownPlan | None = None,
    skill_modifier: float = 1.0,
    keep_iteration_results: bool = False,
) -> SimResult:
    """Run Monte Carlo. Either pass a damage_profile (events are generated each
    iteration) or events_override (replay mode, same events every iteration).

    If ``target_error > 0``, iterations stop early when the relative standard
    error of mean DTPS falls below the threshold (per SimC's ``target_error``
    convention). ``iterations`` is then the *maximum*; we always run at least
    ``min_iterations`` first.

    If ``talent_set_override`` is provided, it is used directly instead of
    looking up ``character.talents`` in constants.yaml.

    If ``keep_iteration_results`` is True, the returned SimResult carries the
    per-iteration results (heavy — full timelines). Used by the talent A/B
    machinery for paired-bootstrap CIs on common-random-number deltas; callers
    must drop the reference once done, never cache it.
    """
    c = load_constants()
    if talent_set_override is not None:
        talent_set = set(talent_set_override)
        # Make the override authoritative for build-time stats too: max_hp() and
        # total_armor() read character._talent_set(), and MitigationState caches
        # both at construction (before state.talents is assigned). Without this
        # rebind, armor/HP talents (armor_specialization, reinforced_plates,
        # indomitable) would be invisible to the per-arm sim. dc_replace returns
        # a copy — the caller's character is never mutated.
        character = dc_replace(character, talent_set_override=frozenset(talent_set))
    else:
        talent_set = set(c.get("talent_loadouts", {}).get(character.talents, {}).get("talents", []))

    if duration_override is not None:
        duration_s = duration_override
    elif damage_profile is not None:
        duration_s = damage_profile.duration_s
    elif events_override:
        duration_s = max(e.time_s for e in events_override) + 5.0
    else:
        raise ValueError("run_simulation requires damage_profile or events_override")

    iteration_results: list[IterationResult] = []

    # Phase 2.10 — separate RNG for skill_modifier draws so the iteration RNG
    # (events / mitigation rolls) is untouched. Without this, consuming a
    # random number for a skill check shifts every downstream draw and silently
    # breaks every pinned-seed test. The offset is arbitrary but constant.
    _SKILL_RNG_OFFSET = 0x5_F11_5_A20
    _use_skill_rng = skill_modifier < 1.0

    for it in range(iterations):
        rng = random.Random(seed + it)
        skill_rng = random.Random(seed + it + _SKILL_RNG_OFFSET) if _use_skill_rng else None
        if events_override is not None:
            events = events_override
        else:
            assert damage_profile is not None, (  # noqa: S101 — internal API contract, not user input
                "run_simulation requires either damage_profile or events_override"
            )
            events = generate_events(damage_profile, rng)

        state = MitigationState(character)
        state.talents = talent_set
        policy = make_policy(character, skill_modifier=skill_modifier, skill_rng=skill_rng)

        # Plan owns its declared long-CD buttons — block the heuristic
        # in policy.decide() from firing them on its own. The plan
        # press itself will assign cd_until to a finite value when it
        # fires; until then, inf keeps the heuristic from preempting.
        if cooldown_plan is not None and cooldown_plan.presses:
            for cd_slot in cd_slots_for(character.class_spec, planned_abilities(cooldown_plan)):
                setattr(state, cd_slot, float("inf"))

        # Auto-scale baseline HPS. dtps_estimate uses 14% of pre-mit raw damage
        # as a pessimistic estimate of post-mitigation DTPS. Observed range across
        # real M+ logs: 9–15%. Using 14% prevents under-healing in magic-heavy runs.
        raw_dtps = sum(e.raw_amount for e in events) / duration_s
        if healing_profile.baseline_hps_abs > 0:
            baseline_hps = healing_profile.baseline_hps_abs
        else:
            dtps_estimate = raw_dtps * 0.14
            baseline_hps = dtps_estimate * healing_profile.baseline_hps_pct_of_dtps

        # Mastery: Nature's Guardian (Guardian) — heals + absorbs RECEIVED scale by
        # (1 + 0.7×mastery). Returns 1.0 for every non-Guardian spec, so each
        # ``* heal_mult`` below is a bit-identical no-op for them. See
        # Character.incoming_healing_multiplier.
        heal_mult = character.incoming_healing_multiplier()

        # Tile externals over the full duration — the healer doesn't stop after 120s.
        externals = _tile_externals(healing_profile.externals, duration_s)
        next_external_idx = 0
        # Cursor for the planned-cooldown overrides (Phase 6.1).
        next_plan_idx = 0

        damage_timeline: list[tuple[float, float]] = []
        heal_timeline: list[tuple[float, float]] = []

        died = False
        time_to_die: float | None = None
        last_t = 0.0
        raw_total = 0.0
        dealt_total = 0.0
        # Token-bucket healer-throughput cap (Top-5 #3, 2026-07-06 retrospective).
        # 0.0 capacity means the profile doesn't set it — uncapped, bit-identical
        # to the pre-cap behavior. Bank starts full so an isolated burst at t=0
        # is always fully funded; only sustained draws deplete it faster than
        # the refill rate replaces it. See HealingProfile's field docstring.
        _healer_budget_enabled = healing_profile.healer_budget_capacity_pct_of_max_hp > 0
        healer_budget = healing_profile.healer_budget_capacity_pct_of_max_hp * state.max_hp
        min_hp_seen = 1.0  # lowest HP fraction reached, tracked post-mitigation/post-save
        sb_active_time = 0.0
        sb_rage_starved_s = 0.0
        sb_charge_limited_s = 0.0
        _sb_rage_cost = c["active_mitigation"]["shield_block"]["rage_cost"]
        _is_prot_warrior = character.class_spec == "protection_warrior"
        reactive_ready_at = 0.0  # next time a reactive burst is allowed

        # Disposition ledger accumulators (2026-07-09) — per-iteration running
        # SUMS, not per-event lists (this loop runs thousands of times per
        # iteration across thousands of iterations; a list would blow up
        # memory). The mitigation-chain buckets (avoided/blocked/armor/vers/
        # dr_layers) only ever come back non-zero from `apply_mitigation` for
        # Protection Warrior — every other spec's per-spec mitigation module
        # doesn't set those keys, so `.get(..., 0.0)` below silently sums
        # zero for them. `_is_prot_warrior` (already computed above for the
        # SB-gap diagnosis) is reused as the one flag that tells the
        # aggregator whether those particular sums are a real measurement or
        # just "not instrumented yet" — see `IterationResult.disposition_instrumented`.
        disp_avoided_raw = 0.0
        disp_blocked_cut = 0.0
        disp_armor_cut = 0.0
        disp_vers_cut = 0.0
        disp_dr_layers_cut = 0.0
        disp_absorbed_ip = 0.0
        disp_absorbed_healer = 0.0
        # Healed-back split is NOT spec-gated — self_heal_events plus the
        # warrior-only Fueled by Violence heal (below) are both genuinely
        # separable from the healer/reactive-heal stream at this point in
        # the loop for every spec, so these two sums are real measurements
        # regardless of `_is_prot_warrior`.
        disp_healed_self = 0.0
        disp_healed_external = 0.0

        # Adaptive-lookahead Phase A (2026-05-23). The runner threads a
        # time-bounded slice of upcoming events into every ``policy.decide``
        # call so a future Phase B can react to imminent damage (a
        # tank-buster within ~2s, an upcoming spike) rather than only the
        # current event + recent_dtps. No current policy consumes the
        # slice — bit-identity at every pinned seed is preserved with the
        # default. ``policy.lookahead_window_s == 0`` short-circuits the
        # helper allocation cost entirely.
        lookahead_window_s = float(c.get("policy", {}).get("lookahead_window_s", 0.0))

        for current_idx, event in enumerate(events):
            now = event.time_s
            dt = now - last_t
            # Accumulate SB coverage for the [last_t, now] interval before updating last_t.
            sb_active_in_interval = max(0.0, min(state.shield_block_until, now) - max(last_t, 0.0))
            sb_active_time += sb_active_in_interval
            # Diagnose SB gaps: check state at last_t before this event's policy runs.
            if _is_prot_warrior:
                sb_gap = dt - sb_active_in_interval
                if sb_gap > 0:
                    if state.shield_block_charges_available(last_t) > 0:
                        sb_rage_starved_s += sb_gap  # had a charge, not enough rage
                    else:
                        sb_charge_limited_s += sb_gap  # both charges recharging
            last_t = now
            raw_total += event.raw_amount

            # Last Stand expiry: remove the HP boost when the buff fades.
            if state.last_stand_base_max_hp > 0 and now >= state.last_stand_until > 0:
                state.max_hp = state.last_stand_base_max_hp
                state.hp = min(state.hp, state.max_hp)
                state.last_stand_base_max_hp = 0.0

            policy.tick(state, now)

            if _healer_budget_enabled:
                cap = healing_profile.healer_budget_capacity_pct_of_max_hp * state.max_hp
                refill_rate = (
                    healing_profile.healer_budget_refill_pct_of_max_hp_per_s * state.max_hp
                )
                healer_budget = min(cap, healer_budget + refill_rate * dt)

            # Budget is charged at the NOMINAL (pre-heal_mult) rate — mastery
            # (Nature's Guardian etc.) makes a given cast land for more HP on
            # the receiving tank, it doesn't make the healer's cast cost less
            # or the tank's own resource stretch further. Multiplying by
            # heal_mult before the budget draw would make higher mastery
            # drain the SAME finite bank faster for no in-game reason —
            # caught by test_guardian_mastery_has_positive_survival_marginal
            # flipping sign when this was wired up wrong.
            nominal_heal = baseline_hps * dt
            if _healer_budget_enabled:
                nominal_heal = min(nominal_heal, healer_budget)
            heal_amount = nominal_heal * heal_mult
            if heal_amount > 0:
                actual_heal = min(state.max_hp - state.hp, heal_amount)
                state.hp = min(state.max_hp, state.hp + heal_amount)
                heal_timeline.append((now, actual_heal))
                disp_healed_external += actual_heal
                if _healer_budget_enabled:
                    # Charge the bank for healing that actually LANDED
                    # (converted back to nominal/pre-heal_mult units), not the
                    # full offered amount — measure_healer_budget.py measured
                    # R/B net of overhealing, so charging gross here would
                    # drain the bank on overheal a real healer's throughput
                    # ceiling was never spent on (validator-confirmed
                    # 2026-07-07: this mismatch alone flipped two real
                    # zero-death log runs to 100% simulated death_rate).
                    healer_budget -= actual_heal / heal_mult

            while next_external_idx < len(externals) and externals[next_external_idx].time_s <= now:
                ext = externals[next_external_idx]
                if ext.type == "absorb":
                    state.healer_absorb = max(state.healer_absorb, (ext.amount or 0.0) * heal_mult)
                    state.healer_absorb_until = now + ext.duration_s
                elif ext.type == "dr_cooldown":
                    state.healer_dr_until = now + ext.duration_s
                    state.healer_dr_amount = ext.amount_pct or 0.0
                elif ext.type == "heal":
                    h = (ext.amount or 0.0) * heal_mult
                    actual_heal = min(state.max_hp - state.hp, h)
                    state.hp = min(state.max_hp, state.hp + h)
                    heal_timeline.append((now, actual_heal))
                    disp_healed_external += actual_heal
                next_external_idx += 1

            recent_dmg = state.recent_damage_total(now, 5.0)
            recent_dtps = recent_dmg / 5.0 if recent_dmg > 0 else 0.0

            # Apply any planned long-CD presses BEFORE the heuristic
            # policy runs so it sees them as already-pressed.
            if cooldown_plan is not None:
                next_plan_idx = consume_due_presses(state, cooldown_plan, now, next_plan_idx)

            upcoming = (
                events_in_window(events, current_idx, lookahead_window_s)
                if lookahead_window_s > 0
                else None
            )
            policy.decide(state, now, recent_dtps, event, upcoming_events=upcoming)

            # Drain self-kit heals applied inside policy.tick()/decide() (Guardian
            # Tooth & Claw + Frenzied Regeneration) into heal_timeline so HRPS /
            # ETMI / Normalized Tank Score credit self-sustain. Order-independent:
            # the metrics bin heal_timeline by timestamp (metrics.py compute_window_max
            # / tmi_iteration_expsums). Empty (no-op) for specs whose policy records
            # nothing — the warrior path stays bit-identical.
            if state.self_heal_events:
                disp_healed_self += sum(h for _, h in state.self_heal_events)
                heal_timeline.extend(state.self_heal_events)
                state.self_heal_events.clear()

            result = apply_mitigation(state, event, rng)
            damage = result["dealt"]
            dealt_total += damage

            # Disposition ledger (2026-07-09) — Protection Warrior only; every
            # other spec's apply_*_mitigation result dict doesn't carry these
            # keys, so `.get(..., 0.0)` sums zero for them (see
            # `disp_avoided_raw`'s init comment above — `_is_prot_warrior`,
            # not this dict, is the flag that tells the aggregator whether
            # zero means "measured zero" or "not instrumented").
            disp_avoided_raw += result.get("avoided_raw", 0.0)
            disp_blocked_cut += result.get("blocked_cut", 0.0)
            disp_armor_cut += result.get("armor_cut", 0.0)
            disp_vers_cut += result.get("vers_cut", 0.0)
            disp_dr_layers_cut += result.get("dr_layers_cut", 0.0)
            disp_absorbed_ip += result.get("absorbed_by_ignore_pain", 0.0)
            disp_absorbed_healer += result.get("absorbed_by_healer", 0.0)

            # Rage from taking physical hits — models Revenge procs and hit-based rage.
            if (
                not result["was_avoided"]
                and not result["was_reflected"]
                and event.school == "physical"
            ):
                rage_on_hit = (
                    event.raw_amount / state.max_hp * c["rage"]["hit_rage_per_max_hp_fraction"]
                )
                state.rage = min(state.rage_max, state.rage + rage_on_hit)

            state.hp -= damage
            damage_timeline.append((now, damage))

            # Reactive healing — healer burst-heals on spike damage
            if (
                healing_profile.reactive_threshold_hp_pct > 0
                and now >= reactive_ready_at
                and state.hp > 0
                and state.hp / state.max_hp < healing_profile.reactive_threshold_hp_pct
            ):
                # Budget charged at the nominal (pre-heal_mult) rate — see the
                # baseline-heal comment above; same reasoning applies to the
                # reactive burst.
                nominal_burst = state.max_hp * healing_profile.reactive_burst_pct_of_max_hp
                if _healer_budget_enabled:
                    nominal_burst = min(nominal_burst, healer_budget)
                burst = nominal_burst * heal_mult
                actual = min(state.max_hp - state.hp, burst)
                state.hp = min(state.max_hp, state.hp + burst)
                heal_timeline.append((now, actual))
                disp_healed_external += actual
                # The cooldown gates the healer's ATTEMPT to intervene, not
                # whether the bank could fully fund it — a budget-starved
                # burst still consumes the cooldown (scaled down, not free
                # to retry sooner), same as a real healer who tried and had
                # less to give.
                reactive_ready_at = now + healing_profile.reactive_cooldown_s
                if _healer_budget_enabled:
                    # Charge for the effective (landed) portion — see the
                    # baseline-heal comment above for why gross-charging a
                    # net-measured budget is wrong.
                    healer_budget -= actual / heal_mult

            # Ardent Defender cheat-death: SimC gates this on the BUFF being
            # active (`buffs.ardent_defender->check()`), not on cooldown
            # readiness — a lethal hit only gets saved if the player already
            # pressed AD proactively and is still inside its 12s window.
            # The previous `now >= state.ardent_defender_cd_until` check got
            # this backwards: cd_until starts at -1.0 (always "ready") before
            # the first ever press, so it granted a free save to the very
            # first lethal hit of the fight even with zero button presses,
            # then blocked EVERY save for the next 90s after any real press
            # (cd_until = press_time + 90, while the buff itself only lasts
            # 12s) — the opposite of SimC's rule in both directions.
            # Validator-confirmed bug (2026-07-03), fixed as a Top-5 #3
            # prerequisite (2026-07-06 retrospective) since it systematically
            # under-counts ProtPal deaths ahead of surfacing death_rate.
            if (
                state.hp <= 0
                and not died
                and character.class_spec == "protection_paladin"
                and now < state.ardent_defender_until
            ):
                ad_cfg = c["specs"]["protection_paladin"]
                state.hp = state.max_hp * ad_cfg["ardent_defender_cheat_death_heal_pct"]
                # The save consumes the buff — "this effect ends" per the
                # real tooltip. cd_until is untouched: the 90s cooldown
                # already started at press time (protection_paladin.py),
                # not at the moment the save happens to land.
                state.ardent_defender_until = now

            # Nadir, recorded after any cheat-death save but before the
            # cosmetic death-reset below (which would mask the real low
            # point at 1.0/max_hp). Divides by max_hp *now* since Last
            # Stand can change it mid-fight (see line ~200).
            min_hp_seen = min(min_hp_seen, max(0.0, state.hp) / state.max_hp)

            if state.hp <= 0 and not died:
                died = True
                time_to_die = now
                state.hp = 1.0  # cosmetic — keep simulation running for window stats

        iteration_results.append(
            IterationResult(
                died=died,
                time_to_die_s=time_to_die,
                final_hp_pct=max(0.0, state.hp) / state.max_hp,
                min_hp_pct=min_hp_seen,
                raw_damage_total=raw_total,
                dealt_damage_total=dealt_total,
                healing_total=sum(h for _, h in heal_timeline),
                damage_timeline=damage_timeline,
                heal_timeline=heal_timeline,
                shield_block_uptime_pct=sb_active_time / duration_s if duration_s > 0 else 0.0,
                sb_rage_starved_s=sb_rage_starved_s,
                sb_charge_limited_s=sb_charge_limited_s,
                disposition_instrumented=_is_prot_warrior,
                disposition_avoided_raw=disp_avoided_raw,
                disposition_blocked_cut=disp_blocked_cut,
                disposition_armor_cut=disp_armor_cut,
                disposition_vers_cut=disp_vers_cut,
                disposition_dr_layers_cut=disp_dr_layers_cut,
                disposition_absorbed_ip=disp_absorbed_ip,
                disposition_absorbed_healer=disp_absorbed_healer,
                disposition_healed_self=disp_healed_self,
                disposition_healed_external=disp_healed_external,
            )
        )

        # Convergence check: stop early when mean-DTPS relative std-err is small.
        if (
            target_error > 0
            and len(iteration_results) >= min_iterations
            and len(iteration_results) % convergence_check_every == 0
        ):
            dtps_arr = np.array([r.dealt_damage_total / duration_s for r in iteration_results])
            mean_dtps = float(dtps_arr.mean())
            if mean_dtps > 0:
                stderr = float(dtps_arr.std(ddof=1)) / np.sqrt(len(dtps_arr))
                if stderr / mean_dtps < target_error:
                    break

    aggregated = _aggregate(
        iteration_results,
        character.max_hp(),
        duration_s,
        compute_metrics=compute_metrics,
        compute_tmi_metrics=compute_tmi_metrics,
    )
    if keep_iteration_results:
        aggregated.iteration_results = iteration_results
    return aggregated


def _aggregate(
    results: list[IterationResult],
    max_hp: float,
    duration_s: float,
    compute_metrics: bool = True,
    compute_tmi_metrics: bool = True,
) -> SimResult:
    deaths = sum(1 for r in results if r.died)
    death_rate = deaths / len(results) if results else 0.0
    death_times = [r.time_to_die_s for r in results if r.died and r.time_to_die_s is not None]
    dtps_values = np.array([r.dealt_damage_total / duration_s for r in results])
    sb_uptime_values = np.array([r.shield_block_uptime_pct for r in results])
    sb_rage_starved_pct = np.array(
        [r.sb_rage_starved_s / duration_s if duration_s > 0 else 0.0 for r in results]
    )
    sb_charge_limited_pct = np.array(
        [r.sb_charge_limited_s / duration_s if duration_s > 0 else 0.0 for r in results]
    )
    # p5 (not p1): p1 degenerates to 0 whenever death_rate >= 1% (a worse
    # duplicate of death_rate) and is only ~20 samples deep at the default
    # 2000 iterations, before early-stop convergence can shrink N further.
    # calibration-scientist, 2026-07-06.
    min_hp_values = np.array([r.min_hp_pct for r in results])
    p5_min_hp_pct = float(np.percentile(min_hp_values, 5)) if len(min_hp_values) else None

    disposition = _aggregate_disposition(results)

    if not compute_metrics:
        return SimResult(
            iterations=len(results),
            deaths=deaths,
            death_rate=death_rate,
            death_times=death_times,
            mean_dtps=float(dtps_values.mean()),
            p50_dtps=float(np.percentile(dtps_values, 50)),
            p99_dtps=float(np.percentile(dtps_values, 99)),
            mean_5s_window=0.0,
            p95_5s_window=0.0,
            p99_5s_window=0.0,
            p99_10s_window=0.0,
            p99_15s_window=0.0,
            tmi_6=0.0,
            etmi_6=0.0,
            tmi_12=0.0,
            etmi_12=0.0,
            mean_sb_uptime=float(sb_uptime_values.mean()),
            mean_sb_rage_starved_pct=float(sb_rage_starved_pct.mean()),
            mean_sb_charge_limited_pct=float(sb_charge_limited_pct.mean()),
            sample_max_hp=max_hp,
            sample_duration_s=duration_s,
            sample_damage_timeline=[],
            sample_heal_timeline=[],
            sample_died=False,
            sample_time_to_die_s=None,
            p5_min_hp_pct=p5_min_hp_pct,
            **disposition,
        )

    w5 = np.array([compute_window_max(r.damage_timeline, 5.0, duration_s) for r in results])
    w10 = np.array([compute_window_max(r.damage_timeline, 10.0, duration_s) for r in results])
    w15 = np.array([compute_window_max(r.damage_timeline, 15.0, duration_s) for r in results])

    if compute_tmi_metrics:
        tmi_6 = compute_tmi(
            results, max_hp, duration_s, window_s=SRD_DEFAULT_WINDOW_S, include_externals=False
        )
        etmi_6 = compute_tmi(
            results, max_hp, duration_s, window_s=SRD_DEFAULT_WINDOW_S, include_externals=True
        )
        tmi_12 = compute_tmi(
            results, max_hp, duration_s, window_s=SRD_M_PLUS_WINDOW_S, include_externals=False
        )
        etmi_12 = compute_tmi(
            results, max_hp, duration_s, window_s=SRD_M_PLUS_WINDOW_S, include_externals=True
        )
    else:
        tmi_6 = etmi_6 = tmi_12 = etmi_12 = 0.0

    # Pick the worst-spike iteration as the visualization sample — what tanks
    # actually need to see is when they almost died, not the median run.
    sample_idx = int(np.argmax(w5)) if results else 0
    sample = results[sample_idx] if results else None

    # Phase 6.2 / 6.3 — HRPS + Normalized Tank Score. HRPS is the net
    # damage-after-self-sustain per second the healer must supply.
    # `healing_total` on IterationResult already excludes IP absorbs
    # from the engine's self-sustain accounting; the timeline sums work
    # the same way.
    from .normalized_score import compute_hrps, compute_normalized_tank_score

    hrps_values = np.array(
        [compute_hrps(r.dealt_damage_total, r.healing_total, duration_s) for r in results]
    )
    mean_hrps = float(hrps_values.mean()) if len(hrps_values) else 0.0
    normalized_tank_score = compute_normalized_tank_score(
        death_rate=death_rate,
        mean_hrps=mean_hrps,
        mean_dtps=float(dtps_values.mean()),
    )

    return SimResult(
        iterations=len(results),
        deaths=deaths,
        death_rate=death_rate,
        death_times=death_times,
        mean_dtps=float(dtps_values.mean()),
        p50_dtps=float(np.percentile(dtps_values, 50)),
        p99_dtps=float(np.percentile(dtps_values, 99)),
        mean_5s_window=float(w5.mean()),
        p95_5s_window=float(np.percentile(w5, 95)),
        p99_5s_window=float(np.percentile(w5, 99)),
        p99_10s_window=float(np.percentile(w10, 99)),
        p99_15s_window=float(np.percentile(w15, 99)),
        tmi_6=tmi_6,
        etmi_6=etmi_6,
        tmi_12=tmi_12,
        etmi_12=etmi_12,
        mean_sb_uptime=float(sb_uptime_values.mean()),
        mean_sb_rage_starved_pct=float(sb_rage_starved_pct.mean()),
        mean_sb_charge_limited_pct=float(sb_charge_limited_pct.mean()),
        sample_max_hp=max_hp,
        sample_duration_s=duration_s,
        sample_damage_timeline=sample.damage_timeline if sample else [],
        sample_heal_timeline=sample.heal_timeline if sample else [],
        sample_died=sample.died if sample else False,
        sample_time_to_die_s=sample.time_to_die_s if sample else None,
        constants_version=int(load_constants().get("constants_version", 0)),
        mean_hrps=mean_hrps,
        normalized_tank_score=normalized_tank_score,
        p5_min_hp_pct=p5_min_hp_pct,
        **disposition,
    )


def _aggregate_disposition(results: list[IterationResult]) -> dict:
    """Aggregate per-iteration disposition sums into `SimResult`'s mean
    shares. Returns a plain dict of kwargs (`**disposition` at both
    `SimResult(...)` call sites) so the two return paths above — one of
    which skips the heavier window/TMI math — share this one computation
    rather than duplicating it.

    Shares are computed as (sum of the bucket across every iteration) ÷
    (sum of raw damage across every iteration) rather than averaging each
    iteration's own ratio — the two are equivalent in expectation, but
    the sum-of-sums form guarantees the 8 chain shares add to exactly
    1.0 (mirroring the per-event invariant averaged across iterations)
    instead of drifting from float rounding on a per-iteration divide.
    """
    instrumented = bool(results) and results[0].disposition_instrumented
    total_raw = sum(r.raw_damage_total for r in results)
    total_dealt = sum(r.dealt_damage_total for r in results)

    def _share(total_bucket: float, denom: float) -> float:
        return total_bucket / denom if denom > 0 else 0.0

    healed_self_share = _share(sum(r.disposition_healed_self for r in results), total_dealt)
    healed_external_share = _share(sum(r.disposition_healed_external for r in results), total_dealt)

    return {
        "disposition_instrumented": instrumented,
        "disposition_avoided_share": _share(
            sum(r.disposition_avoided_raw for r in results), total_raw
        ),
        "disposition_blocked_share": _share(
            sum(r.disposition_blocked_cut for r in results), total_raw
        ),
        "disposition_armor_share": _share(sum(r.disposition_armor_cut for r in results), total_raw),
        "disposition_vers_share": _share(sum(r.disposition_vers_cut for r in results), total_raw),
        "disposition_dr_layers_share": _share(
            sum(r.disposition_dr_layers_cut for r in results), total_raw
        ),
        "disposition_absorbed_ip_share": _share(
            sum(r.disposition_absorbed_ip for r in results), total_raw
        ),
        "disposition_absorbed_healer_share": _share(
            sum(r.disposition_absorbed_healer for r in results), total_raw
        ),
        "disposition_dealt_share": _share(total_dealt, total_raw),
        # Defensive clamp — see SimResult.disposition_healed_self_share's
        # docstring for why this ratio isn't a strict conservation bound.
        "disposition_healed_self_share": min(1.0, max(0.0, healed_self_share)),
        "disposition_healed_external_share": min(1.0, max(0.0, healed_external_share)),
    }
