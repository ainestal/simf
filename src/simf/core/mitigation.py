import random
from collections import deque

from .character import Character
from .constants import load_constants
from .events import DamageEvent

MAX_ARMOR_DAMAGE_REDUCTION = 0.85


def calculate_armor_resist(value: float, k: float, multiplier: float = 1.0) -> float:
    """SimC's `util::calculate_armor_resist(value, armor_coeff, multiplier)`.

    Used for armor DR and for block value (F2, F3). For block, `value` is
    block-value-as-armor-rating and `multiplier` is 1.0 for regular block,
    2.0 for crit block. Result is clamped at 85%.

    2026-07-21 fix (docs/validation/protwarrior_shield_block_fix_2026_07_21.md):
    the multiplier is applied to the RESIST FRACTION, not to `value` before
    the division — confirmed against SimC's real source, fetched fresh from
    GitHub (`engine/util/util.cpp`, branch `midnight`, live 2026-07-21):

        double calculate_armor_resist( double armor, double armor_coeff, double multipler )
        {
          double resist = armor / ( armor + armor_coeff );
          resist *= multipler;
          resist = clamp( resist, 0.0, MAX_ARMOR_DAMAGE_REDUCTION );
          return resist;
        }

    The previous implementation computed `(value * multiplier) / (value * multiplier + k)`
    instead — a different curve entirely once multiplier != 1.0 (only the
    crit-block call site, multiplier=2.0, was ever affected; armor DR and
    regular block always pass multiplier=1.0, where the two forms are
    algebraically identical). At Brutoh's calibration block_value_rating
    (~2472 armor-equivalent, K=3430) this under-stated crit-block mitigation
    by ~24pp (0.590 modeled vs the SimC-correct 0.838) — the dominant
    contributor to the "modeled ~49% vs real ~57% block-value magnitude"
    gap the full-chain-wedge investigation surfaced. `test_block_armor_curve.py`'s
    own `test_crit_block_caps_at_max_armor_dr_not_one` had asserted 0.75 while
    its docstring claimed to test the 0.85 cap engaging — a self-contradiction
    that, with this fix, now resolves correctly (0.60 block value × 2 = 1.20,
    clamped to 0.85 — the cap actually engages, matching the test's own name).
    """
    if value <= 0:
        return 0.0
    resist = value / (value + k)
    resist *= multiplier
    return min(max(resist, 0.0), MAX_ARMOR_DAMAGE_REDUCTION)


class MitigationState:
    """Mutable per-iteration state — HP, rage, AM cooldowns, absorbs, talent procs."""

    def __init__(self, character: Character):
        self.character = character
        self.max_hp = character.max_hp()
        self.hp = self.max_hp
        self.rage = 0.0
        self.rage_max = 100.0

        c = load_constants()
        sb_const = c["active_mitigation"]["shield_block"]
        self.sb_charges_ready_at = [0.0] * sb_const["charges"]
        self.shield_block_until = -1.0

        self.demo_shout_until = -1.0
        self.demo_shout_cd_until = -1.0
        self.shield_wall_until = -1.0
        self.shield_wall_cd_until = -1.0
        self.last_stand_until = -1.0
        self.last_stand_cd_until = -1.0
        self.last_stand_base_max_hp = 0.0  # max_hp before LS boost; restored on expiry
        self.spell_reflect_until = -1.0
        self.spell_reflect_cd_until = -1.0

        self.ignore_pain_absorb = 0.0
        # Tracks how much of the CURRENT Ignore Pain shield came cumulatively
        # from Brutal Vitality since the shield last fully depleted. Separate
        # from the shared 30%-of-max-HP cap on the whole shield — Brutal
        # Vitality also has its own 15%-of-max-HP self-cap on what IT
        # specifically contributes (talents.brutal_vitality.cap_pct_of_max_hp,
        # enforced in policy.py). Reset to 0.0 alongside ignore_pain_absorb
        # wherever the shield conceptually restarts from empty.
        self.brutal_vitality_absorb = 0.0
        self.ignore_pain_until = -1.0
        self.healer_absorb = 0.0
        self.healer_absorb_until = -1.0

        self.healer_dr_until = -1.0
        self.healer_dr_amount = 0.0

        self.bsv_active_until = -1.0
        self.bsv_cd_until = -1.0

        self.bfi_stacks = 0
        self.bfi_stacks_max = 4

        # Brewmaster stagger pool (only used when class_spec == brewmaster_monk).
        # Phase 4.4: explicit stagger DoT model so Purifying Brew has real
        # math behind it. Pool is the current undealt stagger damage;
        # drain rate empties it over `stagger_dot_duration_s`. The policy
        # presses Purifying Brew when pool exceeds the heavy-stagger
        # threshold (default 40% max HP).
        self.stagger_pool = 0.0
        self.stagger_pool_drain_rate = 0.0  # damage/sec, updated on add
        self.last_stagger_tick_t = 0.0

        # Protection Paladin state (only used when class_spec == protection_paladin)
        self.holy_power = 0.0
        self.holy_power_max = 5
        # Word of Glory's real funding pool in Midnight is MANA, not Holy
        # Power (log-verified 2026-07-04 — see protection_paladin.py). Starts
        # full; ProtPalPolicy.tick() regenerates it, decide() spends it.
        self.mana = float(c["specs"]["protection_paladin"]["mana_max"])
        self.sotr_until = -1.0
        self.ardent_defender_until = -1.0
        self.ardent_defender_cd_until = -1.0
        self.sentinel_absorb = 0.0
        self.last_sentinel_tick = 0.0

        # Rolling 5s damage-taken window. `add_recent_damage()` is the only
        # supported writer — it maintains the `_sum` invariant so reads are
        # O(1) instead of resumming on every event. The deque is ordered by
        # time because events are processed in time order; trim pops from
        # the front.
        self.recent_damage_window: deque[tuple[float, float]] = deque()
        self._recent_damage_sum: float = 0.0
        self.talents: set[str] = set()

        # Self-heal accounting (heal_timeline-bound). Policies that heal the
        # tank from its OWN kit (e.g. Guardian Tooth & Claw + Frenzied
        # Regeneration) mutate `self.hp` directly inside tick()/decide(), so the
        # runner — which only records the HEALER model's heals to its local
        # heal_timeline — never sees them. They append `(t, effective_heal)`
        # here instead; the runner drains this into heal_timeline so HRPS /
        # ETMI / Normalized Tank Score credit self-sustain. Empty for every spec
        # whose policy records nothing (warrior path stays bit-identical).
        self.self_heal_events: list[tuple[float, float]] = []

        # Party-realistic magic DR flag — when True, apply averaged party magic-DR
        # in addition to the normal mit chain. Replay sets this True automatically
        # via DamageEvent.is_log_replay; synthetic callers can opt in explicitly.
        self.party_magic_dr_active = False

        # Per-iteration cached character properties. These are pure functions of
        # immutable Character state (Last Stand mutates `self.max_hp`, not char),
        # so they're stable for the lifetime of one MitigationState. Pre-compute
        # here so the hot mitigation chain does ~12 attribute reads per iteration
        # instead of ~6 method-call+lookup chains per event.
        self.cached_versatility_dr = character.versatility_dr()
        self.cached_total_armor = character.total_armor()
        self.cached_base_dodge = character.base_dodge()
        self.cached_base_parry = character.base_parry()
        self.cached_base_block = character.base_block()
        self.cached_block_value_rating = character.block_value_rating()
        self.cached_critical_block_chance = character.critical_block_chance()
        self.cached_haste_pct = character.haste_pct()

    def add_recent_damage(self, t: float, d: float) -> None:
        """Append a damage entry and update the running sum.
        Use this instead of touching `recent_damage_window` directly so the
        cached sum and the window stay in lockstep."""
        self.recent_damage_window.append((t, d))
        self._recent_damage_sum += d

    def apply_self_heal(self, t: float, heal: float) -> float:
        """Apply a self-kit heal, clamp it to missing HP, and record the
        EFFECTIVE (post-clamp) amount to ``self_heal_events`` for
        heal_timeline accounting. Returns the effective heal.

        Use this instead of ``state.hp = min(max_hp, hp + heal)`` for heals
        the tank produces from its own kit (Guardian Tooth & Claw / Frenzied
        Regeneration), so HRPS / ETMI / Normalized Tank Score credit
        self-sustain. The clamp matches the old inline behaviour exactly, so
        the death calc is unchanged — this only adds accounting."""
        if heal <= 0:
            return 0.0
        effective = min(self.max_hp - self.hp, heal)
        if effective <= 0:
            return 0.0
        self.hp += effective
        self.self_heal_events.append((t, effective))
        return effective

    def trim_recent_damage(self, now: float, window: float = 5.0) -> None:
        cutoff = now - window
        # Window is time-ordered (events are processed in time order, and the
        # only writer is `add_recent_damage`). Pop the stale head and decrement.
        while self.recent_damage_window and self.recent_damage_window[0][0] < cutoff:
            _t, d = self.recent_damage_window.popleft()
            self._recent_damage_sum -= d
        # Floating-point error accumulates over thousands of pops; reset to
        # zero whenever the window empties so the cache can't drift forever.
        if not self.recent_damage_window:
            self._recent_damage_sum = 0.0

    def recent_damage_total(self, now: float, window: float = 5.0) -> float:
        self.trim_recent_damage(now, window)
        return self._recent_damage_sum

    def shield_block_charges_available(self, now: float) -> int:
        return sum(1 for t in self.sb_charges_ready_at if t <= now)


def apply_mitigation(state: MitigationState, event: DamageEvent, rng: random.Random) -> dict:
    """Apply the full mitigation chain. Returns a result dict; mutates state."""
    spec = state.character.class_spec
    if spec == "brewmaster_monk":
        from ..classes.brewmaster_monk import apply_brewmaster_mitigation

        return apply_brewmaster_mitigation(state, event, rng)
    if spec == "guardian_druid":
        from ..classes.guardian_druid import apply_guardian_mitigation

        return apply_guardian_mitigation(state, event, rng)
    if spec == "protection_paladin":
        from ..classes.protection_paladin import apply_protection_paladin_mitigation

        return apply_protection_paladin_mitigation(state, event, rng)
    if spec == "blood_death_knight":
        from ..classes.blood_death_knight import apply_blood_dk_mitigation

        return apply_blood_dk_mitigation(state, event, rng)
    if spec == "vengeance_demon_hunter":
        from ..classes.vengeance_dh import apply_vengeance_dh_mitigation

        return apply_vengeance_dh_mitigation(state, event, rng)
    # Default: protection_warrior (existing code below)
    c = load_constants()
    char = state.character
    now = event.time_s
    raw = event.raw_amount

    result = {
        "raw": raw,
        "dealt": 0.0,
        "was_avoided": False,
        "was_reflected": False,
        "was_blocked": False,
        "was_crit_blocked": False,
        "absorbed_by_ignore_pain": 0.0,
        "absorbed_by_healer": 0.0,
        # Disposition ledger (2026-07-09) — read-only snapshots of the SAME
        # `damage *= 1 - x` chain below, so a "where your survivability
        # comes from" panel can report a per-event breakdown without
        # altering a single existing computed number. Every bucket is a
        # (pre-step damage) - (post-step damage) snapshot; see the
        # invariant asserted in tests/test_disposition_ledger_conservation.py:
        #   raw == avoided_raw + blocked_cut + armor_cut + vers_cut
        #        + dr_layers_cut + absorbed_by_ignore_pain
        #        + absorbed_by_healer + dealt
        # Protection Warrior ONLY — every other spec's apply_*_mitigation
        # returns a result dict without these keys; downstream code must
        # treat their absence as "not yet instrumented," not as a real
        # zero measurement.
        "avoided_raw": 0.0,
        "blocked_cut": 0.0,
        "armor_cut": 0.0,
        "vers_cut": 0.0,
        "dr_layers_cut": 0.0,
    }

    # 1. Avoidance (dodge / parry) — only physical attacks
    if (
        event.is_avoidable
        and event.school == "physical"
        and rng.random() < (state.cached_base_dodge + state.cached_base_parry)
    ):
        result["was_avoided"] = True
        result["avoided_raw"] = raw
        return result

    # 2. Spell reflect — caught spells become "avoided" from a damage-taken perspective
    if event.attack_type == "spell" and now < state.spell_reflect_until:
        result["was_avoided"] = True
        result["was_reflected"] = True
        result["avoided_raw"] = raw
        return result

    damage = raw
    # Disposition ledger snapshot — see the result-dict comment above.
    _disp_pre_block = damage

    # 3. Block roll (physical, blockable) — F2/F3: block value goes through
    # the armor curve, crit block uses multiplier=2.0 (clamped at 0.85, not 1.0).
    if event.is_blockable:
        sb_active = now < state.shield_block_until
        block_chance = (
            c["active_mitigation"]["shield_block"]["block_chance_during"]
            if sb_active
            else state.cached_base_block
        )
        if rng.random() < block_chance:
            result["was_blocked"] = True
            multiplier = 1.0
            if rng.random() < state.cached_critical_block_chance:
                result["was_crit_blocked"] = True
                multiplier = 2.0
            K = c["armor"]["k_constant"]
            block_dr = calculate_armor_resist(state.cached_block_value_rating, K, multiplier)
            damage *= 1 - block_dr

    result["blocked_cut"] = _disp_pre_block - damage
    _disp_pre_armor = damage

    # 4. Armor DR (physical only, NOT bleeds — bleeds bypass armor in WoW).
    # ROADMAP 3.9.3: pre-this-fix the engine ran armor DR on every physical
    # event including bleed DOT ticks, over-mitigating bleeds by ~+37pp per
    # the per-school audit (docs/validation/magic_mit_gap_remeasurement_2026_05_23.md).
    if event.school == "physical" and not event.is_bleed:
        K = c["armor"]["k_constant"]
        dr = calculate_armor_resist(state.cached_total_armor, K)
        damage *= 1 - dr

    result["armor_cut"] = _disp_pre_armor - damage
    _disp_pre_vers = damage

    # 5. Versatility (all damage)
    damage *= 1 - state.cached_versatility_dr

    result["vers_cut"] = _disp_pre_vers - damage
    # Lumped ledger bucket for every DR layer from here (5b) through the
    # end of step 8 (healer DR external) — see the result-dict comment
    # near the top of this function for the full invariant this composes
    # into. One bucket, not N, because the DR layers below don't need
    # individual UI attribution today and a single snapshot is cheaper
    # on this hot loop than N pre/post pairs.
    _disp_pre_dr_layers = damage

    # 5b. REMOVED 2026-07-21 (docs/validation/protwarrior_shield_block_fix_2026_07_21.md).
    # Shield Block does NOT carry a separate flat physical-DR aura on top of
    # block chance in Midnight 12.0.5. Confirmed three ways: (1) SimC's real
    # mitigation chain (target_mitigation.cpp / warrior_target_mitigation.cpp,
    # both vendored in docs/simc-reference/) has no such term — Shield Block's
    # entire effect is guaranteeing the block roll, then block value runs
    # through the same calculate_armor_resist curve as any other block; (2) a
    # live Wowhead fetch of spell 132404 (2026-07-21) shows its 4 real effects
    # are "Modify Block % Value: 100%" (block chance), "Damage/Healing Done:
    # 30%" (a Shield Slam damage buff, not damage-TAKEN), an unlabelled/zero
    # "Mod % Damage Taken (All)" slot (every other populated effect on this
    # spell shows an explicit percentage; this one shows none — a vestigial
    # zero-value DBC effect slot, not an active 30% reduction), and a Shield
    # Slam crit-chance buff; (3) an independent validator audit found the real
    # in-game block rate inside vs outside logged Shield Block windows
    # (91.2% vs 25.8%, ratio 0.9635) is inconsistent with a real ~30% layer
    # (which would predict ratio ~0.70) — the corpus's own logs refute it
    # empirically, not just via source-reading. The old `physical_dr: 0.30`
    # was uncited (unlike every neighboring constant in this file) and was
    # masking the calculate_armor_resist multiplier-order bug fixed the same
    # day (see that function's docstring) — the two were coupled: removing
    # this layer ALONE (without the multiplier fix) swings the corpus from
    # +11.0% to +32.4% over-predict. `active_mitigation.shield_block.physical_dr`
    # stays in constants.yaml as a historical/documentation artifact (0.0,
    # comment-only) so any stale reference resolves to a no-op instead of a
    # KeyError; nothing reads it anymore.

    # 5c. Defensive Stance — Prot Warrior baseline 15% damage taken reduction
    # to ALL schools (per spell data 386208 effect 1, base_value=-15%, school
    # mask 127 = all). See docs/simc-reference/AUDIT.md F4 for the source and
    # rationale. Previously simf applied this only to non-physical events with
    # the physical component absorbed into a K=2700 fudge; the structural fix
    # in 2026-05-18 land K=3430 (DBC) + DS to all schools simultaneously.
    if spec == "protection_warrior":
        spec_cfg = c.get("specs", {}).get("protection_warrior", {})
        ds_const = spec_cfg.get("defensive_stance_dr", 0.0)

        # Unyielding Stance — spell 1235047. DBC: A_ADD_FLAT_MODIFIER /
        # SPELLMOD op 3 ("Modifies Effect #1's Value"), value -4 — this is a
        # flat modifier to Defensive Stance's OWN Effect 1 magnitude (-15 →
        # -19 at live), NOT a separate aura. Folds additively into ds_const
        # here, unlike Fight Through Flames below (which activates a
        # DIFFERENT effect slot, Effect 3, and so correctly composes as its
        # own multiplicative layer). All schools — Effect 1 carries school
        # mask 127, same as the base DR it modifies. Live value 0.04 (was
        # previously miscoded onto Demoralizing Shout's DR at the wrong
        # value — see constants.yaml's comment).
        if "unyielding_stance" in state.talents:
            us_cfg = c.get("talents", {}).get("unyielding_stance", {})
            ds_const += us_cfg.get("defensive_stance_dr_increase", 0.0)

        damage *= 1 - ds_const

        # 5c-bis. Fight Through Flames — spell 452494 activates Defensive
        # Stance buff Effect 3 to grant additional magic-only DR (school mask
        # 126 = magic schools). Composes multiplicatively as a separate
        # buff-effect layer on top of Effect 1 (handled above). May 12 2026
        # hotfix lifted the magnitude 4% → 6%. See docs/simc-reference/AUDIT.md
        # F4 for the SimC parse_effects derivation.
        if "fight_through_flames" in state.talents and event.school != "physical":
            ftf_cfg = c.get("talents", {}).get("fight_through_flames", {})
            ftf_dr = ftf_cfg.get("magic_dr", 0.0)
            if ftf_dr:
                damage *= 1 - ftf_dr

        # 5d. Party-aura DR layer — averaged contribution of party auras.
        # Phase 3.9.2 (2026-05-24) generalised the flat `party_magic_dr` knob
        # into a per-school dict so the engine can finally model all-school
        # healer auras (Shaman Earth Shield 4%, Druid Symbol of Hope 5%, etc.)
        # which apply to physical AND magic. The previous magic-only
        # representation left a structural −12.79pp under-mitigation on
        # physical across 18 Brutoh runs — see
        # docs/validation/magic_mit_gap_remeasurement_2026_05_23.md. Only
        # applied when state.party_magic_dr_active is set (replay opt-in;
        # name retained for backwards-compat with the gap-measurement
        # script). Schema: party_dr_by_school: {all: float, magic: float}.
        # `all` applies to every event; `magic` stacks multiplicatively on
        # top for non-physical events.
        if state.party_magic_dr_active:
            party_cfg = spec_cfg.get("party_dr_by_school", {})
            dr_all = party_cfg.get("all", 0.0)
            if dr_all:
                damage *= 1 - dr_all
            if event.school != "physical":
                dr_magic = party_cfg.get("magic", 0.0)
                if dr_magic:
                    damage *= 1 - dr_magic

    # 6. Active mitigation DRs
    # Demo Shout reduces physical attack power only — no effect on magic damage.
    #
    # ATTACKER-SIDE DEBUFF — skip in log replay. Demoralizing Shout is a debuff
    # on the *mob* (it reduces the damage the attacker deals), applied during the
    # attacker's damage calculation BEFORE the hit reaches the tank. WoW's combat
    # log `unmitigatedAmount` (which log_replay feeds as `raw_amount`) is snapshot
    # AFTER attacker-side modifiers but BEFORE the defender's own mitigation — so
    # Demo Shout's −20% is already baked into `raw_amount`. Re-applying it here
    # would double-count it (unlike Defensive Stance, a defender-side reduction
    # that is NOT in `unmitigatedAmount` and so MUST be modeled in replay).
    # Confirmed by per-hit forensics on Brutoh's own corpus: the mitigated
    # fraction r=(amount+absorbed+blocked)/base is ~flat inside vs outside Demo
    # windows (0.730 vs 0.740, 1pp) — if base were pre-Demo it would drop ~20pp.
    # See docs/validation/protwarrior_demo_shout_double_count_2026_07_17.md
    # (and the prior Finding B in phase4_brewmaster_physical_gap_decomposition_2026_07_04.md).
    # In SYNTHETIC mode the damage profile carries raw mob output, so the layer
    # is a legitimate modeled DR and still applies (is_log_replay defaults False).
    ds_cfg = c["active_mitigation"]["demoralizing_shout"]
    ds_immune = event.source_id in ds_cfg.get("immune_sources", [])
    if (
        event.school == "physical"
        and now < state.demo_shout_until
        and not ds_immune
        and not event.is_log_replay
    ):
        damage *= 1 - ds_cfg["damage_taken_reduction"]

    if now < state.shield_wall_until:
        damage *= 1 - c["active_mitigation"]["shield_wall"]["damage_reduction"]

    # Keep Your Feet on the Ground (Mountain Thane hero talent, spell
    # 438591) — flat -8% ALL-school DR while the buff is up. Window-gated
    # like VDH's Painbringer/Metamorphosis: replay events carry the log's
    # real buff windows in event.active_buffs (io.log_replay.load_replay's
    # buff_ability_ids); live/synthetic events carry an empty set, so the
    # live path takes no credit (no Thunder Blast policy/rotation model yet
    # — calibration-path-first staging). See constants.yaml's
    # keep_feet_on_ground_dr comment and
    # docs/validation/protwarrior_magic_wedge_kyfotg_lead_2026_07_22.md.
    warrior_cfg = c["specs"]["protection_warrior"]
    if warrior_cfg["keep_feet_on_ground_spell_id"] in event.active_buffs:
        damage *= 1 - warrior_cfg["keep_feet_on_ground_dr"]

    # 7. Talent passive DRs
    if "indomitable" in state.talents:
        damage *= 1 - c["talents"]["indomitable"]["damage_reduction"]

    if "battle_scarred_veteran" in state.talents and now < state.bsv_active_until:
        damage *= 1 - c["talents"]["battle_scarred_veteran"]["damage_reduction"]

    # Brace for Impact reduces physical damage taken per stack. Bleeds bypass
    # the talent in WoW just as they bypass armor — the buff tooltip explicitly
    # excludes bleed damage. Mirrors the armor-bypass gate at mitigation.py:217
    # (session 22, 2026-05-23). The per-school audit on 2026-05-24
    # (docs/validation/per_school_gap_policy_tick_2026_05_24.md) caught this:
    # adding policy.tick to the audit script ramped BfI stacks to 4 on every
    # event, including bleeds, widening the bleed-bucket engine over-mit by
    # +1.18pp.
    if "brace_for_impact" in state.talents and event.school == "physical" and not event.is_bleed:
        bfi_dr = state.bfi_stacks * c["talents"]["brace_for_impact"]["shield_slam_stack_dr"]
        damage *= 1 - bfi_dr

    # ATTACKER-SIDE DEBUFF — skip in log replay, same reasoning as Demo Shout
    # above. Phalanx marks the *target* (mob) and reduces the damage that mob
    # deals to the tank, so its effect is already inside the log's
    # `unmitigatedAmount` / `raw_amount`. Averaging it back in here (×0.96 on
    # every physical hit at avg_uptime 0.5) double-counts it during replay.
    # Brutoh's ratified 16-log corpus runs the `brutoh-actual` loadout, which
    # includes Phalanx — so this was a live double-count in the flagship
    # calibration, not a theoretical one. Synthetic mode still applies it (the
    # profile carries raw mob output; is_log_replay defaults False).
    if "phalanx" in state.talents and not event.is_log_replay:
        # Phalanx applies a debuff to the target; debuff-immune bosses never
        # receive it, so the player gets no DR from those sources.
        phalanx_cfg = c["talents"]["phalanx"]
        phalanx_immune = event.source_id in phalanx_cfg.get("immune_sources", [])
        if not phalanx_immune:
            # Phalanx uptime is ~50% (TC applies debuff, SS consumes it; real cycle ~6s apply / 12s total).
            avg_uptime = phalanx_cfg.get("avg_uptime", 0.50)
            damage *= 1 - phalanx_cfg["damage_taken_debuff_on_target"] * avg_uptime

    # Racial damage-taken reductions (always-on / averaged)
    if char.race == "highmountain_tauren":
        damage *= 1 - c["racials"]["highmountain_tauren_dr"]
    if char.race == "dwarf" and event.school == "physical":
        # Stoneform 10% physical DR for 8s every 120s → averaged
        avg_dr = c["racials"]["dwarf_stoneform_phys_dr_active"] * (8.0 / 120.0)
        damage *= 1 - avg_dr

    # 8. Healer DR external
    if now < state.healer_dr_until:
        damage *= 1 - state.healer_dr_amount

    result["dr_layers_cut"] = _disp_pre_dr_layers - damage

    # 9. Absorbs
    if event.is_log_replay:
        # Replay mode: use actual per-event absorb from the log (IP + healer shields).
        # This bypasses the sim IP model so absorb doesn't contaminate K calibration.
        absorbed = min(damage, event.log_absorbed)
        damage -= absorbed
        result["absorbed_by_ignore_pain"] = absorbed
    else:
        # Synthetic mode: sim IP model and healer absorb.
        if state.ignore_pain_absorb > 0 and now < state.ignore_pain_until:
            absorbed = min(damage, state.ignore_pain_absorb)
            state.ignore_pain_absorb -= absorbed
            damage -= absorbed
            result["absorbed_by_ignore_pain"] = absorbed
            if state.ignore_pain_absorb <= 1e-6:
                state.ignore_pain_absorb = 0.0
                # Shield fully consumed — Brutal Vitality's cumulative
                # sub-total only makes sense while some shield exists.
                state.brutal_vitality_absorb = 0.0

        if state.healer_absorb > 0 and now < state.healer_absorb_until:
            absorbed = min(damage, state.healer_absorb)
            state.healer_absorb -= absorbed
            damage -= absorbed
            result["absorbed_by_healer"] = absorbed
            if state.healer_absorb <= 1e-6:
                state.healer_absorb = 0.0

    result["dealt"] = damage
    state.add_recent_damage(now, damage)
    return result
