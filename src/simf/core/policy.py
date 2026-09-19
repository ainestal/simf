import random

from .character import Character
from .constants import load_constants
from .events import DamageEvent
from .mitigation import MitigationState


def make_policy(
    character: Character,
    tank_dps: float | None = None,
    *,
    skill_modifier: float = 1.0,
    skill_rng: random.Random | None = None,
):
    """Return the right policy for the character's spec.

    Without this dispatch, every non-Warrior spec silently inherited the
    Warrior `ActiveMitigationPolicy` — Death Strike never fired, Soul
    Cleave never healed, Purifying Brew never cleared, Vampiric Blood /
    Metamorphosis / Incarnation never triggered. Validator Phase 4 audit
    (2026-05-16) caught this.

    Phase 2.10 — `skill_modifier` ∈ [0.0, 1.0] gates opportunistic Shield
    Block / Demoralizing Shout presses for `protection_warrior`. 1.0 is
    the default (no change). Other specs ignore the modifier in v1 — they
    aren't calibrated for skill-tier sweeps yet (engine fidelity gap).
    """
    if character.class_spec == "protection_paladin":
        from ..classes.protection_paladin import ProtPalPolicy

        return ProtPalPolicy(character)
    if character.class_spec == "blood_death_knight":
        from ..classes.blood_death_knight import BloodDKPolicy

        return BloodDKPolicy(character)
    if character.class_spec == "vengeance_demon_hunter":
        from ..classes.vengeance_dh import VengeanceDHPolicy

        return VengeanceDHPolicy(character)
    if character.class_spec == "brewmaster_monk":
        from ..classes.brewmaster_monk import BrewmasterPolicy

        return BrewmasterPolicy(character)
    if character.class_spec == "guardian_druid":
        from ..classes.guardian_druid import GuardianPolicy

        return GuardianPolicy(character)
    return ActiveMitigationPolicy(
        character,
        tank_dps=tank_dps,
        skill_modifier=skill_modifier,
        skill_rng=skill_rng,
    )


def _am_reduce(state: MitigationState, now: float, rage_spent: float, c: dict) -> None:
    """Anger Management: reduce all recharging SB charge timers by rage_spent / rage_per_cd_s."""
    reduction = rage_spent / c["talents"]["anger_management"]["rage_per_cd_s"]
    for i, t in enumerate(state.sb_charges_ready_at):
        if t > now:
            state.sb_charges_ready_at[i] = max(now, t - reduction)


# Tank DPS as a fraction of max HP per second. Used to drive Brutal Vitality
# absorb generation. Auto-scales with character to handle stat squishes.
# 5% HP/s is roughly a tank's sustained DPS in M+ at the squished scale.
TANK_DPS_AS_PCT_OF_MAX_HP_PER_S = 0.05


class ActiveMitigationPolicy:
    """Decides when to press AM buttons. Conservative-defensive policy (Kiratank-style)."""

    def __init__(
        self,
        character: Character,
        tank_dps: float | None = None,
        *,
        skill_modifier: float = 1.0,
        skill_rng: random.Random | None = None,
    ):
        self.character = character
        if tank_dps is None:
            tank_dps = character.max_hp() * TANK_DPS_AS_PCT_OF_MAX_HP_PER_S
        # Tank offensive throughput scales with haste (rotation speed) and crit
        # (Revenge crits, etc.). This feeds Brutal Vitality absorb generation,
        # so secondary stats correctly contribute to survivability via that path.
        haste_mult = 1 + character.haste_pct()
        crit_mult = 1 + character.crit_pct() * 0.5
        self.tank_dps = tank_dps * haste_mult * crit_mult
        self.last_tick_t = 0.0
        # Phase 2.10 — skill_modifier gates opportunistic Shield Block + Demo
        # Shout presses. Emergency CDs (Shield Wall, Last Stand) and reactive
        # (Spell Reflect) are deliberately unaffected — those represent
        # presses no engaged tank realistically misses. RNG is consumed ONLY
        # when modifier < 1.0; at 1.0 the policy is bit-identical to pre-v13.
        self.skill_modifier = float(skill_modifier)
        self._skill_rng = skill_rng

        # Phase 2.10d — edge-triggered opportunity sampler. One Bernoulli draw
        # per logical press opportunity, not per `decide()` call. Without this,
        # the per-event sampling collapsed effective press rate to
        # `1 - (1 - m) ^ K` for K events in the ~1.5s pressable window — at
        # K=10 even m=0.40 acts like ~1.00, flattening the ladder. After a
        # missed press, the next opportunity is one natural cycle away
        # (`recharge_s / charges` for SB, `cooldown_s` for DS), so effective
        # press rate ≈ modifier × optimal-rate, which is what the ladder
        # tier semantics imply.
        self._sb_was_pressable: bool = False
        self._sb_skill_roll: bool | None = None
        self._sb_next_opportunity_at: float = 0.0
        self._ds_was_pressable: bool = False
        self._ds_skill_roll: bool | None = None
        self._ds_next_opportunity_at: float = 0.0

        # Phase 2.10f — emergency CD reaction-lag state. SW + LS *always*
        # eventually fire under sustained pressure (no Bernoulli gate), but
        # a distracted tank presses them late. Each HP-threshold cross
        # samples one lag value; the press fires when `now` reaches the
        # scheduled time. Cleared on recovery above the threshold so a
        # flicker doesn't strand a stale schedule (advisor flag,
        # 2026-05-22). Bit-identity at modifier=1.0 — `_roll_reaction_lag()`
        # returns 0.0 without consuming RNG.
        self._sw_pending_press_at: float | None = None
        self._ls_pending_press_at: float | None = None

    def tick(self, state: MitigationState, now: float) -> None:
        c = load_constants()
        dt = max(0.0, now - self.last_tick_t)
        self.last_tick_t = now

        # Passive rage gen scales with haste (auto-attack frequency, Anger Management uptime).
        # Approximation — real haste effect on rage gen is more complex.
        rage_per_second = 5.0 * (1 + state.cached_haste_pct)
        state.rage = min(state.rage_max, state.rage + dt * rage_per_second)

        # Brutal Vitality: % of tank's outgoing damage becomes IP absorb.
        # Two independent caps apply, whichever binds tighter: BV's own
        # 15%-of-max-HP self-cap on what IT has specifically contributed
        # (tracked separately in state.brutal_vitality_absorb, since it
        # resets whenever the shield fully depletes but the shared pool
        # doesn't otherwise distinguish BV credit from direct-cast credit),
        # and the shared 30%-of-max-HP cap on the whole shield.
        if "brutal_vitality" in state.talents:
            damage_done_dt = self.tank_dps * dt
            absorb_gain = (
                damage_done_dt * c["talents"]["brutal_vitality"]["pct_of_damage_dealt_to_ip_absorb"]
            )
            # 1. Clamp to BV's own remaining room under its self-cap first.
            bv_cap = state.max_hp * c["talents"]["brutal_vitality"]["cap_pct_of_max_hp"]
            bv_room = max(0.0, bv_cap - state.brutal_vitality_absorb)
            absorb_gain = min(absorb_gain, bv_room)

            # 2. Apply the (already BV-capped) grant against the shared cap.
            ip_cap = state.max_hp * c["active_mitigation"]["ignore_pain"]["cap_pct_of_max_hp"]
            new_absorb = min(ip_cap, state.ignore_pain_absorb + absorb_gain)
            granted = new_absorb - state.ignore_pain_absorb
            if granted > 0:
                state.ignore_pain_absorb = new_absorb
                # Only the portion that actually made it past BOTH caps
                # counts toward BV's own sub-total — avoids double-counting
                # if the shared cap (not BV's own cap) is what binds.
                state.brutal_vitality_absorb += granted
                state.ignore_pain_until = max(
                    state.ignore_pain_until,
                    now + c["active_mitigation"]["ignore_pain"]["duration_s"],
                )

        # Brace for Impact: 1 stack per Shield Slam cast, capped at 4.
        # SS has a ~6s base CD scaled by haste.
        if "brace_for_impact" in state.talents:
            ss_cycle_s = 6.0 / (1 + state.cached_haste_pct)
            target_stacks = min(state.bfi_stacks_max, int(now / ss_cycle_s))
            state.bfi_stacks = max(state.bfi_stacks, target_stacks)

    def decide(
        self,
        state: MitigationState,
        now: float,
        recent_dtps: float,
        incoming_event: DamageEvent | None = None,
        upcoming_events: list[DamageEvent] | None = None,
    ) -> None:
        # ``upcoming_events`` carries the adaptive-lookahead slice the
        # runner threads in (Phase A 2026-05-23). Phase B's first
        # consumer below — ``demo_shout_precast`` — uses it to pre-cast
        # Demoralizing Shout against a known incoming physical spike.
        # The consumer is toggle-gated (default-OFF) in
        # ``constants.yaml::policy.lookahead_consumers.demo_shout_precast``;
        # with the toggle OFF every pinned seed stays bit-identical.
        c = load_constants()
        sb = c["active_mitigation"]["shield_block"]
        ip = c["active_mitigation"]["ignore_pain"]
        ds = c["active_mitigation"]["demoralizing_shout"]
        sw = c["active_mitigation"]["shield_wall"]
        ls = c["active_mitigation"]["last_stand"]
        spr = c["active_mitigation"]["spell_reflect"]

        # 1. Shield Block — press within one GCD of expiry to maintain coverage.
        # Phase 2.10d — skill gate is edge-triggered per opportunity. A "press
        # opportunity" is the contiguous window where charges + rage + timing
        # allow a press. We roll the skill Bernoulli once per opportunity. If
        # the roll fails, the next chance is one natural SB cycle away
        # (recharge_s / charges) — modelling "you missed this cycle, next
        # one is your next chance," which gives effective press rate ≈
        # modifier × optimal-rate.
        #
        # 2026-07-21 fix (docs/validation/protwarrior_post_shield_block_bias_decomposition_2026_07_21.md):
        # Shield Block's charge recharge is HASTED in the real game — SimC's
        # `shield_block_t` constructor sets `cooldown->hasted = true;`
        # (`engine/class_modules/sc_warrior.cpp`, live-fetched at the same
        # `midnight` commit this project's other SimC citations use,
        # fd60a6384dcd55f3737f18f31755141c1fe1e540). This mirrors the
        # existing hasted-cooldown convention already used two lines above
        # for Shield Slam's cycle (`ss_cycle_s = 6.0 / (1 + haste)`), which
        # this recharge timer had NOT been getting — every charge recharged
        # at a flat, un-hasted 16s regardless of the character's real haste.
        # At Brutoh's calibration haste (23.18%), effective recharge ≈
        # 16/1.2318 ≈ 13.0s, giving a 2-charge steady-state uptime of
        # 6/(13.0/2) ≈ 92% — matching the ratified 16-log corpus's own
        # real logged SB uptime (90.1%, time-weighted) far better than the
        # un-hasted model's ≈59% (measured via a real `run_simulation` call
        # against the same corpus, `SimResult.mean_sb_uptime`). This was the
        # dominant driver of the post-Shield-Block-fix +23.0% over-predict:
        # the sim spent ~41% of each fight believing SB was down when the
        # real player kept it up ~90% of the time, taking un-blocked-value
        # damage during that gap far more often than reality.
        hasted_recharge_s = sb["recharge_s"] / (1 + state.cached_haste_pct)
        # 2026-07-21 investigation (docs/validation/protwarrior_sb_press_cadence_2026_07_21.md):
        # SimC's real default Protection APL presses Shield Block far more
        # eagerly than this — `engine/class_modules/apl/apl_warrior.cpp:372`
        # reads `shield_block,if=buff.shield_block.remains<=10`, which is a
        # near-no-op given the buff's 6s base duration, i.e. SimC presses
        # essentially the instant a charge (+ rage) is available and lets
        # the charge/cooldown economy do all the real throttling — because
        # `buff_t::extend_duration_or_trigger` (`engine/buff/buff.cpp:2101`)
        # ADDS another full `buff_duration()` on top of whatever remains
        # when pressed early, banking ahead rather than wasting the press.
        # Tried mirroring that literally (dropping this `-1.5` gate down to
        # "press whenever charges+rage allow"): it ROBUSTLY REGRESSES this
        # engine's calibration (ratified-corpus RMSE 0.162→0.323,
        # `SimResult.mean_sb_uptime` 69.6%→51.7%) rather than improving it,
        # and a buffer sweep (0.5s/1.5s/3.0s/removed) showed a clean
        # MONOTONIC trend in the OPPOSITE direction — tighter reactive
        # windows IMPROVE uptime, wider or removed ones hurt it. Root cause:
        # `state.shield_block_until = now + sb["duration_s"]` two lines
        # below is an ABSOLUTE RESET, not SimC's additive extension — a
        # press against this model only ever banks up to `duration_s` of
        # coverage total, so pressing while the current buff still has
        # `remaining` seconds left only nets `duration_s - remaining` of
        # real gain (small whenever `remaining` is large). Pressing "ASAP"
        # spends a charge for that small gain and pulls the charge-recharge
        # clock earlier, producing a BIGGER gap later once both charges are
        # spent and recharging. Faithfully capturing SimC's real benefit
        # would need this state machine to model banked/additive remaining
        # duration instead of a reset — a bigger, riskier re-architecture
        # than this investigation's scope (echoing the prior session's own
        # deferral of exactly that). Kept as-is; DO NOT "fix" this toward
        # SimC's literal condition without re-deriving the banking model
        # first — see the validation doc for the full sweep.
        sb_preconditions = (
            now >= state.shield_block_until - 1.5
            and state.shield_block_charges_available(now) > 0
            and state.rage >= sb["rage_cost"]
        )
        new_sb_opportunity = sb_preconditions and (
            not self._sb_was_pressable or now >= self._sb_next_opportunity_at
        )
        if new_sb_opportunity:
            self._sb_skill_roll = self._roll_skill_gate()
            if not self._sb_skill_roll:
                self._sb_next_opportunity_at = now + hasted_recharge_s / sb["charges"]
        self._sb_was_pressable = sb_preconditions
        if sb_preconditions and self._sb_skill_roll:
            for i, t in enumerate(state.sb_charges_ready_at):
                if t <= now:
                    state.sb_charges_ready_at[i] = now + hasted_recharge_s
                    break
            state.shield_block_until = now + sb["duration_s"]
            state.rage -= sb["rage_cost"]
            if "anger_management" in state.talents:
                _am_reduce(state, now, sb["rage_cost"], c)
            self._sb_skill_roll = None
            self._sb_was_pressable = False

        # 2. Ignore Pain — synthetic mode: cast when under sustained damage pressure.
        # In replay mode this is bypassed — log_absorbed handles absorbs per event.
        ip_cap = state.max_hp * ip["cap_pct_of_max_hp"]
        if (
            state.rage >= ip["rage_cost"]
            and state.ignore_pain_absorb < ip_cap * 0.5
            and recent_dtps > ip["synthetic_cast_dtps_threshold"]
        ):
            ip_gain = min(
                state.max_hp * ip["absorb_pct_of_max_hp"], ip_cap - state.ignore_pain_absorb
            )
            if ip_gain > 0:
                state.ignore_pain_absorb += ip_gain
                state.ignore_pain_until = now + ip["duration_s"]
                state.rage -= ip["rage_cost"]
                if "anger_management" in state.talents:
                    _am_reduce(state, now, ip["rage_cost"], c)

        # 3. Demoralizing Shout — fire on CD in replay mode (real log = real CD usage);
        # gate on 250k dealt DTPS in synthetic mode.
        # Phase 2.10d — edge-triggered skill gate. One roll per "DS CD elapsed"
        # event. If missed, next chance is one full DS cooldown later — modelling
        # "you skipped this DS cycle."
        # Phase B 2026-05-23 — adaptive-lookahead consumer adds a third
        # alternative trigger: if the sum of upcoming physical damage in
        # the configured window exceeds the threshold, pre-cast DS so the
        # DR buff covers the spike instead of arriving after. Toggle-gated
        # default-OFF (preserves bit-identity); replay mode is unaffected
        # because ``is_replay_event`` already fires DS unconditionally.
        is_replay_event = incoming_event is not None and incoming_event.is_log_replay
        ds_lookahead_triggers = self._lookahead_demo_shout_precast(c, now, upcoming_events)
        ds_preconditions = now >= state.demo_shout_cd_until and (
            is_replay_event
            or recent_dtps > ds["synthetic_cast_dtps_threshold"]
            or ds_lookahead_triggers
        )
        new_ds_opportunity = ds_preconditions and (
            not self._ds_was_pressable or now >= self._ds_next_opportunity_at
        )
        if new_ds_opportunity:
            self._ds_skill_roll = self._roll_skill_gate()
            if not self._ds_skill_roll:
                self._ds_next_opportunity_at = now + ds["cooldown_s"]
        self._ds_was_pressable = ds_preconditions
        if ds_preconditions and self._ds_skill_roll:
            state.demo_shout_until = now + ds["duration_s"]
            cd = ds["cooldown_s"]
            if "thunderlord" in state.talents:
                cd *= 0.7
            state.demo_shout_cd_until = now + cd
            self._ds_skill_roll = None
            self._ds_was_pressable = False

        # 4. Shield Wall — emergency at <40% HP.
        # Phase 2.10f — reaction-lag skill gate. The threshold crossing
        # schedules a press at `now + lag`; the press fires when the
        # schedule elapses. If HP recovers above the threshold before
        # then, we clear the schedule (player saw the dip pass and didn't
        # commit). At modifier=1.0 the lag is always 0 — fires same frame
        # as pre-2.10f.
        sw_threshold_crossed = state.hp / state.max_hp < 0.40 and now >= state.shield_wall_cd_until
        if sw_threshold_crossed:
            if self._sw_pending_press_at is None:
                self._sw_pending_press_at = now + self._roll_reaction_lag("shield_wall", c)
            if now >= self._sw_pending_press_at:
                state.shield_wall_until = now + sw["duration_s"]
                cd = sw["cooldown_s"]
                if "enduring_defenses" in state.talents:
                    cd *= 1 - c["talents"]["enduring_defenses"]["shield_wall_cdr_pct"]
                state.shield_wall_cd_until = now + cd
                self._sw_pending_press_at = None
        else:
            self._sw_pending_press_at = None

        # 5. Last Stand — last-resort at <25% HP when SW is on cooldown
        # AND SW's DR window has already expired. Reachability fix
        # (2026-05-22): the pre-fix condition `now >= shield_wall_cd_until`
        # only allowed LS when SW was OFF cooldown — which is exactly
        # when LS is NOT needed (SW would have fired one frame earlier
        # at the same HP threshold). The post-fix dual gate:
        #   - `now >= shield_wall_until`     — SW's 8s DR window has ended
        #   - `now < shield_wall_cd_until`   — SW is still on cooldown
        # …captures "SW has been spent and isn't currently mitigating;
        # LS is the next emergency." Without the `>= shield_wall_until`
        # half, LS would fire concurrently with SW on the same decide()
        # call (since SW puts itself on CD that same frame), wasting LS
        # on top of an active SW window.
        ls_threshold_crossed = (
            state.hp / state.max_hp < 0.25
            and now >= state.last_stand_cd_until
            and now >= state.shield_wall_until
            and now < state.shield_wall_cd_until
        )
        if ls_threshold_crossed:
            if self._ls_pending_press_at is None:
                self._ls_pending_press_at = now + self._roll_reaction_lag("last_stand", c)
            if now >= self._ls_pending_press_at:
                state.last_stand_base_max_hp = state.max_hp
                boost = state.max_hp * ls["max_hp_increase"]
                state.hp += boost
                state.max_hp += boost
                state.last_stand_until = now + ls["duration_s"]
                state.last_stand_cd_until = now + ls["cooldown_s"]
                self._ls_pending_press_at = None
        else:
            self._ls_pending_press_at = None

        # 6. Spell Reflect — react to incoming spell tank busters.
        # Phase 2.10f — Bernoulli skill gate. One draw per tank-buster
        # opportunity. At modifier=1.0, short-circuits to True without
        # consuming RNG (bit-identity). Reactive ability — the
        # opportunity is the event itself, not a recurring CD-elapsed
        # tick, so no `_next_opportunity_at` is needed.
        if (
            incoming_event is not None
            and incoming_event.attack_type == "spell"
            and incoming_event.is_tank_buster
            and now >= state.spell_reflect_cd_until
            and self._roll_skill_gate()
        ):
            state.spell_reflect_until = now + spr["duration_s"]
            state.spell_reflect_cd_until = now + spr["cooldown_s"]

        # 7. Battle-Scarred Veteran proc — activates the damage reduction window.
        # Gated by an ICD (~60s) to prevent consecutive procs when HP stays low.
        if "battle_scarred_veteran" in state.talents:
            bsv = c["talents"]["battle_scarred_veteran"]
            if (
                state.hp / state.max_hp < bsv["trigger_hp_pct"]
                and now >= state.bsv_active_until
                and now >= state.bsv_cd_until
            ):
                state.bsv_active_until = now + bsv["duration_s"]
                state.bsv_cd_until = now + bsv["internal_cd_s"]

    def _roll_reaction_lag(self, ability: str, c: dict) -> float:
        """Sample a non-negative reaction lag (seconds) for an emergency CD.

        Phase 2.10f (2026-05-22) — SW and LS *always* fire under sustained
        pressure (an emergency CD that never presses isn't a skill question,
        it's a different game), but a tired / distracted tank presses them
        late. The lag is a half-normal distribution
        `|N(0, sigma * (1 - modifier))|`. At modifier=1.0 the lag is exactly
        0.0 with no RNG consumed — preserves bit-identity with every pinned-
        seed test predating 2.10f.

        Sigma defaults (per `skill_modifier_reaction_lag` in constants):
            modifier=1.00 → sigma=0.0 → lag=0
            modifier=0.85 → sigma=0.3 → mean lag ~0.24s
            modifier=0.65 → sigma=0.7 → mean lag ~0.56s
            modifier=0.40 → sigma=1.2 → mean lag ~0.96s
        """
        if self.skill_modifier >= 1.0:
            return 0.0
        if self._skill_rng is None:
            return 0.0
        cfg = c.get("skill_modifier_reaction_lag", {})
        sigma_base = float(cfg.get(f"{ability}_sigma_at_zero_s", 2.0))
        sigma = sigma_base * (1.0 - self.skill_modifier)
        return abs(self._skill_rng.gauss(0.0, sigma))

    def _roll_skill_gate(self) -> bool:
        """One Bernoulli draw against `skill_modifier`. Called by the per-ability
        opportunity samplers in `decide()` — one draw per logical press
        opportunity, NOT per `decide()` call.

        Phase 2.10d (2026-05-22) — replaced the per-call `_skill_allows_press`
        that sampled every damage event. The old form collapsed effective
        press rate to `1 - (1 - skill_modifier) ^ K` for K events in the
        pressable window; at K=10 even modifier=0.40 acted like ~1.00 and
        the skill ladder spread at +18 was just 3pp (validator flag,
        2026-05-20).

        Bit-identity at modifier=1.0 is preserved by short-circuiting before
        any RNG draw — any consumed randomness would shift downstream
        sequences and break every pinned-seed test (advisor flag,
        2026-05-20)."""
        if self.skill_modifier >= 1.0:
            return True
        if self._skill_rng is None:
            return True
        return self._skill_rng.random() < self.skill_modifier

    def _lookahead_demo_shout_precast(
        self,
        c: dict,
        now: float,
        upcoming_events: list[DamageEvent] | None,
    ) -> bool:
        """Phase B adaptive-lookahead consumer — should Demo Shout pre-cast?

        Returns ``True`` when (a) the toggle is enabled, (b) the runner
        actually threaded a non-empty lookahead slice, and (c) the sum of
        physical damage scheduled within ``upcoming_window_s`` from ``now``
        exceeds ``upcoming_physical_threshold``. The caller OR-s this into
        ``ds_preconditions`` alongside the existing replay / past-burst
        gates so the consumer ADDS firing occasions without replacing
        either.

        Short-circuits to ``False`` if the toggle is OFF, the lookahead
        slice is empty, or no consumer config is present — preserves
        bit-identity at every pinned seed in the shipped default.
        """
        cfg = c.get("policy", {}).get("lookahead_consumers", {}).get("demo_shout_precast", {})
        if not cfg.get("enabled", False):
            return False
        if not upcoming_events:
            return False
        window_s = float(cfg.get("upcoming_window_s", 1.5))
        threshold = float(cfg.get("upcoming_physical_threshold", 375_000.0))
        horizon = now + window_s
        upcoming_phys = sum(
            e.raw_amount for e in upcoming_events if e.school == "physical" and e.time_s <= horizon
        )
        return upcoming_phys > threshold
