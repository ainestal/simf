# simf vs SimC — survivability math audit

**Audit date:** 2026-05-18
**simf state audited:** v0.10.7 (post-Phase 2.7), `mitigation.py` Prot Warrior path, `constants.yaml`, `character.py`
**SimC reference:** branch `midnight`, commit `fd60a63` (2026-05-18), WoW build 12.0.5.67602
**Validation set:** Brutoh's 9 M+ logs — current global RMSE 0.079, but **single-character → high overfit risk**

## Methodology

For each step in simf's `apply_mitigation`, compare to the corresponding SimC call site. Findings classified:

- **STRUCTURAL** — the formula or call order differs. Cannot be fixed by changing a constant.
- **CONSTANT** — formula matches but a numeric value drifts. Tunable.
- **MISSING** — SimC has a term that simf does not model at all.
- **EXTRA** — simf has a term (often a fudge) that SimC does not need.
- **OK** — matches.

Findings are ordered by **expected impact on cross-character generalization** (NOT impact on Brutoh's RMSE alone). A fix can lower Brutoh's RMSE *and* worsen cross-tank accuracy, or vice versa. We optimize for the former.

---

## Findings — ordered by leverage

### F1. Armor K is wrong — **CONSTANT**, biggest single fix

- **simf:** `constants.yaml` `armor.k_constant: 2700`
- **SimC:** `dbc->armor_mitigation_constant(level)` → `expected_stat[90].armor_constant = 3430.0` for level 90 (Midnight). Hardcoded in DBC, NOT a class-by-class value.
- **Math:** Brutoh's 5517 armor → simf gives **67.1%** physical DR; SimC gives **61.7%**. **5.4 percentage points overestimate** of armor DR per event.
- **Why simf fudged it:** comment in `constants.yaml:48-56` explicitly says K=2700 absorbs Defensive Stance's physical component because DS was being applied only to non-physical events (see F4). Two bugs cancel for Brutoh; both fail for other tanks.
- **Fix:** Set `k_constant: 3430`. Add Defensive Stance physical DR back explicitly (F4).
- **Expected effect:** Brutoh's RMSE will jump on physical-heavy dungeons (sim under-mitigates by ~5pp) until F4 is also fixed. Both must land together.

### F2. Block value uses flat % — **STRUCTURAL**

- **simf** `mitigation.py:189-193` + `character.py:149-154`:
  ```python
  block_value = state.cached_block_value_pct  # e.g. 0.30 + mastery_pct * 0.5
  damage *= 1 - block_value
  ```
- **SimC** `player.cpp:8765-8779`:
  ```cpp
  double block_value = s->target_block_value;            // armor-equivalent rating
  double block_resist = util::calculate_armor_resist( block_value, armor_coeff );
  s->result_amount *= 1.0 - block_resist;
  ```
- **Difference:** SimC's block value is *in the same units as armor*, then converted through the armor curve `block_value / (block_value + 3430)`, capped at 0.85. simf treats block as a flat % off raw damage.
- **Impact:** Direction depends on block_value magnitude. For typical Prot Warrior numbers, the structural difference probably *under-mitigates* small blocks and *over-mitigates* large blocks. Magnitude is hard to predict without empirical re-fit; suspect 1-3pp impact per blocked event.
- **Fix:** Re-source block value from spell data (Shield Block 2's effectN, plus mastery-driven block-value rating) and route through `calculate_armor_resist`. This is a non-trivial refactor of `character.py:block_value_pct`.
- **Recommendation:** Defer. Block-event frequency is low (~10% baseline + Shield Block windows), so the per-fight aggregate error is bounded. Address after F1+F4.

### F3. Crit block double-then-clamp-to-1 — **STRUCTURAL**

- **simf** `mitigation.py:190-193`:
  ```python
  if rng.random() < state.cached_critical_block_chance:
      block_value = min(block_value * 2, 1.0)   # cap at 100% block — implies whole-attack-blocked
  ```
- **SimC** `sc_warrior.cpp:9143-9148`:
  ```cpp
  if ( s->block_result == BLOCK_RESULT_CRIT_BLOCKED ) {
      double block_value = s->target_block_value;
      double block_resist = util::calculate_armor_resist( block_value, armor_coeff, 2.0 );
      s->result_amount *= 1.0 - block_resist;
  }
  ```
- **Difference:** SimC applies crit block as the armor curve with `multiplier=2.0` ON TOP of the normal block. Result is still clamped to 0.85 (MAX_ARMOR_DAMAGE_REDUCTION), never to 1.0. simf's `min(x*2, 1.0)` cap allows blocking 100% of an attack when block_value > 0.5, which is wrong even structurally.
- **Impact:** When `block_value_pct >= 0.5`, simf computes full immunity to that attack. SimC tops out at 85% reduction even on crit block. Real impact: low at current gear (block_value ~30-40%), but the bug becomes worse as block scales up.
- **Fix:** Same path as F2 — once block value moves to the armor curve, crit block becomes a separate `calculate_armor_resist(block_value, K, 2.0)` pass with the 0.85 cap.

### F4. Defensive Stance: application scope + magnitude both wrong — **STRUCTURAL + CONSTANT**

- **simf** `mitigation.py:211-215` + `constants.yaml:241`:
  ```python
  if spec == "protection_warrior" and event.school != "physical":
      ds_const = spec_cfg.get("defensive_stance_dr", 0.0)  # 0.20
      damage *= 1 - ds_const
  ```
- **SimC** `sc_warrior.cpp:9220-9224`:
  ```cpp
  parse_effects( buff.battle_stance, effect_mask_t( true ).disable( 4 ) );
  // Stance Mastery for Defensive stance is not working in game as of Dec 04 2025
  parse_effects( buff.defensive_stance, effect_mask_t( true ).disable( 2, 5, 6 ) );
  ```
- **Real behavior (verified against DBC spell data for spell 386208):**
  - **Effect 1 (1020125):** subtype 87 (`A_MOD_DAMAGE_PERCENT_TAKEN`), base_value −15%, school mask 127 (all schools) — **−15% damage taken, all schools**. Enabled for Prot.
  - **Effect 2 (1020126):** subtype 79 (damage done), −10%, all schools — disabled for Prot (no offense penalty).
  - **Effect 3 (1153688):** placeholder, base_value 0%, school mask 126 (magic schools) — gets activated to a non-zero value when "Fight Through the Flames" talent (spell 452494) is taken, granting −6% magic damage taken.
  - **Effects 5/6 (1294015/1294016):** the Stance Mastery chunk-DR ("attack >20% max HP gets −15%") — **disabled in SimC** per the Dec 04 2025 comment because the in-game implementation is broken.
- **Two bugs in simf:**
  1. **Scope**: simf applies only to non-physical (comment says "physical component absorbed into K") — but the in-game aura is school mask 127 = all schools.
  2. **Magnitude**: simf uses 0.20; real spell-data value is **0.15**.
- **Net effect of the two bugs on simf's current outputs:**
  - Magic damage: simf reduces by 20%, real is 15% → simf overestimates magic survivability by ~5pp per magic event. **Consistent with current magic-heavy log residuals (MGT +5.3%, NPX −5.7%, MaiC −8.7%) being closer to zero/positive than physical-heavy logs.**
  - Physical damage: simf reduces by 0% (relies on K=2700 fudge); real is 15% → simf under-mitigates raw, BUT K=2700 vs the true K=3430 inflates armor DR by ~5.4pp, which closely matches the 15% × `(1 - armor_dr)` ≈ 5.8pp expected from the missing DS-physical multiplier. **The two errors are nearly self-canceling for Brutoh's gear pool — this is why RMSE is so low.**
- **Fix:** Set `defensive_stance_dr: 0.15`. Apply to ALL damage events (remove the `event.school != "physical"` guard). Pair with F1 (K=3430). Optionally add "Fight Through the Flames" as a separate talent buff that adds −6% magic DR when on.
- **Magnitude:** With F1+F4 atomic, expect Brutoh's physical-heavy logs to shift ≤ 1pp (the errors were canceling). Magic-heavy logs will see RMSE go DOWN by ~5pp (the over-mitigation correction). **Net RMSE may actually improve, not worsen.** This is good news.

### F5. Uniform `rating_per_pct: 100` — **STRUCTURAL** (DR curve missing)

- **simf** `constants.yaml:58-70`: all secondaries = 100 rating per 1%.
- **SimC:** rating-per-percent is class-specific, level-derived (`current.rating.*` from `dbc->combat_rating_multiplier`), AND subject to DR breakpoints (`def_dr` constants) at ~30/39/47/54/66% with progressive penalties.
- **Per Maxroll Midnight 12.0.1 stat-DR page:** secondaries have a "soft cap" at 30% — every percent above that costs more rating. Hard cap 113.3%.
- **Why this matters for cross-character generalization:** Brutoh likely sits below the DR threshold on every secondary (a +14-18 Prot Warrior typically has ~20-25% on top secondary). For other tanks at higher gear, mastery-stacked Pal or haste-stacked Brewmaster could cross 30%, and simf would over-credit their stat above that point.
- **Why this matters for stat weights:** the displayed "haste vs crit vs vers" trade-offs in `survivability_weights.py` and `dps_stat_weights` use ratings as proxies for percent. Uniform 100 is a self-consistent fudge only if (a) all stats actually have the same rating-per-percent, and (b) no DR. Both are false in real WoW.
- **Fix:** Source real rating-per-percent from DBC (or, pragmatically, from a current Wowpedia/Maxroll table) for level 90. Add a `secondary_dr_curve()` helper that returns effective percent given linear-rating input.
- **Tractability:** Moderate. The "linear-then-broken-piecewise" curve is ~10 lines. Hardest part is sourcing the exact class-by-class rating constants — Wowpedia and class Discords have these for Midnight.

### F6. `hp_per_stamina: 22` is a guess — **CONSTANT**

- **simf** `constants.yaml:63` → `character.py:38`: `base_hp = stamina * 22`.
- **SimC:** Health = `dbc->resource_max_player(RESOURCE_HEALTH, level, character_class) + bonus_health_from_stamina`. At level 90, the stamina-to-HP multiplier is in the per-class scaling table. For Warriors at level 90 the in-game tooltip ratio is *approximately* 20:1 (not 22:1), but tier sets / Indomitable / racials all stack on top.
- **Fix:** Either pull from DBC or accept a small overestimate. Magnitude: ~9% HP error in the worst case. Already mostly nullified by `max_hp_override` which most SimC profiles provide.
- **Recommendation:** Low priority. Most Character objects load from SimC export where max_hp_override is set from the in-game value. Only matters for the SimC-export profiles without `max_hp` (rare).

### F7. Crit-block chance scaling with mastery — **CLOSED 2026-05-20 (atomic with F13)**

- **Resolution:** Flipped 1.0 → 1.5 per SimC `sc_warrior.cpp:8990`
  (`cache.mastery() × effectN(1).mastery_value()`, where
  `effectN(1).mastery_value() = 1.5` per spell 76857). In-game tooltip
  cross-check at 8% mastery: crit-block +12% confirms the 1.5 coefficient.
- **Shipped atomic** with F13 (mastery_block_chance_scaling 0.0 → 0.5).
  The previous 1.0 was empirically load-bearing pre-F12; with F12's
  shield-item block_value sourcing in place, the SimC-truthful 1.5 + F13
  is safe.
- **Ship sweep:** Best K shifted 3200 → **3400**; RMSE at canonical K=3430
  dropped from ~0.079 (post-F12) to **~0.065**. Empirical and canonical
  are now within 30 K of each other, down from a 230 K gap.
- **Full trail:** `docs/validation/k_calibration_mastery_chain_2026_05_20.md`,
  `docs/validation/simc_warrior_mastery_2026_05_19.md`.

### F8. Avoidance is school-gated to physical — **CONSTANT**

- **simf** `mitigation.py:163-169`: dodge/parry rolled only for physical attacks.
- **SimC:** parry only physical (correct); dodge only physical (correct). But there's also a separate **miss** chance against spell hits that simf does not model at all.
- **Magnitude:** Miss is small (base 3-5%) for spells against tanks; impact bounded.
- **Recommendation:** Defer. Already correct on dodge/parry; miss is low-impact.

### F9. Party magic DR is opt-in via env flag — **EXTRA** (fudge layer)

- **simf** `mitigation.py:224-226` + `constants.yaml:252`: applies a flat 5% non-physical DR when `state.party_magic_dr_active`.
- **SimC:** doesn't model party auras unless the user explicitly configures them as enemy debuffs / target buffs. simf's "averaged 5%" is a tunable approximation of unmodeled Ancestral Vigor / Elemental Resistance / etc.
- **Fix:** Keep as-is for now. It's a known fudge with explicit opt-in. Document its existence; don't pretend it's first-principles.

### F10. Phalanx `avg_uptime: 0.50` is a fudge — **CONSTANT**

- **simf** `constants.yaml:152-153`: assumes 50% uptime for the Phalanx mark debuff in M+.
- **SimC:** would simulate the proc cycle (Thunder Clap applies, Shield Slam consumes). Real uptime depends on TC cadence, target lifespan, and number of active mobs — variable.
- **Fix:** Either model the actual proc cycle or document this as an explicit averaged approximation. Low priority — Phalanx is a single talent that not every Prot Warrior runs.

---

## Things simf gets RIGHT

- **OK**: Armor formula structure `armor / (armor + K)`, capped at 0.85 = `MAX_ARMOR_DAMAGE_REDUCTION`. Identical to SimC.
- **OK**: Versatility damage reduction = `versatility_pct * 0.5`, applied multiplicatively to all damage. Matches `composite_mitigation_multiplier`.
- **OK**: Avoidance rolled BEFORE damage application. Matches SimC's `result_is_hit` gate.
- **OK**: Spell reflect treated as full avoid for damage-taken purposes.
- **OK**: Multi-buff DR stacking is multiplicative (Indomitable × Shield Wall × Demo Shout, etc.). Matches SimC's bucket-of-buffs in `composite_mitigation_multiplier`.
- **OK**: Absorbs (Ignore Pain, healer shields) applied AFTER all multiplicative DR. Matches SimC's `assess_damage_pre_absorb` → `assess_damage` flow.
- **OK**: Healer DR external (`healer_dr_amount`) is multiplicative. Matches SimC's externals like Pain Suppression.

---

## Recommended order of fixes

Land in this order — each step is small and validates before the next:

1. **F1 + F4 together (must be atomic)** — K=2700→3430 + Defensive Stance to all damage at 0.15. The errors were near-cancelling for Brutoh, so RMSE may *improve* not regress. Repurpose `simf calibrate-k` as a sanity check: with the new math, best-fit K should land near 3430; if it doesn't, something else is wrong.
2. **F5** — real rating-per-percent + DR breakpoints. Mostly impacts stat weights and cross-tank comparison. *RMSE impact small for Brutoh (he's below DR), large for stat-weight UI accuracy.*
3. **F2 + F3** — block routed through armor curve. Non-trivial refactor; test on Shield Block windows. *RMSE impact: small per-event but consistent.*
4. **F7** — mastery → crit-block chance coefficient. Constant tune only.
5. **F9, F10** — document the remaining fudges. Defer first-principles fix to when we have multi-character logs.
6. **F6, F8** — accept current state; revisit if calibration suggests need.

Step 1 alone should be enough to *materially* change cross-tank accuracy. Steps 2-3 are next-order. Steps 4+ are diminishing returns.

## Follow-up items discovered during the audit

These were not in the original F1–F10 set but surfaced while implementing
the fixes. Track separately so the next audit pass can pick them up.

### F11. `mastery_block_value_scaling: 0.5` is structurally wrong — **CLOSED 2026-05-20 (post-F12)**

- **Resolution:** Flipped 0.5 → 0.0 alongside F12 in commit da52704.
  The previous 0.5 was an empirically load-bearing fudge masking the
  missing shield-item block_value contribution. With F12 now sourcing
  block_value from `shield_armor × 2.5`, the SimC-truthful 0.0 is safe.
- **Structural truth (primary-source verified 2026-05-19):** SimC
  `sc_warrior.cpp:8910` (`composite_block_value`) has no mastery term.
- **Ship sweep:** RMSE at canonical K=3430 went 0.066 → ~0.079 (modest
  regression bought structural truth + a real shield ilvl axis). Best K
  unchanged at 3200/0.063. See
  `docs/validation/k_calibration_f12_2026_05_20.md`.
- **Earlier (2026-05-19) attempts** (both reverted, retained for history):
  1. Isolated flip 0.5 → 0.0 before F12 — RMSE 0.062 → 0.106 (~70% worse).
  2. Atomic F11+F7-retune+F13 before F12 — RMSE 0.066 → 0.110 (~67% worse).
- **Full trail:** `docs/validation/k_calibration_mastery_chain_2026_05_19.md`,
  `docs/validation/k_calibration_f11_revert_2026_05_19.md`,
  `docs/validation/simc_warrior_mastery_2026_05_19.md`,
  `docs/validation/k_calibration_f12_2026_05_20.md`.

### F12. Shield-item block_value sourcing — **CLOSED 2026-05-20**

- **Resolution:** Commits dbb1ab9 (F12.1 plumbing) and da52704 (F12.2+F12.3
  math). `Character` carries `shield_armor: int`, populated from the
  off-hand item's armor stat via Wowhead XML in `io/item_db.py`.
  `block_value_rating()` now returns `shield_armor × 2.5` per SimC
  `engine/player/player.cpp:1681`. The legacy `Character.block_value_pct()`
  method was deleted (no remaining consumers).
- **Sweep result:** RMSE at canonical K=3430 ~0.079 (was ~0.066 pre-F12).
  Below the 0.080 ship threshold; structurally correct but does NOT
  fully close the empirical-vs-canonical K gap on its own. Residuals at
  K=3400 skew positive — unmodeled mitigation remaining, candidates are
  F13 + F7-retune (now feasible) or Wowhead-931 shield_armor being too low.
- **Ground-truth check (resolved 2026-05-20):** in-game tooltip read of
  Spellbreaker's Rebuke gave 989 armor at ilvl 295 (post-voidcore upgrade
  on 2026-05-19). Wowhead XML's 931 value was correct for the shield's
  pre-upgrade ilvl 285 — exactly what every calibration log in `examples/`
  was recorded under. `brutoh.yaml` stores 989 (current state). The K
  sweep was correctly matched to the historical corpus at 931. Wowhead
  XML for crafted items honors bonus_ids — the recon's open question on
  this was wrong.
- **Full trail:** `docs/validation/f12_shield_block_value_recon_2026_05_19.md`,
  `docs/validation/k_calibration_f12_2026_05_20.md`.

### F13. Mastery → block chance — **CLOSED 2026-05-20 (atomic with F7-retune)**

- **Resolution:** New constant `base.mastery_block_chance_scaling: 0.5`
  wired into `Character.base_block()` for Prot Warrior. Implements
  SimC `sc_warrior.cpp:8893` —
  `block_subject_to_dr = cache.mastery() × effectN(2).mastery_value()`
  with `effectN(2).mastery_value() = 0.5` per spell 76857. In-game
  tooltip cross-check at 8% mastery: block +4% confirms the 0.5
  coefficient.
- **Shipped atomic** with F7 retune (1.0 → 1.5). At Brutoh's ~16%
  mastery, F13 adds ~8pp pre-DR block chance — well below any DR
  breakpoint, so F14 deferral does not bias the result.
- **Ship sweep:** The post-F12 systematic positive bias (mean +3.6%,
  13/18 logs over-predicting damage) is gone. Mean residual at K=3400
  is now **−0.4%** (9/18 positive, 9/18 negative). RMSE at canonical
  K=3430 ≈ 0.065 — top band of the ship criteria.
- **Full trail:** `docs/validation/k_calibration_mastery_chain_2026_05_20.md`.

### F14. Block chance diminishing returns — **CLOSED 2026-05-23**

- **simf (post-F14):** `character.py:base_block` for `protection_warrior`
  returns `block_chance + _composite_block_dr(mastery_pct × 0.5)`. The
  flat `block_chance` baseline is NOT passed through the DR curve; only
  the mastery contribution (and block rating, which is zero in Midnight
  12.0.5) flows through.
- **SimC source:** `engine/player/player.cpp:5467` —
  ```
  diminished = bonus / (block_factor × bonus × 100 × vertical_stretch
                        + horizontal_shift)
  ```
- **DBC constants** (Warrior class_id=1, identical for every other modelled
  tank class in `engine/dbc/sc_extra_data.inc` lines 1807-1818):
  - `block_factor: 1.0`
  - `block_vertical_stretch: 0.0067`
  - `horizontal_shift: 1/0.94` ≈ 1.0638298
- **Impact at Brutoh's stack:** mastery_pct ≈ 0.25, so pre-DR bonus_block
  ≈ 12.5%. Post-DR contribution ≈ 10.9% — F14 takes ~1.6pp off block
  chance, which propagates to ~0.2% expected mitigation loss on physical
  events. constants_version 17 → 18.

**SimC audit chain F1-F14 is now closed.** No further structural divergences
between simf's Prot Warrior mit math and SimC's `target_mitigation` path
remain outstanding.

## What this audit does NOT cover

- Per-spec mitigation for Brewmaster / Guardian / Pal / DK / VDH — these have their own `apply_*_mitigation` functions in `classes/`. They will have their own audit when we extend the work.
- Log-replay parsing accuracy (the path from WCL JSON → `DamageEvent`).
- Healer/HPS coupling (`HealingProfile` model).
- DPS-side math (we're a survivability sim; DPS is presented as relative trade-off only).
