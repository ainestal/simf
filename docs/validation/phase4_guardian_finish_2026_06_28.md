# Guardian finish — self-heal accounting + honesty caveat + the calibrated gate

**2026-06-28.** Closes out the Guardian *modeling* work (no new data needed) and
records the precise data gate that still blocks `guardian_druid.calibrated: true`.
Follows the Guardian model build-out (#199–#211) and the mastery/self-heal
recalibration (`phase4_guardian_self_heal_mastery_2026_06_27.md`).

## What shipped here

### 1. Guardian self-heals are now recorded to `heal_timeline`

Guardian **Tooth & Claw** (`GuardianPolicy.tick`) and **Frenzied Regeneration**
(`GuardianPolicy.decide`) heal the tank from its own kit. They mutated
`state.hp` directly but were never recorded to the runner's `heal_timeline`, so
HRPS / ETMI / Normalized Tank Score **under-credited Guardian self-sustain**
(the open follow-up named in the #210/#211 docs).

- New `MitigationState.apply_self_heal(t, heal)`: clamps the heal to missing HP
  (identical to the old inline `hp = min(max_hp, hp + heal)` — **death calc
  unchanged**), records the *effective* (post-clamp) amount to a new
  `self_heal_events` list, and returns it.
- Both Guardian self-heals call it instead of mutating `hp` inline.
- The runner drains `state.self_heal_events` into `heal_timeline` each iteration
  (order-independent — the metrics bin `heal_timeline` by timestamp).

**Scope / safety:** both heals live in `GuardianPolicy`, so this is Guardian-only.
The warrior (calibrated) path is **bit-identical** — its policy never calls
`apply_self_heal`, pinned by a source-grep tripwire
(`test_apply_self_heal_only_wired_on_guardian_path`). The change is purely
additive accounting: it correctly **lowers** Guardian HRPS and **improves**
(lowers) its ETMI, since self-sustain reduces the healing an external healer must
supply. The warrior (calibrated) path has no self-kit heal of this shape (its
only self-heals — Fueled by Violence reflect, Last Stand's max-HP boost — are
either already recorded or not heals), so it is unaffected.

**Cross-spec caveat (tracked follow-up).** Four *other* `calibrated: false`
specs still apply their self-kit heals via the old inline `state.hp = min(...)`
and are deliberately left un-recorded for now: Blood DK Death Strike
(`blood_death_knight.py`), VDH Soul Fragment + Soul Cleave (`vengeance_dh.py`),
Prot Paladin Word of Glory (`protection_paladin.py`). So until they're migrated,
the **cross-spec Normalized Tank Score** credits Guardian's self-sustain but not
theirs — acceptable while all four remain un-calibrated (cross-spec NTS isn't a
trusted output yet), but it must be closed before any of them is trusted for
cross-spec comparison. Migrating them is a separate, reviewed change (it shifts
those specs' HRPS / NTS); the source-grep tripwire pins the current Guardian-only
wiring so the migration is deliberate, not accidental.

Tests: `tests/test_guardian_selfheal_accounting.py` (unit clamp/record/no-op +
no-healer sim proving credit + HRPS-below-DTPS + the Guardian-only tripwire).
The mastery healing test (`test_guardian_natures_guardian_heal.py`) was retuned
from raw=28000 → 45000 to keep the tank in the (50%, 100%) HP band — above the
FrR trigger (no mastery-neutral confound) and below max HP (no overheal
convergence), so the mastery lever shows cleanly (~1.47× vs the ~1.30 heal_mult
floor). The old value sat in the overheal-converged regime once self-heals were
counted, which is *why* it broke — the failure was correct.

### 2. Guardian-specific honesty addendum

`_uncalibrated_spec_warning()` now appends a Guardian-specific line (via
`_SPEC_MODELING_CAVEAT`) naming the un-regressed levers, so a Guardian user
doesn't read the generic "not calibrated" line and assume every term is equally
solid. Tests: `tests/test_uncalibrated_spec_warning.py`.

## The remaining gate to `calibrated: true`

**Decided against flipping (2026-06-28) on cross-player evidence.** Validated the
model against **11 real, recent, gear-certain Guardian logs from WCL zone 47**
(distinct players, +16/+19/+22) — see
`phase4_guardian_wcl_validation_2026_06_28.md`. Result: **8/11 within ±15%, RMSE
0.155** (vs Warrior's 0.068), roughly **unbiased** (mean +3.2%, best-K 3000 ≈
canonical → no flat gap) but scattering wider than the single AnonGuardian1 profile
(13/16, mean 9.2%) suggested. The population scatter itself is the gate. Biggest
driver: the **magic-mitigation residual** — both over-prediction outliers
(+21%, +33%) are Nexus-Point Xenas (70% magic), where the model under-credits
Guardian magic DR. So `calibrated: false` stays, now blocked on a *model* gap
(magic mit) plus the single-profile data limits:

1. **Haste→Ironfur slope is un-regressable** — AnonGuardian1 is a single Elune's-Chosen
   build with gear-locked haste (~12.1%), so the elasticity `k` (0.75) is a
   single-point fit. **Unblock:** a 2nd Guardian profile at a materially
   different haste %, OR a **Druid-of-the-Claw** log (different hero tree, no EC
   rage engine). The owner *can* produce this — it's the one concrete data ask.
2. **Mastery→healing conversion** is medium-confidence (single mastery point;
   pending an in-game tooltip read).
3. **Magic-mitigation residual** remains.

**Tracked modeling follow-up (pre-existing, surfaced by the review):** Tooth &
Claw is modeled as a %-max-HP heal (`tooth_and_claw_heal_pct_max_hp`) yet is
scaled by `incoming_healing_multiplier()` (the Nature's Guardian 227034 proc).
But the SimC source (this project's own finding,
`phase4_guardian_self_heal_mastery_2026_06_27.md`) shows the NG proc *excludes*
percent heals — which is exactly why **FrR** is correctly NOT mastery-scaled. So
T&C and FrR (both %-max-HP) are treated inconsistently. If Midnight T&C is truly
a %-max-HP heal it should be mastery-neutral too; if it's a flat/absorb heal the
current routing is fine. Confirm against SimC/datamining before changing — it
would slightly lower Guardian's mastery marginal. NOT introduced by this commit
(predates it; this commit only swapped the inline clamp for `apply_self_heal` on
that line).

**Procedure when the 2nd build lands** (so the flip is mechanical, not
archaeology): drop the log → `scripts/calibrate_spec_from_logs.py` → confirm the
haste slope holds at the new haste % → re-run the K sweep → if Guardian lands
within ±15% across both builds, flip `guardian_druid.calibrated: true`.

## Interaction with 12.1.0

Patch 12.1.0 ("Curse of Ula'tek", ~Aug 2026) buffs **Brambles +25%** and applies
a systemic +25% HP / +25% creature-damage rescale — which will **re-open**
Guardian calibration on launch. That is *why* finishing the modeling (not
flipping the flag) was the right call now: the model structure survives the
patch; only the constants re-fit on live numbers + fresh Season-2 logs. See
`patch_1210_readiness_2026_06_28.md`.
