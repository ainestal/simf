# Guardian self-heal + mastery recalibration — 227034 is the mastery proc, not a stream

**2026-06-27.** Follow-up to the #210 Nature's Guardian healing-received lever
(`phase4_guardian_mastery_hp_2026_06_25.md`). The #210 doc's "Still open" note
proposed modeling **Frenzied Regeneration (22842)** and a **"Nature's Guardian"
proc self-heal (227034)** as two unmodeled Guardian self-heal streams (~25% of
AnonGuardian1's received healing), to fix an understated mastery. A research + SimC-source
audit **refuted half that premise** and produced a better, log-grounded fix.

## Finding 1 — 227034 is the mastery's OWN heal proc, not a separate ability

SimC `engine/class_modules/sc_druid.cpp` (midnight branch, build 12.0.x), inside
`if ( mastery.natures_guardian->ok() )`:

```cpp
12466:  driver->name_str = "natures_guardian";
12467:  driver->spell_id = mastery.natures_guardian->id();        // 155783 (the mastery)
12468:  driver->proc_flags2_ = PF2_ALL_HIT | PF2_PERIODIC_HEAL;
...
12480:  return !pct_heal->base_pct_heal && !pct_heal->tick_pct_heal; // EXCLUDES % heals
...
12483:  auto _heal = get_secondary_action<druid_heal_t>( "natures_guardian", this, find_spell( 227034 ) );
...
12490:  _heal->base_dd_min = _heal->base_dd_max = s->result_total * cache.mastery_value();
```

So **spell 227034 is the heal the mastery driver fires** on every *non-percent*
heal received, healing for `received_heal × mastery_value`. It is the mastery's
healing-taken aura **realized as discrete heal events in the combat log** — *not*
a separate ability and *not* the Lunar Beam leech (that is the distinct spell
`204069`, which already appears as its own 7.8% line in AnonGuardian1's healing breakdown).

**Implication:** simf already models this effect as a multiplier
(`Character.incoming_healing_multiplier()`). Adding 227034 as a *new* self-heal
stream would **double-count mastery** — the exact trap #210 was built to avoid.
The original workflow design (an "EC-gated 227034 stream") was discarded.

The max-HP side of the mastery (line 13122, `resources.max *= 1 + cache.mastery_value()`)
uses the **full** `mastery_value` too — corroborating that both auras are coeff
**1.0 × mastery%**, not 0.7. (simf still does not add an explicit max-HP term;
the stamina→HP path already reproduces the NG-inclusive sheet HP — see #210.)

## Finding 2 — Frenzied Regeneration is mastery-NEUTRAL

FrR (22842) **is** a real, previously-unmodeled self-heal — but it is a
`%`-max-HP heal, and the 227034 proc condition explicitly **excludes percent
heals** (`!base_pct_heal && !tick_pct_heal`, line 12480). So the Guardian mastery
does **not** amplify FrR. Modeling FrR adds general Guardian survival accuracy but
**cannot** be the fix for "understated mastery."

Modern (8.0.1+) FrR is a flat % of max HP over 3s (the pre-8.0 "% of damage taken
in the last 5s, min 5% max HP" formula was removed). Icy Veins 12.0.7: ~20% max
HP, 4 ticks; **Innate Resolve** (near-mandatory M+ talent) adds a 2nd charge and
scales the heal up to +120% the lower your health; Well-Honed Instincts auto-casts
it below 40%.

## Finding 3 — the real fix: the mastery coefficient was 0.7 but should be 1.0

simf modeled the multiplier as `1 + 0.7 × mastery_pct` = ×1.103 at 668. SimC heals
227034 for `received_heal × cache.mastery_value()` (line 12490) — the **full**
mastery_value, coeff **1.0**, not 0.7. The 0.7 Wowhead "SP mod" is the per-mastery-
*point* coefficient that is already baked into the displayed mastery %; applying it
again on top double-counted. The correct multiplier is `1 + mastery_value`.

**The raw mastery_value ≈ 0.147** at AnonGuardian1's 668 rating, confirmed three ways:
1. simf's own `mastery_pct(668) = 0.1468`.
2. A **proc-pairing** measurement (dual-validator, 2026-06-27): pairing each 227034
   heal with the heal that triggered it gives `227034_raw / trigger_raw ≈ 0.13–0.14`.
3. SimC's max-HP aura (line 13122) uses the same full `mastery_value`.

So `incoming_healing_multiplier(668) = 1 + 1.0 × 0.1468 = 1.147` (was 1.103).

### ⚠️ Why NOT ×1.226 (an earlier draft's error, caught by the log-replay validator)

A first pass measured the **whole-fight effective ratio**
`227034_eff / (total_received_eff − 227034_eff − FrR_eff)` and got **0.226 ± 0.023**
(CV 10.2%, 10 logs) — and wrongly proposed `coeff 1.54` to hit ×1.226. That ratio is
**inflated by differential overheal**: the mastery proc is ~95% efficient (it fires
in small chunks) while the healer streams overheal ~50%, so the *effective* ratio
overstates the *true* coefficient by ~1.5×. It is **not** the multiplier:

- simf applies the multiplier to the **OFFERED** `baseline_hps`, which the runner
  then **clamps to missing HP** (`min(max_hp − hp, heal)`). The physically-correct
  multiplier on a pre-clamp offered stream is `1 + mastery_value` (raw), not the
  post-overheal effective ratio.
- **Survival is decided in low-HP windows** where overheal → 0, so there
  effective ≈ raw ≈ 0.147. The 0.226 aggregate is dominated by *safe*-window
  overheal differential, which is irrelevant to survival. Applying 0.226 would
  over-credit healing ~7% in exactly the windows that decide deaths.

The effective-ratio table is retained below only as context for *why the proc shows
up as ~19% of received healing*, NOT as the coefficient:

| stat (10 logs) | mean | median | σ | CV | range |
|---|---|---|---|---|---|
| effective ratio (context only) | 0.226 | 0.229 | 0.023 | 10.2% | 0.195–0.265 |
| **raw mastery_value (the coefficient)** | **~0.147** | — | — | — | 0.13–0.14 (proc-pairing) |

### The recalibration

`natures_guardian_heal_coeff: 0.70 → 1.0` (constants_version 28 → 29). Verified:
`incoming_healing_multiplier(668) = 1.1468`.

**LEVEL vs SLOPE.** The logs pin the multiplier *level* (raw mastery_value ≈ 0.147)
and SimC fixes the coeff at 1.0. The *slope* (how mastery_value grows per rating
point) rests on simf's normalized `rating_per_pct = 100` anchor — kept uniform with
every other secondary so cross-stat gem/weight comparisons stay consistent and
K-calibration is untouched — and is **not** independently validated from one gear
point. → `calibrated: false` persists.

## Implementation

- `constants.yaml specs.guardian_druid`: `natures_guardian_heal_coeff 0.70 → 1.0`;
  new `frenzied_regeneration:` sub-block (`base_pct_max_hp 0.20`,
  `innate_resolve_low_hp_bonus 1.2`, `trigger_hp_pct 0.50`, `charges 2`,
  `recharge_s 24`). constants_version 29.
- `character.py incoming_healing_multiplier()`: docstring corrected (227034 =
  mastery proc; FrR excluded). The numeric change is entirely the constant.
- `classes/guardian_druid.py GuardianPolicy`: FrR charge tracker in `__init__`;
  a reactive FrR block in `decide()` that fires when `hp_pct ≤ 0.50` and a charge
  is up, heals `max_hp × 0.20 × (1 + 1.2×(1−hp_pct))` clamped to missing HP,
  consumes a charge (24s recharge). It mutates `state.hp` (same as the existing
  Tooth & Claw heal) and is **NOT** multiplied by `incoming_healing_multiplier()`.
- `tests/test_guardian_natures_guardian_heal.py`: +6 tests — the ×1.147 anchor,
  FrR fires/doesn't-fire/charge-cap/clamp/mastery-neutral/absent-block-noop.

## Validation

- **Mitigation untouched (smoke).** `scripts/calibrate_spec_from_logs.py calibrate
  guardian_druid` judges `mean_dtps` (damage *taken*, a pure mitigation metric).
  This change is healing-only, so per-run dtps deltas are unchanged from the #209
  baseline — the run confirms the replay path still works and mitigation didn't
  move. (Healing affects survival/death-rate, not dtps; the timed corpus has the
  tank surviving in reality, and more modeled healing keeps it surviving — no
  contradiction.)
- **Mastery is a stronger, correct survival stat.** The sim-path `mastery_rating`
  marginal rises with the coefficient (coeff 0.7 → 1.0, so ×~1.43 on the marginal).
  Unit tests pin the ×1.147 anchor and a positive marginal. The gem suggester / gear
  surface consume the sim path, so they now value mastery at the corrected magnitude.
- **Non-Guardian bit-identity.** `incoming_healing_multiplier()` returns exactly
  `1.0` for every non-Guardian spec (test-pinned across 5 specs); FrR lives in
  `GuardianPolicy`, dispatched only for `guardian_druid`. No runner contract change.

## Caveats / still open

- **Slope un-regressable** (single mastery point) and **profile-sensitive marginal**
  — in a high-HPS healer profile the recalibrated mastery can read 2nd-highest for
  survival; the gem suggester could over-recommend mastery gems there. Watch when
  promoting toward calibrated.
- **FrR magnitudes MEDIUM confidence** (single Elune's-Chosen build; `recharge_s`
  patch-uncertain 24–36s; Innate Resolve bonus not separately validated). The
  log-replay validator measured raw FrR ~50–56% max HP per press (5+ ticks/cast,
  not 4) at low HP — consistent with base 0.20 × Innate Resolve + the unmodeled
  Guardian-of-Elune +20%; the runner's clamp brings effective to ~28%/press, in the
  observed effective band. Magnitude is clamp-masked, so it's left as-is.
- **Flagged for a future #210-HP revisit (out of scope here):** the log-replay
  validator measured AnonGuardian1's in-combat `maxHP` field at ~970k–1.02M vs #210's
  observed 782k and simf's modeled ~770k. The 782k is likely an opening/unbuffed
  bear sample; the in-combat figure includes Incarnation (+30%) and raid/temporary
  buffs (which simf models transiently), so this is probably not a steady-state HP
  under-model — but it warrants re-checking the #210 HP anchor against a buff-aware
  in-combat sample. Not touched by this change (FrR is fraction-of-max_hp, so the
  absolute value doesn't bias it).
- **FrR is not yet recorded to `heal_timeline`** (it mutates `state.hp` only, like
  Tooth & Claw), so HRPS/ETMI don't credit it. Consistent with T&C today; a
  heal_timeline-accounting pass for all Guardian self-heals is a follow-up.
- **Wowhead vs SimC on the aura type.** Wowhead labels 155783's 2nd aura "Mod
  Absorb Taken %"; SimC implements the healing benefit as the 227034 heal proc.
  simf routes both heals and absorbs through the one multiplier — acceptable at
  `calibrated: false`.
- `guardian_druid.calibrated` stays **false** (un-regressable slope + single build
  + FrR medium confidence).

## Correction to the #210 doc

`phase4_guardian_mastery_hp_2026_06_25.md` "Still open" section described 227034 as
an unmodeled *self-heal stream*. That was a mis-identification: 227034 is the
mastery's own heal proc (this doc, Finding 1), already modeled by
`incoming_healing_multiplier()`. The genuinely-unmodeled stream was FrR (added
here, mastery-neutral); the genuine mastery gap was the coefficient (recalibrated
here). A pointer note is added to that doc.
