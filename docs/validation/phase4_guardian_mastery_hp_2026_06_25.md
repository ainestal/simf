# Guardian Nature's Guardian mastery → survival — characterization + resolution

Originated 2026-06-25 (characterization). **Resolved + implemented 2026-06-26**
(see the RESOLUTION section at the bottom — the original max-HP proposal was
refuted as a double-count by adversarial verification; the shipped model is the
healing/absorb-received lever instead).

The last unmodeled piece of "finish the Guardian model" (haste ✓ #201, agility ✓
#203, gem suggester ✓ #204/#205, Bear Form +40% HP ✓ #209). **Guardian mastery
was worth nothing to the engine** — this memo characterizes the mechanic and the
resolution wires it as a survival stat.

## The mechanic (Wowhead spell 155783, multi-source confirmed)

**Mastery: Nature's Guardian** applies two always-on auras, each with spell
coefficient **0.7** on the mastery stat:
- `Mod Increase Maximum Health %` — **max HP × (1 + 0.7 × mastery)**
- `Mod Absorb/Healing Taken %` — **healing & absorbs received × (1 + 0.7 × mastery)**
  (plus a separate full-strength +Attack-Power aura, irrelevant to survival).

The 0.7 coefficient is the Wowhead spell-effect "SP mod" and is corroborated by
the 2023-04-10 hotfix that raised it "to 70% (was 50%)"; stable DF → TWW →
Midnight. The max-HP bonus is **0.7 × displayed mastery %**, NOT 1:1 with it (a
common misread — the displayed mastery % is the unfractioned value the +AP aura
tracks). Confirmed by a multi-source research sweep (Wowhead, Warcraft Wiki
patch history, Maxroll DR table, XPOff TWW mastery breakdown).

## ⚠️ KEY FINDING — the max-HP aura is ALREADY captured; an explicit term double-counts

This reverses the original (pre-#209) proposal to add `×(1 + 0.7 × mastery)` to
`Character.max_hp()`. Adversarial verification (research workflow + an independent
arithmetic check) established:

simf's `max_hp() = stamina × hp_per_stamina(22) × racial × bear(1.40)` already
reproduces the in-game **sheet** HP, and the sheet HP is **always-on-NG-inclusive**
(Nature's Guardian is a permanent passive). Decisive arithmetic, AnonGuardian1
log-hydrate path (`WoWCombatLog-062026_163923.txt`, stamina 33,375, Tauren):

| quantity | value |
|---|---|
| modeled `max_hp()` (current, NO NG term) | 770,962 |
| observed in-form (bear) max HP | 782,078 → **+1.44% residual** (snapshot noise) |
| modeled WITH a proposed ×(1 + 0.7·mastery) term @ mastery 668 | 850,187 → **+8.71% OVER** |

The `hp_per_stamina = 22` constant (vs WoW's real ~20) already absorbs ~+10%,
which is essentially Nature's Guardian's +11.6% at their mastery (if real hp/stam
were 20: `33,375 × 20 × 1.05 = 700,875`; implied NG to reach observed = +11.6%).
**So an explicit max-HP term would double-count and make Guardian materially
WORSE than today (+1.4% → +8.7%).** The HP level is left untouched.

## What WAS unmodeled — the healing/absorb-received lever

NG's *second* aura — `Mod Absorb/Healing Taken %` at the same 0.7 coefficient —
scales **all healing and absorbs received** by `1 + 0.7 × mastery`. simf modeled
neither healer throughput nor absorbs scaling with mastery, so Guardian mastery
had **zero survival value** (the closed-form marginal was 0; the sim perturbation
of `mastery_rating` moved nothing because nothing depended on it). **This** is the
genuinely-missing survival lever, and it is what the resolution implements.

## Two limitations (why `calibrated` stays false)

1. **Un-regressable.** AnonGuardian1's mastery is gear-locked to a single value (668), so
   any model validates the *level* at one mastery point, never the *slope* —
   identical to the haste/agility situation.
2. **Conversion constant is medium-confidence.** The 0.7 coefficient is solid, but
   the **Midnight Guardian base mastery %** (~0.08, web-inferred from the 6%/8%
   tooltip; band 0.08–0.086) and the **rating→%** conversion (simf uses its uniform
   `100` secondary anchor, not the literal ~46 lvl-90 figure) need an in-game
   zero-rating tooltip to pin. Magnitude pending that tooltip.

## Grounding against AnonGuardian1's local logs

Hydrated from `examples/anonguardian1-guardian/WoWCombatLog-062026_163923.txt`
(AnonGuardian1-AnonRealm1-EU): stamina 33,375, mastery_rating 668, observed in-form max HP
782,078. The Tauren racial (+5%, already modeled) explains most of the naive
"+6.5%" gap I first read — race-corrected the HP residual is ~+1.4%, too
snapshot-confounded to isolate NG. **The logs cannot validate the slope** — the
magnitude comes from the tooltip + the published 0.7, not the log gap.

> Side-finding (FIXED, PR #208): `hydrate_character` defaulted `race = "human"`,
> dropping Tauren +5% HP. `combat_log.detect_race()` now infers race from racial
> casts (War Stomp 20549 → tauren). Log-hydrated Tauren tanks no longer
> under-predict HP by 5%.

---

## RESOLUTION (2026-06-26) — healing-received lever shipped; HP term refuted

Ratified by the user (AskUserQuestion): model NG's healing/absorb-received aura;
do **not** add a max-HP term. Implementation (constants_version 27 → 28):

1. **`constants.yaml` `specs.guardian_druid`:** `natures_guardian_heal_coeff: 0.70`,
   `base_mastery_pct: 0.08`, `mastery_rating_per_pct_guardian: 100`.
2. **`Character.mastery_pct()`:** new `guardian_druid` branch (base 0.08 + the
   conversion above + secondary DR) — previously fell through to the warrior
   branch (base 0.12, wrong). Warrior/Paladin/DK bit-identical (their consumers —
   block scaling, DS-heal scaling — don't branch Guardian; `base_block()` returns
   0 for Guardian and never calls `mastery_pct()`).
3. **`Character.incoming_healing_multiplier()`** (new): `1 + 0.70 × mastery_pct()`
   for Guardian, **1.0** for every other spec.
4. **`runner.py`:** applies `heal_mult` to `baseline_hps`, external heals, external
   absorbs, and reactive bursts. Every site is `× 1.0` (bit-identical) for
   non-Guardians.
5. **`classes/guardian_druid.py`:** applies it to the Tooth-and-Claw self-heal
   (a self-heal = healing received).
6. **No `max_hp()` change** (avoids the double-count) and **no
   `survivability_weights.py` change** — the sim path already perturbs
   `mastery_rating`, so it picks up the new dependency automatically.

### Validation

- **Mastery is now a valued survival stat.** `compute_survivability_marginals`
  for a AnonGuardian1-like Guardian (mastery 668): `mastery_rating` marginal **p ≈ 196,
  m ≈ 15** (was 0). `heal_mult @ 668 = 1.103` (+10.3% effective healing). The live
  gem suggester + gear surface use this sim path, so they now value mastery.
- **Magnitude caveat:** the healing-received marginal is sensitive to the assumed
  healer throughput in the default profile — in a high-HPS scenario mastery can
  read as comparable to or above agility for survival. Directionally correct (NG
  *is* a strong survival mastery), magnitude pending the tooltip → `calibrated`
  stays **false**.
- **Closed-form fallback** (`marginals.py`) leaves `mastery_rating` at `{p:0, m:0}`
  with a comment: the healing-throughput value has no eHP-denominator closed form,
  so it is sim-path-only (same situation as the agility-dodge component).
- 12 new unit/behavioural tests (`tests/test_guardian_natures_guardian_heal.py`)
  pin the new behaviour + every non-Guardian path's bit-identity.

### Still open (not in this PR)

- The exact `base_mastery_pct` + rating→% for Midnight Guardian (needs an in-game
  zero-rating tooltip; the band is documented above).
- **The lever currently UNDER-states mastery** (dual-validator finding): NG scales
  *all* healing received, but simf models only the healer streams (baseline +
  externals + reactive) and the Tooth-and-Claw self-heal. Two large Guardian
  self-heal streams are unmodeled and so don't yet inherit the multiplier —
  **Frenzied Regeneration (22842)** and the **Nature's Guardian proc self-heal
  (227034)**, together ~25% of AnonGuardian1's received healing in one log. Modeling them
  as Guardian self-heal streams (they'd pick up `incoming_healing_multiplier()`
  for free) is the natural follow-up; until then mastery's true survival value is
  understated, not overstated. A pre-existing gap, not introduced here.

  > **CORRECTION (2026-06-27, see `phase4_guardian_self_heal_mastery_2026_06_27.md`).**
  > A SimC-source audit showed **227034 is the mastery's OWN heal proc**
  > (`received_heal × mastery_value`, sc_druid.cpp:12463-12492) — i.e. the
  > healing-received aura *already* modeled by `incoming_healing_multiplier()`,
  > realized as discrete log events. It is **not** a separate stream; modeling it
  > as one would double-count mastery. And **FrR is a percent-heal that the proc
  > excludes**, so it is mastery-NEUTRAL (modeling it does not raise mastery's
  > value). The genuine understated-mastery issue was the **coefficient**: SimC
  > heals 227034 for `received_heal × mastery_value` (coeff 1.0, not 0.7; raw
  > mastery_value ≈ 0.147 at AnonGuardian1's gear, proc-pairing-confirmed), so
  > `natures_guardian_heal_coeff` was recalibrated 0.70 → 1.0 (×1.103 → ×1.147).
  > (The whole-fight *effective* ratio ×1.226 is overheal-inflated — NOT used.) FrR
  > was added as a separate, mastery-neutral reactive self-heal.
- The mastery survival marginal is **profile-sensitive** (it reads ~2nd-highest in
  the default high-HPS profile — above stamina, behind versatility); the gem
  suggester could over-recommend mastery gems in a high-HPS scenario. Watch when
  promoting toward calibrated.
- `guardian_druid.calibrated` stays **false** (un-regressable single mastery point
  + the magic residual + this medium-confidence conversion).
