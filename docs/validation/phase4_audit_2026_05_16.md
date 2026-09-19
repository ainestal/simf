# Phase 4 engine math audit — 2026-05-16

Validator Mode A. Audit of commits `7a3da15` (Blood DK), `dba5730` (VDH),
`1e1ef25` (Brewmaster + Guardian). Reference chain: Prot Warrior
`src/simf/core/mitigation.py:86-260`. K=2700, all four new specs marked
`calibrated: false`.

## Headline — none of the four is shippable as-is

Two structural bugs run through the work, plus one math bug in Brewmaster.
Each chain looks correct read in isolation (which is why all 343 unit
tests pass), but the runtime wiring is broken:

### Bug 1 — Spec policy dispatch is silently missing (CRITICAL)

`src/simf/core/policy.py:7-13` only special-cases `protection_paladin`.
Every other spec — including all four new ones — falls through to
`ActiveMitigationPolicy`, the Warrior policy.

```
$ python -c 'from simf.core.policy import make_policy; ...'
blood_death_knight     -> ActiveMitigationPolicy
vengeance_demon_hunter -> ActiveMitigationPolicy
brewmaster_monk        -> ActiveMitigationPolicy
guardian_druid         -> ActiveMitigationPolicy
protection_paladin     -> ProtPalPolicy
```

Consequence: **none of `BloodDKPolicy`, `VengeanceDHPolicy`,
`BrewmasterPolicy`, `GuardianPolicy` is ever invoked by `run_simulation`.**
Death Strike never heals. Soul Cleave never heals. Purifying Brew never
clears the stagger pool. Tooth-and-Claw heal never fires. Vampiric Blood
/ Metamorphosis / Incarnation never trigger from `decide()`. Bone Shield
averaged DR still applies (it's in the chain, not the policy), so
mitigation reads partly correct, but every spec-specific *active heal or
emergency CD* is dead code.

**Test gap that masked it:** `tests/test_blood_dk.py:165-176` and
`tests/test_vengeance_dh.py:117-133` instantiate the policy class directly
(`BloodDKPolicy(char).decide(...)`) and assert it heals. They never check
that `make_policy(blood_dk_character)` returns `BloodDKPolicy`. A one-line
integration test would have caught this in either commit.

### Bug 2 — State-slot reuse cross-contaminates the chain (HIGH)

Because `ActiveMitigationPolicy` runs on every spec, the Warrior policy
sets `state.shield_wall_until` at HP<40%, `state.last_stand_*` at HP<25%,
and `state.bsv_active_until` when BSV procs (if talent set). The new
chains then **re-read those slots and apply their own DR constants:**

- Guardian Druid: Warrior's Shield Wall trigger at HP<40% is read as
  Survival Instincts — a **50% DR** (`survival_instincts_dr: 0.50`),
  which is stronger than Warrior's actual SW (40%). Guardian gets an
  accidentally-better emergency button on Warrior policy presses.
- Blood DK: reads SW slot as Icebound Fortitude (30%). Close enough to
  SW that the bug is hidden but still wrong semantically.
- Brewmaster: reads SW slot as Fortifying Brew (20% DR), under-tuned vs
  the real Warrior SW.
- Vengeance DH: reads `bsv_active_until` as Demon Spikes. If the spec
  runs with `brutoh-actual` talents (BSV included), every BSV proc
  silently activates Demon Spikes (+20% phys DR + 15% parry).

The flip side: the new specs *don't* read Warrior-only slots that are
genuinely active (Indomitable 4% DR, IP absorbs from Brutal Vitality).
So the cross-contamination is asymmetric, not a clean overlay.

The "deliberate slot reuse" pattern from the commit messages is sound in
principle (state structure stays simple), but it requires the policy
dispatch fix first. Until then, the slots leak.

### Bug 3 — Brewmaster `_drain_stagger` never reduces HP (CRITICAL, Brewmaster only)

`src/simf/classes/brewmaster_monk.py:24-40`. The drain function pops
damage out of the stagger pool and appends it to
`state.recent_damage_window`:

```python
state.stagger_pool -= drain
state.recent_damage_window.append((now, drain))
```

But the runner only deducts HP via `state.hp -= damage` where `damage` is
the chain `result["dealt"]`. Drained stagger never reaches `state.hp`.
Net effect: **40% of physical damage and 10% of magic damage simply
vanish.** This is the primary reason Brewmaster shows 0% deaths even at
+20 Fortified (table below). The unit test
`test_brewmaster_stagger_pool_grows_on_synthetic_hit` validates that the
pool grows, not that it ever causes harm.

Secondary math issue at the same site: `drain = pool × dt / 10` is
exponential decay, not linear-over-10s. With `dt=1.0` it drains 10% of
the pool per tick; the pool half-lifes in ~7s, not 10s. Should be
`drain = max_pool_at_apply × dt / 10` tracked per-stagger-bucket, or
linear from a recorded peak.

## Death-rate sanity (Brutoh stats swapped onto each spec)

`scripts/phase4_smoke.py`, 500 iterations, m+_pull_caster profile scaled
to +14/+16/+18/+20 Fortified. **All numbers below are with the dispatch
bug active.** They are useful for "is anything obviously broken in
runtime behavior" — they are NOT a calibrated death-rate baseline.

```
=== +14 Fortified ===
spec                         deaths   mean_dtps    p99_5s
protection_warrior            0.0%      70,585     687,363
blood_death_knight            0.0%     107,317     803,575
vengeance_demon_hunter        0.0%     113,451     864,622
brewmaster_monk               0.0%      98,207     725,809
guardian_druid                0.0%      81,302     589,362
protection_paladin            0.0%     107,388     957,841

=== +18 Fortified ===
protection_warrior            0.0%      85,096     875,059
blood_death_knight            0.8%     129,505     981,475
vengeance_demon_hunter       31.6%     138,318   1,086,484
brewmaster_monk               0.0%     117,559     877,702
guardian_druid                0.0%      95,348     679,402
protection_paladin            0.0%     129,379   1,182,166

=== +20 Fortified ===
protection_warrior            0.0%      93,926     991,772
blood_death_knight            7.0%     143,324   1,090,852
vengeance_demon_hunter       63.4%     153,864   1,211,480
brewmaster_monk               0.0%     129,267     970,791
guardian_druid                0.0%     103,898     741,581
protection_paladin            1.0%     143,573   1,310,069
```

Read:

- **VDH at +18/+20 is the standout** — 32% / 63% death rate vs Warrior
  0% on the same gear. Demon Spikes is a 6s window with no auto-press
  policy at runtime, no magic-school DR layer (real VDH has Demonic
  Wards / Aldrachi Design passives), no Soul Cleave heals firing. Even
  after the policy fix, VDH will probably still die more than Warrior
  on this profile because Fiery-Brand-averaged is only 6.8%, no
  party_magic_dr equivalent, no avoidance from Demon-Spikes-up.
- **Brewmaster never dies** — primarily because of Bug 3. Real
  Brewmaster on equivalent gear would die between Warrior and Guardian
  rates. Numbers here are meaningless until the drain fix lands.
- **Guardian never dies** — partly because the Warrior policy press of
  SW gets read as 50% SI, partly because Ironfur is averaged into
  `total_armor()` (`character.py:51-55`) as a baseline boost rather than
  a tied-to-state mechanic, partly because real Frenzied Regen isn't
  modeled. Death rate is suspiciously generous.
- **DTPS shape** — at +20 Brutoh-as-Pal/DK shows 143k mean DTPS while
  Brutoh-as-Warrior shows 94k. That ~50% gap is *expected* given the
  Warrior chain has Phalanx (4% averaged), Indomitable (4%),
  Brace-for-Impact (up to 4%), Fueled-by-Violence (heal), Defensive
  Stance 20% non-physical, and party_magic_dr 5% non-physical that the
  new specs lack. The relative ordering Pal ≈ DK > Brew > VDH ≫ Guardian ≈ Warrior is **inverse-correlated with how many flat-DR
  bolt-ons the chain has**, which is the same systematic bias the K
  calibration would reveal.

## Per-spec breakdown

### Blood Death Knight (`blood_death_knight.py`)

**a) Constants spot-check** (`constants.yaml:298-340`):
- `bone_shield_avg_stacks=7.0` × `dr_per_stack=0.03` → 21% phys DR.
  Tooltip-faithful (3%/stack, max 10, typical M+ rotational uptime is
  6-8). OK as placeholder.
- `death_strike_heal_pct_of_recent_dmg=0.25`, `min_heal_pct=0.07`.
  Matches in-game DS formula (max of 7% max HP or 25% recent-5s damage).
- `vampiric_blood_max_hp_increase=0.35`, `healing_increase=0.30`. Real
  Midnight VB last I checked is +30%/+30%; +35% max HP is **slightly
  generous**. Flag for tooltip re-verification, not a blocker.
- `icebound_fortitude_dr=0.30`, duration 8s, CD 180s. Matches tooltip.
- `mastery_ds_heal_scaling=0.005` — Blood mastery is "Blood Shield" not
  "DS heal amplifier" per se; the heal is the absorb. Modeling it as a
  flat DS-heal multiplier is a reasonable abstraction but not literal.

**b) Chain order** (`blood_death_knight.py:36-119`): avoidance → armor →
vers → Bone Shield (phys) → IF (shield_wall slot) → healer DR → absorb.
Order is consistent with Warrior reference. Missing relative to Warrior:
no demo shout / spell reflect (DK doesn't have direct equivalents —
correct), no Defensive Stance party_magic_dr (could add an AMS averaged
DR layer for non-physical, but `calibrated: false` covers it).

**c) State-slot reuse:** Reuses `shield_wall_until` for IF and
`last_stand_*` for VB. **Cross-contamination via Bug 2** — the Warrior
policy will also press SW/LS on this state, double-mapping the slot.
Verdict: structurally OK once Bug 1 fixed; until then, contaminated.

**d) Replay-mode:** `if event.is_log_replay` branch at line 102 correctly
prefers `log_absorbed` over synthetic blood shield. Verdict: OK.

**e) Death-rate verdict at +14:** 0% — under-tuned by ~10% mean DTPS
relative to Pal/VDH on the same gear, indicating either Bone-Shield is
slightly over-credited or Warrior `ActiveMitigationPolicy`'s Brutal
Vitality absorb (which DOES fire on the DK state because BV is in the
talents list) is leaking ~5-15% absorb in. Without fixing Bug 1, the
+14 number can't be trusted as a baseline.

### Vengeance Demon Hunter (`vengeance_dh.py`)

**a) Constants spot-check** (`constants.yaml:342-374`):
- `demon_spikes_physical_dr=0.20`, `parry_chance_during=0.15`, duration
  6s. Tooltip-correct.
- `metamorphosis_max_hp_increase=0.30`, leech 0.50, duration 15s, CD
  240s. Tooltip-correct.
- `soul_fragments_per_minute=6.0`, `heal_pct_max_hp=0.06`. Active VDH
  generates closer to 10-15 fragments/min in M+ pace (Spirit Bomb /
  Soul Carver / fragment-from-Soul-Cleave). 6 is **low** — flag for
  tuning to 10-12.
- `fiery_brand_target_dr=0.40` × `avg_uptime=0.17` → 6.8% averaged
  across all schools. The "across all schools" is a deliberate
  abstraction (real FB is target-debuff so it should only affect
  damage *from* the branded target, but in multi-mob M+ the averaging
  is defensible).
- `base_dodge_chance=0.08`. Reasonable for VDH baseline.

**b) Chain order** (`vengeance_dh.py:29-101`): avoidance (with Demon
Spikes parry bonus) → armor → vers → Demon Spikes phys DR → Fiery Brand
averaged → healer DR → absorb. Consistent with reference. **Missing
magic-school DR** — real VDH has Demonic Wards / Aldrachi Design passive
~10-15% magic DR. Its absence is consistent with the +20 death-rate
being magic-spike-driven.

**c) State-slot reuse:** `bsv_active_until` reused for Demon Spikes,
`last_stand_*` for Metamorphosis. With Bug 1 present, the Warrior policy
sets `bsv_active_until` from BSV procs (if talent equipped) which the
VDH chain interprets as Demon Spikes — a cross-contamination *helping*
VDH slightly (the +20% phys DR fires on a Warrior trigger). Mostly
neutral effect on the death-rate; not the cause of the 63% deaths.

**d) Replay-mode:** Correct — `if event.is_log_replay` at line 86.

**e) Death-rate at +14:** 0%. At +18: 31.6%. At +20: 63.4%. **Obviously
over-tuned (dies too easily).** Root cause is the policy bug (no Soul
Cleave heal, no Metamorphosis press) and missing magic-school DR. Even
after fixing Bug 1, expect VDH to remain harder-dying than Warrior on
magic-heavy profiles until Aldrachi-Design or equivalent lands.

### Brewmaster Monk (`brewmaster_monk.py`, Phase 4.4 update)

**a) Constants spot-check** (`constants.yaml:234-260`):
- `stagger_pct_physical=0.40`, `stagger_pct_magic=0.10`. Matches in-game.
- `stagger_dot_duration_s=10.0`. Tooltip-correct.
- `ironskin_brew_stagger_increase=0.05`. Real ISB grants ~10% extra
  stagger; 5% is **low** but defensible as average-modeled.
- `purifying_brew_clear_pct=0.50`. Tooltip-correct.
- `purifying_brew_threshold_pct_of_max_hp=0.40`. Reasonable heavy-stagger
  threshold but quite high — real-play purify happens earlier.
- `fortifying_brew_dr=0.20`, duration 15s, CD 420s. **CD is wrong** —
  Fortifying Brew is 360s in Midnight 12.0.5 last I checked. Flag.
- `celestial_brew_absorb_pct_of_max_hp=0.20`, CD 60s. Roughly right.

**b) Chain order** (`brewmaster_monk.py:43-121`): drain → armor → vers
→ Fortifying Brew → stagger fraction to pool → healer absorb. **No
avoidance roll at all** — Brewmasters do dodge in WoW (~5-8% baseline).
The chain skips it entirely. Flag.

**c) State-slot reuse:** `shield_wall_until` for Fortifying Brew, plus
the new `stagger_pool` / `stagger_pool_drain_rate` /
`last_stagger_tick_t` fields are clean (Brewmaster-only). Cross-
contamination via Bug 2 — Warrior policy SW press at HP<40% triggers
the Fortifying-Brew 20% DR.

**d) Replay-mode:** `if event.is_log_replay` branch at line 90 correctly
falls back to the `log_absorbed` path. **BUT the assumption "stagger is
captured in the log's absorbed field" is wrong** — stagger ticks come
through as `SPELL_PERIODIC_DAMAGE` on the Stagger spell ID, not as the
`absorbed` field of the original hit. This will under-count stagger when
replay-validating against real Brewmaster logs. Not a blocker for now
(no Brew logs in `examples/`), but a known issue for log_replay Phase 4
follow-up.

**e) Death-rate at +14:** 0%, and stays 0% all the way to +20.
**Bug 3 (stagger pool drain never hits HP)** is the primary cause. The
mean DTPS line (98k at +14, 129k at +20) still tracks roughly with
other specs because that metric counts the drained ticks via
`recent_damage_window`, but `state.hp` itself never gets touched by the
drain. Until Bug 3 is fixed, every Brewmaster number is misleading.

### Guardian Druid (`guardian_druid.py`, Phase 4.4 update)

**a) Constants spot-check** (`constants.yaml:262-296`):
- `ironfur_armor_multiplier_per_stack=1.0`, `avg_stacks_m_plus=2.0` →
  +200% armor from Ironfur. Real Ironfur is +75% armor/stack
  (Midnight tuning), stacks-up-to-50%-uptime in M+. **+200% is
  over-tuned by ~2-3x.** The Guardian already gets a massive armor
  boost via `character.total_armor()` (`character.py:51-55`). At
  Brutoh's 5015 base armor, Guardian sees 5015 × 3 = 15045 armor →
  ~85% phys DR ceiling. Flag for revisit.
- `barkskin_dr=0.20`, duration 8s, CD 60s → `barkskin_avg_dr=0.027`. The
  math: 0.20 × 8/60 = 0.0267. OK.
- `survival_instincts_dr=0.50`. Tooltip-correct. Active **only when SW
  slot fires** — currently fires when Warrior policy triggers SW at
  HP<40% (Bug 2).
- `rage_of_the_sleeper_avg_dr=0.033`. Math: 0.25 × 8/60 = 0.033. OK.
  Real RotS has additional reflect mechanic not modeled — fine.
- `tooth_and_claw_procs_per_minute=12.0`, `heal_pct_max_hp=0.06`. Real
  T&C in Midnight is closer to 8-10 procs/min (Mangle 35% chance, ~5s
  cycle scaled by haste). 12 is **slightly high** but tolerable.
- `incarnation_max_hp_increase=0.30`, armor mult 1.30, duration 30s, CD
  180s. Matches tooltip. (Real Incarnation also boosts versatility +20%
  — not modeled.)
- `agility_per_dodge_pct=1000.0` — unused in code as far as I can see.
- `base_dodge_chance=0.05` — reasonable.

**b) Chain order** (`guardian_druid.py:23-93`): dodge → armor (with
Incarnation mult) → vers → Barkskin → RotS → SI (shield_wall slot) →
healer DR → absorb. Consistent. **No parry** (correct — Druids don't
parry).

**c) State-slot reuse:** `shield_wall_until` for SI, `last_stand_*` for
Incarnation. With Bug 2 active, Warrior SW press gives Guardian +50% SI
(huge); Warrior LS press inflates max_hp by 30% (legitimately equivalent
to Incarnation HP boost). The Guardian's `incarnation_armor_multiplier`
(line 54-55) reads `last_stand_until` to decide whether to apply, so a
Warrior LS press also activates the +30% armor mult. **This is the most
generous cross-contamination of all four specs.**

**d) Replay-mode:** Correct.

**e) Death-rate at +14:** 0%, +20: 0%. **Obviously under-tuned by
multiple causes:** Ironfur 2x over, Warrior policy triggers SI/Incarn
slots, T&C heal never fires (Bug 1) but it doesn't matter because the
tank isn't taking damage anyway. Until Ironfur multiplier is corrected
and Bug 1 + Bug 2 fixed, these numbers are meaningless.

## Calibration gap analysis

Even with all bugs fixed, the new specs will show systematic offsets
from the Warrior baseline driven by:
1. Missing flat-DR layers the Warrior chain accumulates (Indomitable 4%,
   Phalanx 4% averaged, Defensive-Stance 20% non-physical,
   party_magic_dr 5%).
2. Different active-mitigation cadences (DK runic-power vs Warrior rage)
   that the synthetic Brutal-Vitality absorb model doesn't translate.
3. Spec-specific passive mitigation not in chain (VDH Demonic Wards,
   Guardian Frenzied Regen, Blood DK AMS, Brewmaster Stagger refinement).

These are model imperfection, not model wrongness — fine for **relative**
sim within a spec (e.g. "does swapping mastery for haste increase my DK
DTPS?") once the policy is firing, but unsafe for **cross-spec
comparison** ("am I a tankier VDH or Blood DK?") at any point until each
spec has ≥2 real-log calibrations within ±15% RMSE.

## Overall trust verdict

| spec               | chain math    | constants     | replay-mode | runtime wiring | ship as-is? |
|--------------------|---------------|---------------|-------------|----------------|-------------|
| blood_death_knight | OK            | minor (VB +5pp generous) | OK     | broken (Bug 1+2) | NO         |
| vengeance_dh       | OK in chain   | low fragments/min        | OK     | broken (Bug 1+2) | NO         |
| brewmaster_monk    | broken (Bug 3)| FB CD wrong, no avoidance| limited| broken (Bug 1+2+3) | NO    |
| guardian_druid     | OK in chain   | Ironfur 2-3x over        | OK     | broken (Bug 1+2) | NO         |

**Required to ship Phase 4:**

1. Fix `make_policy` (`src/simf/core/policy.py:7-13`) to dispatch all
   four new specs. One-line add per spec.
2. Add integration test: `make_policy(char).__class__.__name__ ==
   "BloodDKPolicy"` etc. Catches future regressions.
3. Fix `_drain_stagger` in `brewmaster_monk.py` to actually subtract
   drained damage from `state.hp` (or have the runner do it). Convert
   the exponential decay to linear-over-duration while you're there.
4. Either also fix Bug 2 (clear/snapshot the slots on chain entry) or
   accept slot-leak as a "calibrated: false" known issue and document
   it. Cleaner fix: each spec gets its own state slots (`if_until`,
   `vb_until`, `demon_spikes_until`, etc.) with the chain reading only
   its own.
5. Re-run `scripts/phase4_smoke.py` after fixes — VDH at +14 should
   drop near Warrior baseline; Brewmaster should start showing nonzero
   deaths at +18+; Guardian should not be 0% at +20.

**Allowed to ship with caveat (`calibrated: false` covers this):**
- VDH missing Aldrachi Design / Demonic Wards (~10-15% magic DR)
- DK missing Anti-Magic Shell averaged
- Guardian Ironfur multiplier review (currently 2-3x over)
- Brewmaster replay-mode stagger-tick fidelity (no Brew logs yet)
- VB +5pp generous, FB CD wrong by 60s, T&C procs/min slightly high

The new chains will not survive a real-log validation in their current
state. They are not unsafe to land behind the existing `calibrated:
false` UI warning, but the synthetic verdicts they produce are
meaningless for users until at minimum Bug 1, Bug 2, and Bug 3 are
fixed.
