from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .constants import load_constants


def apply_secondary_dr(linear_pct: float, constants: dict[str, Any] | None = None) -> float:
    """Apply Midnight 12.0.1 stat diminishing returns to a linear rating-derived percent.

    Brackets (per Maxroll): linear pct stacks at full efficiency up to 30%, then
    each subsequent bracket applies an efficiency penalty (-10% / -20% / -30% /
    -40% / -50%) until a hard cap (~113.3%). The first 5% base crit (and class
    base mastery) sit *outside* this DR — they're flat baselines, not rating —
    so callers should apply DR only to the rating-derived portion.

    Source: https://maxroll.gg/wow/resources/stat-diminishing-returns
    Configured via `secondary_dr.breakpoints` and `secondary_dr.hard_cap` in
    constants.yaml so future patches can re-tune without code changes.

    Args:
        linear_pct: rating-derived percent BEFORE DR, as a 0-1 fraction.
        constants: pre-loaded constants dict (avoids re-loading in hot paths).
                   Pass None to load on demand.

    Returns:
        effective percent (0-1 fraction), capped at hard_cap.
    """
    if linear_pct <= 0:
        return 0.0
    c = constants if constants is not None else load_constants()
    cfg = c.get("secondary_dr", {})
    breakpoints = cfg.get("breakpoints", [])
    hard_cap = cfg.get("hard_cap", 1.133)
    if not breakpoints:
        # Backwards-compat: no DR config → return linear (the pre-F5 behavior)
        return min(linear_pct, hard_cap)

    effective = 0.0
    remaining = linear_pct
    prev_eff = 0.0
    for break_eff, efficiency in breakpoints:
        if efficiency <= 0:
            # Sentinel / capped bracket — nothing further contributes.
            break
        bracket_size_eff = break_eff - prev_eff
        if bracket_size_eff <= 0:
            continue
        # Linear cost (in rating-equivalent pre-DR pct) to fill this bracket:
        linear_cost = bracket_size_eff / efficiency
        if remaining <= linear_cost:
            effective = prev_eff + remaining * efficiency
            return min(effective, hard_cap)
        remaining -= linear_cost
        prev_eff = break_eff
    return min(prev_eff, hard_cap)


def _composite_block_dr(bonus_block: float, constants: dict[str, Any] | None = None) -> float:
    """Apply F14 block-chance diminishing returns to a bonus-block fraction.

    Direct port of SimC ``player_t::composite_block_dr`` from
    ``engine/player/player.cpp:5467``:

        diminished = bonus / (block_factor × bonus × 100 × vertical_stretch
                              + horizontal_shift)

    ``bonus_block`` is the portion subject to DR — mastery contribution + any
    block rating (zero in Midnight 12.0.5). The flat ``block_chance`` baseline
    is NOT passed through this curve; that addition happens in the caller.

    Args:
        bonus_block: pre-DR bonus block as a 0-1 fraction.
        constants: pre-loaded constants dict; ``None`` reloads on demand.

    Returns:
        post-DR block contribution as a 0-1 fraction. Returns 0 for non-positive
        input (matches SimC's ``if (bonus_block > 0)`` guard).
    """
    if bonus_block <= 0:
        return 0.0
    c = constants if constants is not None else load_constants()
    dr = c["base"]["block_chance_diminishing_returns"]
    denom = (
        dr["block_factor"] * bonus_block * 100 * dr["block_vertical_stretch"]
        + dr["horizontal_shift"]
    )
    return bonus_block / denom


# Numeric stat fields that must never be negative. A negative value here is
# never a legitimate game stat — it signals a sign-flip or bad arithmetic at a
# construction/hydrate site, so Character.__post_init__ raises on it rather than
# letting a nonsense survivability number propagate silently. Zero is permitted:
# a degraded hydrate (item DB unreachable → stat backfilled to 0) is a
# recoverable state, surfaced honestly via validation_issues(), not a crash.
_NON_NEGATIVE_STAT_FIELDS = (
    "stamina",
    "armor_from_gear",
    "strength",
    "agility",
    "haste_rating",
    "crit_rating",
    "mastery_rating",
    "versatility_rating",
    "parry_rating",
    "shield_armor",
)


@dataclass
class Character:
    name: str
    race: str
    class_spec: str
    talents: str

    stamina: int
    armor_from_gear: int

    # Strength and Agility are the two tank primary stats; each spec uses exactly
    # ONE. Both default 0 so a char_data built for the OTHER primary loads without
    # the missing key crashing ``from_dict``: Guardian / Brewmaster / VDH set
    # agility and leave strength 0; Prot Warrior / Prot Pal / Blood DK set
    # strength and leave agility 0. This matters because the Raider.IO / online
    # hydrate drops zero-valued stats (``and val``), so an agility tank's
    # char_data legitimately omits ``strength`` — it MUST have a default or a
    # Druid looked up by name 500s the Gear surface. Guardian's ``base_dodge``
    # adds agility-derived dodge on top of the flat baseline when agility is
    # populated; strength-based tanks ignore agility entirely (and vice-versa).
    strength: int = 0
    agility: int = 0

    haste_rating: int = 0
    crit_rating: int = 0
    mastery_rating: int = 0
    versatility_rating: int = 0
    parry_rating: int = 0

    # Shield armor for the equipped off-hand item — feeds the SimC block-value
    # formula `block_value = shield.armor × 2.5` (engine/player/player.cpp:1681).
    # Zero for specs that don't equip a shield (Brewmaster, Guardian, VDH).
    # Optional with default 0 so existing demo YAMLs and tests load without
    # changes; the F12 path keys off character spec + non-zero value.
    shield_armor: int = 0

    max_hp_override: int | None = None

    # True when ``stamina`` is the OUT-OF-FORM (caster) value, so Guardian Bear
    # Form's +40% max HP must be applied in max_hp(). Set ONLY by the SimC-paste
    # path, whose gear stamina is form-independent (caster). The log/WCL hydrate
    # paths store the in-form COMBATANT_INFO stamina and leave this False — their
    # `stamina × hp_per_stam` is already the bear HP, so no ×1.40 (no double-count).
    # No-op for non-Guardians. See max_hp() and io/character_from_combatant_info.
    stamina_in_caster_form: bool = False

    # Set by calibrate-k from COMBATANT_INFO talent block to auto-detect armor-
    # multiplier talents (reinforced_plates, armor_specialization) instead of
    # relying on the YAML-specified talent loadout.
    detected_talent_spell_ids: frozenset[int] | None = None

    # BUFF aura spell_ids observed active on this tank in a replay log. Distinct
    # from detected_talent_spell_ids (which carries COMBATANT_INFO trait-node-
    # ENTRY ids, NOT spell ids) — the hero-talent mitigation ledger gates on the
    # talent's BUFF spell_id, which only appears here. Brewmaster only today
    # (e.g. Shado-Pan Predictive Training 451230). See _always_on_dr().
    active_buff_spell_ids: frozenset[int] | None = None

    # Per-run talent-set override (optimizer / diff-card paths). When set, it is
    # authoritative for EVERY talent-derived effect — max_hp(), total_armor(),
    # _always_on_dr() — not just the runtime mitigation branches keyed off
    # MitigationState.talents. run_simulation(talent_set_override=...) rebinds
    # the character with this field so the A/B arms differ in armor/HP talents
    # (armor_specialization, reinforced_plates, indomitable), which would
    # otherwise be invisible. Defaults None → every non-override path
    # (calibration, hydrate, normal sims) is bit-identical.
    talent_set_override: frozenset[str] | None = None

    # A player's REAL selected talents, decoded from a source that doesn't
    # need a hand-curated named loadout to guess from — currently only the
    # COMBATANT_INFO entry-id path (io/talent_decoder.decode_combatant_info_
    # entry_ids), which is validated against real log data. Distinct from
    # talent_set_override (an explicit hypothetical, always wins) and from
    # the `talents` YAML-loadout-name guess (the pre-existing fallback,
    # lowest precedence — see _talent_set()). None when no real build was
    # decoded (e.g. every SimC-paste-loaded character today — the talent-
    # string decode isn't reliable yet, see docs/validation/
    # talent_string_decoder_2026_07_13.md), so those paths are unaffected.
    decoded_talents: frozenset[str] | None = None

    def __post_init__(self) -> None:
        # Hard invariant: a negative stat is never a legitimate game value — it
        # signals a sign-flip or bad arithmetic at a construction/hydrate site.
        # Fail loudly here rather than letting a nonsense survivability number
        # propagate silently. Zero is permitted (a degraded hydrate is a
        # recoverable state, flagged via validation_issues()); only negatives
        # raise. Runs after __init__ for every construction path, incl.
        # from_dict() and dataclasses.replace().
        for field_name in _NON_NEGATIVE_STAT_FIELDS:
            value = getattr(self, field_name)
            if value < 0:
                raise ValueError(
                    f"Character {self.name!r}: {field_name} must be >= 0, got {value!r} "
                    "— a negative stat signals a sign-flip or bad arithmetic upstream."
                )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Character:
        allowed = cls.__dataclass_fields__
        return cls(**{k: v for k, v in data.items() if k in allowed})

    def validation_issues(self) -> list[str]:
        """Structural problems that make this character's survivability numbers
        untrustworthy — typically a degraded hydrate where the item DB was
        unreachable and a required stat was backfilled to 0 (the public
        online-name-lookup path; see io/raider_io.py and the #231 fix).

        Returns human-readable issue strings; an empty list means the required
        stats resolved and the model can produce trustworthy numbers. This is
        the SINGLE definition of "did this character's stats resolve?" — UI
        banners (ui/app.py::_stats_unresolved) and recommenders consult it
        instead of each re-deriving the test, so a new import path that forgets
        to populate a field is caught everywhere at once.

        Does NOT raise — a degraded character is a recoverable state to surface
        honestly. The non-negative invariant is enforced in __post_init__.
        """
        issues: list[str] = []
        if self.stamina <= 0:
            issues.append(
                "stamina did not resolve (0) — max HP and every eHP-derived number is garbage"
            )
        # Every tank spec uses exactly one primary (Strength or Agility): a
        # legit Guardian has strength 0 (Agi primary) and a warrior agility 0,
        # so only BOTH being absent means no gear stats resolved at all.
        if self.strength <= 0 and self.agility <= 0:
            issues.append("no primary stat (strength/agility) resolved")
        return issues

    def is_degraded(self) -> bool:
        """True when validation_issues() is non-empty — the survivability model
        cannot produce trustworthy numbers for this character (a degraded
        hydrate). Surfaces should show an honest "couldn't read your gear"
        banner instead of fabricated eHP / verdict / upgrade numbers."""
        return bool(self.validation_issues())

    def max_hp(self) -> float:
        c = load_constants()
        if self.max_hp_override is not None:
            base = float(self.max_hp_override)
        else:
            base = self.stamina * c["stat_conversion"]["hp_per_stamina"]
            # Bear Form's +40% stamina → +40% max HP — but ONLY when the stored
            # stamina is the out-of-form (caster) value (stamina_in_caster_form,
            # set by the SimC-paste path). The log/WCL hydrate paths store the
            # already-in-form COMBATANT_INFO stamina, so applying ×1.40 there would
            # double-count; they leave the flag False. NOT applied to a
            # max_hp_override (already the in-form sheet value).
            if self.class_spec == "guardian_druid" and self.stamina_in_caster_form:
                base *= c["specs"]["guardian_druid"]["bear_form_stamina_multiplier"]

        if self.race == "tauren":
            base *= c["racials"]["tauren_endurance_hp"]
        if "indomitable" in self._talent_set():
            base *= 1 + c["talents"]["indomitable"]["max_hp_increase"]
        return base

    def total_armor(self) -> float:
        c = load_constants()
        armor = float(self.armor_from_gear)
        if self.race == "earthen":
            armor *= c["racials"]["earthen_titan_wrought_frame"]
        if self.class_spec == "protection_warrior":
            # Vanguard — a Prot Warrior SPEC PASSIVE (find_specialization_spell,
            # always active, NOT a talent — same class of ability as Riposte
            # above base_parry()): adds bonus armor = Strength × 70%. Sourced
            # three ways, all agreeing: (1) live Wowhead tooltip for spell 71
            # (raw accessibility-tree read via Playwright, not an AI-summarized
            # fetch — "Apply Aura: Mod Armor From Stat %", Effect #1, Value:
            # 70%; plain-text tooltip "your Armor is increased by 70% of your
            # Strength"); (2) SimC source, same `midnight` commit this project
            # already cites elsewhere (`fd60a6384dcd55f3737f18f31755141c1fe1e540`,
            # `engine/class_modules/sc_warrior.cpp`,
            # `warrior_t::composite_bonus_armor()`):
            # `ba += spec.vanguard->effectN(1).percent() * current_str;`,
            # gated only on `specialization() == WARRIOR_PROTECTION` — the
            # identical Effect #1 Wowhead names. `current_str` is the
            # character's total effective Strength (base × multipliers, no
            # combat-rating conversion — Strength is a primary stat, applied
            # directly). Added BEFORE the Reinforced Plates / Armor
            # Specialization multiplicative bump below, matching SimC's real
            # `composite_armor()` ordering (`composite_bonus_armor()` is added
            # before `composite_armor_multiplier()` —
            # docs/simc-reference/composite_armor.cpp) so those talents also
            # apply to Vanguard's armor, same as in SimC.
            # This RESOLVES the stale "Vanguard ~= strength*0.258 vs spell
            # value 40%" comment that used to sit near the talents block below
            # — that comment cited the wrong effect (Effect #2 is Vanguard's
            # OTHER, unrelated +40% Stamina effect, not armor) and an
            # empirical fit measured under a since-fixed replay chain (three
            # real mitigation bugs fixed earlier the same day — see
            # docs/validation/protwarrior_vanguard_strength_armor_2026_07_21.md).
            armor += self.strength * c["specs"]["protection_warrior"]["vanguard_armor_per_strength"]
        # Talent-derived armor multipliers (Prot Warrior: Reinforced Plates +5%,
        # Armor Specialization +6%). Stack multiplicatively per SimC source
        # (sc_warrior.cpp:composite_armor_multiplier).
        # When detected_talent_spell_ids is set (from COMBATANT_INFO in calibrate-k),
        # match by spell_id instead of the YAML loadout name.
        if self.class_spec == "protection_warrior":
            # An explicit talent_set_override is authoritative — it represents a
            # hypothetical "what if I ran this build", so it wins over the gear
            # log's detected ids. Without an override, the detected ids (from
            # COMBATANT_INFO) stay authoritative — bit-identical to before.
            applied_detected = False
            if self.detected_talent_spell_ids is not None and self.talent_set_override is None:
                for t_cfg in c.get("talents", {}).values():
                    mult = t_cfg.get("armor_multiplier")
                    sid = t_cfg.get("spell_id")
                    if mult and sid and sid in self.detected_talent_spell_ids:
                        armor *= 1 + mult
                        applied_detected = True
            if not applied_detected:
                # Fall back to the YAML talent loadout whenever the detected-id
                # match applied nothing. Today the match can NEVER succeed on a
                # real log: COMBATANT_INFO's talent block carries trait-node-
                # ENTRY ids (~80k-137k observed across every ACL log in
                # examples/, e.g. WoWCombatLog-051026_073906.txt and
                # examples/bruttah-prot/WoWCombatLog-070326_074225.txt — zero
                # intersection with any modelled spell_id), NOT spell ids —
                # the same mismatch constants.yaml's Brewmaster ledger note and
                # this class's own `active_buff_spell_ids` comment document.
                # Before this fallback, a zero-match detection attempt silently
                # suppressed the YAML loadout's armor credit entirely (always
                # ×1.0). If a real trait-entry-id mapping ever lands and the
                # loop above matches, the detected signal stays authoritative
                # and this fallback is skipped — never double-applied.
                talent_set = self._talent_set()
                for talent_name in talent_set:
                    t_cfg = c.get("talents", {}).get(talent_name, {})
                    mult = t_cfg.get("armor_multiplier")
                    if mult:
                        armor *= 1 + mult
        if self.class_spec == "guardian_druid":
            spec_cfg = c["specs"]["guardian_druid"]
            # Bear Form (5487): +220% Mod Base Resistance (Physical) — a PERMANENT
            # base_armor_multiplier on the gear/caster armor the COMBATANT_INFO
            # snapshot carries (the snapshot is out-of-form, so the ×3.2 is not
            # already baked in). SimC composite_armor applies it before bonus
            # armor; Ironfur is then a FLAT bonus-armor add (below).
            armor *= spec_cfg["bear_form_armor_multiplier"]
            # Ironfur (192081): X% of Agility as FLAT bonus armor per stack, added
            # on top of Bear Form armor (SimC composite_armor adds it OUTSIDE the
            # armor multiplier). avg stacks is haste-scaled for an Elune's Chosen
            # Guardian — see _guardian_ironfur_avg_stacks().
            armor += (
                self.agility
                * spec_cfg["ironfur_flat_armor_per_agility_per_stack"]
                * self._guardian_ironfur_avg_stacks()
            )
        return armor

    def _guardian_ironfur_avg_stacks(self) -> float:
        """Average Ironfur stacks feeding total_armor() — haste-scaled for an
        Elune's Chosen Guardian, static otherwise.

        Default = the static M+ average (``ironfur_avg_stacks_m_plus``). For an
        Elune's Chosen Guardian — detected via the Fury of Elune BUFF aura
        (202770) in ``active_buff_spell_ids``; Druid of the Claw lacks it — the
        value scales with haste: Ironfur is hard rage-capped in M+ (~1:5
        fail:cast) and ~94% of Guardian rage is haste-scaled (auto-attack +
        DoT-tick + haste-reduced-CD ability sources; there is NO rage-from-damage
        in 12.0.x), so more haste → more rage → more Ironfur uptime/stacks.

        Modeled as a first-order uptime-elasticity around AnonGuardian1's measured
        operating point: ``stacks = base × (1 + k·(haste% − ref)/ref)``, true-
        clamped to ``[0, ironfur_stacks_max]``. ``k`` ≈ haste-scaled-rage-fraction
        (0.945) × concave rage→stacks transmission (0.8) ≈ 0.75. The slope is
        UN-REGRESSABLE (AnonGuardian1's haste is gear-locked) so this is a MODELED,
        single-profile estimate and ``calibrated`` stays false. Every other spec
        and every non-Elune's-Chosen Guardian returns the static value
        (haste-independent → bit-identical). See
        docs/validation/phase4_guardian_haste_model_2026_06_24.md.
        """
        c = load_constants()
        spec_cfg = c["specs"]["guardian_druid"]
        base = spec_cfg["ironfur_avg_stacks_m_plus"]
        model = spec_cfg.get("ironfur_haste_model")
        if not model:
            return base
        gate = model.get("detect_buff_spell_id")
        active = self.active_buff_spell_ids or frozenset()
        if gate is None or gate not in active:
            return base  # not Elune's Chosen (or no replay buff signal) → static
        ref = model.get("ref_haste_pct", 0.0)
        if ref <= 0:
            return base
        raw = base * (1 + model["k"] * (self.haste_pct() - ref) / ref)
        return min(max(raw, 0.0), spec_cfg.get("ironfur_stacks_max", base))

    def haste_pct(self) -> float:
        c = load_constants()
        linear = self.haste_rating / c["stat_conversion"]["haste_rating_per_pct"] / 100
        return apply_secondary_dr(linear, c)

    def crit_pct(self) -> float:
        c = load_constants()
        from_rating_linear = self.crit_rating / c["stat_conversion"]["crit_rating_per_pct"] / 100
        # DR applies only to rating-derived percent; the flat base 5% sits
        # below DR (it's a class baseline, not rating).
        return c["base"]["crit_pct"] + apply_secondary_dr(from_rating_linear, c)

    def mastery_pct(self) -> float:
        c = load_constants()
        if self.class_spec == "protection_paladin":
            spec_cfg = c["specs"]["protection_paladin"]
            base = spec_cfg["base_mastery_pct"]
            linear = (
                self.mastery_rating
                / c["stat_conversion"]["mastery_rating_per_pct_paladin_prot"]
                / 100
            )
        elif self.class_spec == "guardian_druid":
            # Mastery: Nature's Guardian. Previously fell through to the warrior
            # branch (wrong base + conversion); now its own. Consumed by
            # incoming_healing_multiplier() (NG's healing/absorb-received aura).
            # base_mastery_pct + the rating conversion are MEDIUM confidence
            # (pending an in-game tooltip) → guardian_druid.calibrated stays false.
            spec_cfg = c["specs"]["guardian_druid"]
            base = spec_cfg["base_mastery_pct"]
            linear = self.mastery_rating / spec_cfg["mastery_rating_per_pct_guardian"] / 100
        else:
            base = c["base"]["mastery_pct_warrior_prot"]
            linear = (
                self.mastery_rating
                / c["stat_conversion"]["mastery_rating_per_pct_warrior_prot"]
                / 100
            )
        return base + apply_secondary_dr(linear, c)

    def incoming_healing_multiplier(self) -> float:
        """Multiplier on non-percent healing + absorbs RECEIVED by the tank.

        Models Guardian "Mastery: Nature's Guardian" (155783) healing-taken aura.
        SimC realizes it as spell 227034 — a proc that fires on every non-percent
        heal received and heals for ``received_heal × mastery_value`` (sc_druid.cpp
        midnight 12463-12492) — i.e. effectively ``1 + mastery`` on healing
        received. simf models the same effect as this multiplier, applied at every
        healer-stream site (runner.py baseline/external/reactive; guardian_druid.py
        Tooth & Claw). ``natures_guardian_heal_coeff`` is 1.0 — SimC heals 227034
        for ``received_heal × mastery_value`` (full coeff, not 0.7), so the
        multiplier is ``1 + mastery_value ≈ 1.147`` at AnonGuardian1's 668 mastery (NOT the
        overheal-inflated whole-fight ×1.226 effective ratio — see the constants.yaml
        note + the 2026-06-27 validation doc). Applied to the OFFERED healing stream,
        which the runner then clamps to missing HP.

        NOTE the 227034 proc EXCLUDES percent-of-max-HP heals, so Frenzied
        Regeneration is mastery-NEUTRAL and is NOT routed through this multiplier
        (see GuardianPolicy.decide). NG's max-HP aura is likewise NOT applied here
        or in max_hp() — simf's calibrated stamina→HP already reproduces the in-game
        sheet HP (always-on-NG-inclusive), so an explicit term would double-count.
        Returns 1.0 for every non-Guardian spec → heal/absorb sites bit-identical.
        """
        if self.class_spec != "guardian_druid":
            return 1.0
        c = load_constants()
        coeff = c["specs"]["guardian_druid"]["natures_guardian_heal_coeff"]
        return 1.0 + coeff * self.mastery_pct()

    def versatility_pct(self) -> float:
        c = load_constants()
        from_rating_linear = (
            self.versatility_rating / c["stat_conversion"]["versatility_rating_per_pct"] / 100
        )
        pct = apply_secondary_dr(from_rating_linear, c)
        if self.race == "kul_tiran":
            pct += c["racials"]["kul_tiran_brush_it_off_vers"]
        return pct

    def versatility_dr(self) -> float:
        return self.versatility_pct() * 0.5

    def base_dodge(self) -> float:
        c = load_constants()
        if self.class_spec == "vengeance_demon_hunter":
            return c["specs"]["vengeance_demon_hunter"]["base_dodge_chance"]
        if self.class_spec == "guardian_druid":
            # Guardian dodge = flat baseline + agility/agility_per_dodge_pct/100.
            # The YAML constant ``agility_per_dodge_pct: 1000`` means 1000 agility
            # contributes +1pp dodge; the /100 converts the result to a 0-1
            # fraction. Zero agility → flat baseline only (matches every
            # plate-tank YAML that doesn't set the field). Surfaced by the
            # AnonGuardian3 smoke test 2026-05-23 — pre-this-PR the
            # ``agility_per_dodge_pct`` constant was dead code.
            spec_cfg = c["specs"]["guardian_druid"]
            from_agility = self.agility / spec_cfg["agility_per_dodge_pct"] / 100
            return spec_cfg["base_dodge_chance"] + from_agility
        return c["base"]["dodge_chance"]

    def base_parry(self) -> float:
        if self.class_spec not in (
            "protection_warrior",
            "protection_paladin",
            "blood_death_knight",
            "vengeance_demon_hunter",
        ):
            return 0.0
        c = load_constants()
        parry_rating_per_pct = c["stat_conversion"].get("parry_rating_per_pct", 100)
        from_strength = self.strength / c["stat_conversion"]["parry_per_strength"] / 100
        from_rating = self.parry_rating / parry_rating_per_pct / 100
        from_riposte = 0.0
        if self.class_spec == "protection_warrior":
            # Riposte — a Prot Warrior SPEC ABILITY (always active, not a
            # talent) — converts critical strike rating 1:1 into the same
            # parry-rating pool. Confirmed via two live in-game tooltip
            # readings, 2026-07-12 (490 crit -> 9.42% parry, 870 crit ->
            # 16.73% parry; both reproduce exactly at parry_rating_per_pct=52).
            # Warrior-specific: do not extend to Paladin/DK/VDH without
            # independently confirming each has an equivalent mechanic. See
            # docs/validation/protwarrior_riposte_crit_parry_gap_2026_07_12.md.
            from_riposte = self.crit_rating / parry_rating_per_pct / 100
        if self.class_spec == "blood_death_knight":
            # DKs have no shield → higher base parry to compensate.
            return (
                c["specs"]["blood_death_knight"]["base_parry_chance"] + from_strength + from_rating
            )
        if self.class_spec == "vengeance_demon_hunter":
            # VDH has no shield but uses agility-based parry; strength scaling
            # still approximates (Brutoh-style stat conventions).
            return (
                c["specs"]["vengeance_demon_hunter"]["base_parry_chance"]
                + from_strength
                + from_rating
            )
        return c["base"]["parry_chance"] + from_strength + from_rating + from_riposte

    def base_block(self) -> float:
        c = load_constants()
        if self.class_spec == "protection_warrior":
            # SimC `composite_block` (sc_warrior.cpp:8893):
            #   block_subject_to_dr = cache.mastery() × effectN(2).mastery_value()
            #   return current.block + composite_block_dr( block_subject_to_dr )
            # The base block (`block_chance` baseline) is NOT subject to DR.
            # Only the mastery contribution (and block rating, which is zero
            # in Midnight 12.0.5) flows through the DR curve. F14 closes the
            # SimC audit chain (player.cpp:5467 — see comment block in
            # constants.yaml::base.block_chance_diminishing_returns).
            bonus = self.mastery_pct() * c["base"]["mastery_block_chance_scaling"]
            return c["base"]["block_chance"] + _composite_block_dr(bonus, c)
        if self.class_spec == "protection_paladin":
            return c["specs"]["protection_paladin"]["base_block_chance"]
        return 0.0

    @property
    def uses_shield(self) -> bool:
        """Whether this spec wields a shield in the off-hand — the only two
        specs with a nonzero ``base_block``. Used by the gear recommender to
        exclude two-handed weapons from main-hand suggestions: equipping one
        would silently unequip the shield, a loss the per-slot ΔeHP compare
        can't see."""
        return self.class_spec in ("protection_warrior", "protection_paladin")

    def critical_block_chance(self) -> float:
        # SimC `block_chance` roll (sc_warrior.cpp:8990):
        #   crit_block_chance = cache.mastery() × effectN(1).mastery_value()
        # effectN(1).mastery_value() = 1.5 per spell 76857.
        c = load_constants()
        return self.mastery_pct() * c["base"]["mastery_crit_block_scaling"]

    def block_value_rating(self) -> float:
        """Block value in armor-equivalent rating units (F2 + F12).

        SimC engine/player/player.cpp:1681:

            base.block_value = items[SLOT_OFF_HAND].stats.armor * 2.5
                               if dbc_inventory_type == INVTYPE_SHIELD else 0

        This flat block_value is fed into `calculate_armor_resist` with
        multiplier=1.0 (regular block) or 2.0 (crit block), then clamped
        at MAX_ARMOR_DAMAGE_REDUCTION (0.85). See core/mitigation.py:210.

        Mastery does NOT contribute to block value in SimC — it affects
        block chance and crit-block chance instead (mastery_block_chance
        and mastery_crit_block scalings). The pre-F12 `+ mastery × 0.5`
        term was an empirically load-bearing fudge that masked the
        missing shield-armor contribution; F12 sources it properly.

        Returns 0 when no shield is equipped (Brewmaster fist weapons,
        Vengeance warglaives, non-shield off-hands) — block_dr is then
        0% even if the block roll succeeds.
        """
        c = load_constants()
        return self.shield_armor * c["base"]["block_value_armor_multiplier"]

    def armor_dr(self) -> float:
        c = load_constants()
        armor = self.total_armor()
        K = c["armor"]["k_constant"]
        return min(armor / (armor + K), c["armor"]["max_armor_dr"])

    def _always_on_dr(self) -> float:
        """Combined damage taken multiplier from always-on spec passives + talents.
        Returns the *multiplier* (e.g. 0.85 means 15% reduction). Includes
        Defensive Stance (Prot Warrior, all schools), Indomitable, etc. — the
        passives that apply to every event regardless of school or button-press.
        """
        c = load_constants()
        mult = 1.0
        if self.class_spec == "protection_warrior":
            spec_cfg = c.get("specs", {}).get("protection_warrior", {})
            ds = spec_cfg.get("defensive_stance_dr", 0.0)
            # Unyielding Stance folds additively into Defensive Stance's own
            # Effect 1 magnitude (see mitigation.py's Defensive Stance
            # section) — must match here too, or the eHP headline and the
            # Monte Carlo timeline disagree for any build carrying it.
            if "unyielding_stance" in self._talent_set():
                us_cfg = c.get("talents", {}).get("unyielding_stance", {})
                ds += us_cfg.get("defensive_stance_dr_increase", 0.0)
            mult *= 1 - ds
            # Indomitable talent: 4% all-damage DR if taken.
            if "indomitable" in self._talent_set():
                indom_dr = c.get("talents", {}).get("indomitable", {}).get("damage_reduction", 0.0)
                mult *= 1 - indom_dr
        elif self.class_spec == "brewmaster_monk":
            mult *= self._mitigation_ledger_dr()
        elif self.class_spec == "guardian_druid":
            # Baseline flat all-school DR the Guardian path previously lacked —
            # the Defensive-Stance / Demonic-Wards analog. Thick Hide (16931, 4%,
            # all schools) × Bear Form's all-school DR (Ursine Adept effectN(2),
            # SimC sc_druid.cpp:14599; ~3%, DBC magnitude medium-confidence)
            # ≈ 6.9% all-school. Bear Form's Glistening-Fur arcane −6% IS applied
            # (school-aware, in classes/guardian_druid.py); its non-arcane −3%
            # sibling (effect #13) is a documented unmodeled gap — see that file
            # + docs/validation/phase4_guardian_magic_mit_2026_06_29.md (2026-06-30).
            spec_cfg = c.get("specs", {}).get("guardian_druid", {})
            mult *= 1 - spec_cfg.get("thick_hide_dr", 0.0)
            mult *= 1 - spec_cfg.get("bear_form_passive_dr", 0.0)
        return mult

    def _mitigation_ledger_dr(self) -> float:
        """All-school DR multiplier from the spec's hero-talent mitigation ledger.

        Each ledger row is a flat / averaged-uptime damage-taken reduction that
        only applies when the talent is detected active — gated on the talent's
        BUFF spell_id observed in `active_buff_spell_ids` (a replay signal;
        COMBATANT_INFO carries trait-node-entry ids, not spell ids, so the buff
        aura is the reliable gate). Returns 1.0 when nothing is detected, which
        keeps every build that lacks the talent bit-identical to pre-ledger
        behaviour. Only `school: all` rows are applied here (school-agnostic);
        school-specific rows are a follow-up (they need an event-aware path).

        See docs/validation/phase4_brewmaster_magic_layers_2026_06_07.md.
        """
        active = self.active_buff_spell_ids
        if not active:
            return 1.0
        c = load_constants()
        spec_cfg = c.get("specs", {}).get(self.class_spec, {})
        mult = 1.0
        for layer in spec_cfg.get("mitigation_ledger", []):
            if layer.get("school") != "all":
                continue
            buff_id = layer.get("detect_buff_spell_id")
            if buff_id and buff_id in active:
                mult *= 1 - layer.get("dr_pct", 0.0) * layer.get("avg_uptime", 1.0)
        return mult

    def effective_hp_physical(self) -> float:
        """Raw physical damage needed to one-shot this character.

        Includes armor DR, versatility DR, and always-on passives (Defensive
        Stance 15% all-schools, Indomitable 4% all-damage). Excludes
        button-press CDs (Shield Block, Shield Wall) and per-event rolls
        (block, dodge, parry) — those don't apply to a single one-shot.
        """
        return self.max_hp() / (
            (1 - self.armor_dr()) * (1 - self.versatility_dr()) * self._always_on_dr()
        )

    def effective_hp_magic(self) -> float:
        """Raw magic damage needed to one-shot this character.

        Includes versatility DR + always-on passives (Defensive Stance 15%,
        Indomitable 4%). Excludes school-specific party auras (those vary by
        composition, modelled separately in mitigation chain).
        """
        return self.max_hp() / ((1 - self.versatility_dr()) * self._always_on_dr())

    def _talent_set(self) -> set[str]:
        # Precedence: an explicit per-run override (a hypothetical "what if")
        # wins over everything, since the optimizer / diff-card A/B arms need
        # their own armor/HP talents. Next, a REAL decoded build (currently
        # only from COMBATANT_INFO — see decoded_talents' docstring) beats the
        # YAML loadout-NAME guess, which stays as the last-resort fallback for
        # characters with no decoded source (e.g. SimC-paste-loaded today).
        if self.talent_set_override is not None:
            return set(self.talent_set_override)
        if self.decoded_talents is not None:
            return set(self.decoded_talents)
        c = load_constants()
        return set(c.get("talent_loadouts", {}).get(self.talents, {}).get("talents", []))
