# Phase 4 — VDH Painbringer + party-DR wiring, and the hydrate-exposure re-check (2026-07-03)

Closes the two layers PR #143 deliberately deferred
(`phase4_vdh_characterization_2026_06_09.md`, "Deliberately deferred"):
**Painbringer** (blocked on a value confirmation) and **`party_dr_by_school`
parity wiring**. Also resolves the open follow-up from
`phase4_protpal_block_value_2026_07_03.md`: whether VDH's WCL corpus is exposed
to the COMBATANT_INFO armor-contamination bug class fixed for the local-log
path the same day.

`specs.vengeance_demon_hunter.calibrated` stays **`false`** — see the verdict.

## 1. Hydrate-exposure re-check: the WCL path is NOT exposed (no bug)

The local-log bug: `_select_combatant_info` picked the LAST of a log file's
many COMBATANT_INFO snapshots, so a temp armor buff up at snapshot time
polluted the hydrated armor. Demon Spikes / Metamorphosis are armor buffs →
the ProtPal doc flagged VDH's corpus for a re-check.

**Finding: the bug class structurally cannot apply to the WCL path, and
empirically does not.** VDH's corpus hydrates through
`wcl_combatant_info.character_from_wcl` →
`wcl_api.fetch_combatant_info_events`, which never touches
`_select_combatant_info` — and there is no multi-snapshot selection to get
wrong. Probed raw for all 3 corpus fights (2026-07-03):

| Tank | CombatantInfo events for actor (in fight) | Snapshot ts (rel) | First DS window | First Meta window | Snapshot inside any DS/Meta window? |
| --- | --- | --- | --- | --- | --- |
| AnonPlayerX1 | 1 (of 5 total = 1/player) | +0.0 s | +19.2 s | +21.4 s | No |
| AnonPlayerX3 | 1 | +0.0 s | +6.5 s | +18.7 s | No |
| AnonPlayerX2 | 1 | +0.0 s | +11.9 s | +11.9 s | No |

WCL emits exactly **one pre-combat snapshot per player per fight** (stamped at
fight start), and no tank had Demon Spikes or Metamorphosis up at that instant
(no pre-pull casts either — every first window opens seconds into the pull).
AnonPlayerX2's higher armor (4,108 vs 3,273/3,355) tracks their higher agility
(2,514 vs ~1,950) and gear, not contamination. **The #143 armor inputs stand
unchanged; no fix needed, none made.**

## 2. Painbringer: value pinned — and it is small now

#143 deferred Painbringer because "SimC's live effect base reads 0
(scripted/rank-driven)" — refusing to invent a number. The value is now pinned
from primary sources, and the 0 is explained:

- **Wowhead tooltip** (talent 207387, live Midnight): *"Consuming a Soul
  Fragment reduces all damage you take by 3% for 8 sec."* The buff aura's own
  tooltip template renders "reduced by 0%" — the static aura carries no value.
- **DB2, pinned to the live retail build 12.0.7.68367** (wago.tools; NOT the
  12.1.0 PTR build, per the volatile-PTR rule): talent **207387** effect 0 =
  `A_ADD_FLAT_LABEL_MODIFIER` (219), **value −3**, ModOp 3 (modifies Effect #1),
  **label 2406** → buff **212988** (carries label 2406 in SpellLabel) effect 0 =
  `A_MOD_DAMAGE_PERCENT_TAKEN` (87), **base 0**, school mask **127 = all seven
  schools**. So the buff's base IS 0 and the −3 arrives via the talent's label
  modifier — the earlier audit read the right field and correctly stopped.
- **Stacking: GONE in Midnight.** `SpellAuraOptions.CumulativeAura = 1` (max
  1 stack; the DF-era per-stack version no longer exists). Re-consuming a
  fragment refreshes the 8 s duration (SimC midnight:
  `set_refresh_behavior(DURATION)`).
- **SimC midnight** (`sc_demon_hunter.cpp`) triggers the buff on every soul-
  fragment consume and applies it via `parse_effects(buff.painbringer)`, and
  the buff's own raw effect base is 0.

  **Post-validator correction (engine-math pass, 2026-07-03):** the paragraph
  above originally claimed "SimC itself credits 0% for Painbringer — same
  SimC-gap class as Demon Spikes' agi-armor." The engine-math validator flagged
  this as likely wrong: SimC builds the Painbringer buff with
  `set_default_value_from_effect_type(A_MOD_DAMAGE_PERCENT_TAKEN)`, the
  identical pattern it uses for **Fiery Brand** (`fiery_brand_debuff`, also a
  `talent_spell_lookup`) — and Fiery Brand's −40% is unquestionably credited
  by SimC. If this pattern resolved to 0, Fiery Brand would do nothing, which
  it doesn't. So SimC most likely resolves Painbringer's real −3% at runtime
  too; the earlier "0" was a static-DB2-effect-base read, not proof of a SimC
  omission (unlike Demon Spikes' agi-armor, which #143 confirmed as a genuine
  SimC gap via `parse_effects` not handling subtype 268 — that comparison
  doesn't hold here). This does **not** change the 3% value, which comes from
  Wowhead + DB2 independent of SimC either way; it only corrects the story of
  *why* the earlier audit read 0. (Not verified to certainty — `talent_spell_
  lookup`'s value-resolution body is declared out-of-line and wasn't traced
  directly — but the Fiery-Brand analogy is high-confidence.)

Measured time-weighted buff uptime on the corpus (from the same
`fetch_buff_windows` data the replay now credits): **89.9% / 95.2% / 95.3%**
— the "~95%" scoping claim from #143 holds.

**Honesty note — the magnitude changed the story.** #143's closure section
called Painbringer "the main remaining magic-gap lever," written when it was
believed per-stack (DF-style, plausibly 3%×N). Midnight's non-stacking flat 3%
is a *small* layer: marginal on the ~24pp magic residual ≈ 3% × (1 − 0.24) ≈
**2.3pp**, and ≈ 0.7pp on physical (existing DR 77.2%) — per the
`N × (1 − existing_DR)` discipline. It is wired because it is real, pinned,
and free of degrees of freedom — **not** because it closes the residual. It
doesn't.

### Wiring

`apply_vengeance_dh_mitigation` step 4b: `damage *= 1 − painbringer_dr` while
`painbringer_spell_id ∈ event.active_buffs` — window-gated off the log's real
buff windows, exactly the Metamorphosis pattern (per-event containment test,
stamped by `adapt_events` from `wcl_api.fetch_buff_windows`). The **live**
(non-replay) path takes **no credit** — the policy has no soul-fragment-consume
model; same calibration-path-first staging as Meta armor / Brewmaster PT, and
the same known live-vs-replay inconsistency, now three layers wide (named in
the caveats).

No calibration-script change was needed: `calibrate_spec_from_wcl.py` already
auto-collects every `specs.<spec>.*_spell_id` constant into the buff-window
fetch, so adding `painbringer_spell_id: 212988` to constants makes the corpus
re-run credit it ("window-gated buffs fetched: [187827, 203819, 212988]" in
the run log). The Brewmaster-ledger warning in the script's docstring does not
bite here — that path is for `active_buff_spell_ids`-style static gating, not
`*_spell_id` window gating.

**A real gap this DID surface:** the *shipped* `simf calibrate-k --wcl-url`
path never passed `buff_ability_ids` to `wcl_to_replay_data` — so Meta armor
(since #143!) and Painbringer were silently zeroed through the CLI while the
characterization script credited them. Anyone reproducing a corpus fight via
the shipped CLI would have read a contradictory (worse) delta. Fixed in
`cli.py` (collects the spec's `*_spell_id` constants, same convention);
pinned by `tests/test_cli_calibrate_wcl_buffs.py`.

## 3. `party_dr_by_school` parity — wired, and honestly worth zero here

The warrior (`mitigation.py`) and Guardian (`guardian_druid.py`) blocks are now
mirrored in the VDH chain with the same values (`{all: 0.05, magic: 0.00}`).
**This contributes ZERO to the calibration deltas below**: the layer is gated
on `state.party_magic_dr_active`, which only the per-school decomposition
script and tests ever set — `run_simulation` never does, for any spec. Every
spec's corpus (warrior's 0.068 RMSE included) is judged with it off, so the
comparison stays apples-to-apples. The wiring buys structural parity (the
decomposition tooling and any future replay opt-in treat VDH like the other
specs), not delta movement — stated so nobody credits it for the numbers.

## 4. Calibration re-run (same 3 fights, iters=250, seed=42, canonical K=3430)

Before = #143 as shipped (`phase4_vdh_characterization_2026_06_09.md` LANDED
section). After = Painbringer window-gated. The run log confirms the new
window is actually fetched (`window-gated buffs fetched: [187827, 203819,
212988]`) — the only engine-visible change vs #143 is the Painbringer layer.

```
=== Sweep (iters=250, canonical K=3430) ===
  K= 2000: RMSE=0.133  [+0.1%, +22.4%, +5.2%] <-best
  K= 2500: RMSE=0.241  [+14.2%, +37.0%, +13.5%]
  K= 3000: RMSE=0.366  [+27.2%, +50.9%, +26.2%]
  K= 3430: RMSE=0.468  [+37.5%, +62.0%, +36.5%] [CANONICAL]
  K= 3500: RMSE=0.484  [+39.1%, +63.7%, +38.1%]
  K= 4000: RMSE=0.595  [+49.9%, +75.4%, +49.2%]
VERDICT: all per-run deltas within +/-15% at canonical K? False
```

| Log | #143 before | #143 after (was) | + Painbringer (now) |
| --- | --- | --- | --- |
| AnonPlayerX1 +19 Windrunner Spire | +101.8% | +43.6% | **+37.5%** |
| AnonPlayerX3 +20 Skyreach | +149.4% | +73.0% | **+62.0%** |
| AnonPlayerX2 +21 Pit of Saron | +113.7% | +43.1% | **+36.5%** |

Canonical-K RMSE: 1.233 (pre-#143) → 0.551 (#143) → **0.468**.

**Movement vs the pre-registered prediction.** The naive marginal (−3% ×
uptime, pre-absorb) predicted deltas of ≈ +39.7 / +68.0 / +39.0. Observed
movement is larger (relative sim-total −4.3% / −6.4% / −4.6% vs the naive
−2.7 to −2.9%) — explained by two mechanical amplifiers #143's caveats
already document, both pushing this direction: (a) the replay subtracts the
log's **fixed** absorb amounts, so a pre-absorb DR cut shrinks the
post-absorb total by more than the DR itself (AnonPlayerX3 — the heaviest
Soul-Barrier absorber in the corpus — moves the most beyond prediction,
exactly as this predicts); (b) **damage-weighted** Painbringer coverage
exceeds its time-weighted uptime (fragments are generated precisely while
damage is flowing; the gaps are between pulls). No unexplained residual
movement; the layer behaves like what it is — a small, real, pinned DR.

At the sweep floor K=2000 two of three fights now sit within ±15%
([+0.1%, +22.4%, +5.2%]) — but the floor-pinned best-K is still the
under-mitigation signature, and the canonical-K deltas are what counts.

## 4b. First Aldrachi Reaver coverage (new fight, same engine)

The #143 gate "an Aldrachi Reaver log confirms the build doesn't break the
model" had zero data — the top-key meta is monolithically Fel-Scarred.
A bounded rankings probe (zone 47, lower brackets where build diversity
lives; hero tree detected from in-fight glaive auras 442435/442695/444661/
444764 — NOT aura 1270547, which turned out to appear on every probed VDH
and is useless as a Fel-Scarred discriminator despite #143's note) found one
on candidate #20 of 24:

- **Player 1 — Magisters' Terrace +15**,
  ilvl 292, armor 3,698 / agi 1,905 / HP 815k — plausible stats, and
  hydrate-clean by the same probe as §1 (1 snapshot at +0.0 s; first DS
  window +9.6 s, first Meta +16.9 s).

```
  K= 2000: RMSE=0.357  [+35.7%] <-best
  K= 3430: RMSE=0.787  [+78.7%] [CANONICAL]
```

**Read with restraint (N=1):** +78.7% sits above the Fel-Scarred trio's
band (+36.5…+62.0). Three confounds travel together — hero build, dungeon
school-mix, and key level — and the parsimonious read is school-mix first:
the dominant known residual is magic-DR, and Magisters' Terrace is
magic-heavy, exactly as fire-heavy Skyreach is the outlier (+62.0) inside
the FS trio. A build-specific unmodeled layer (Thrill of the Fight, Wounded
Quarry, AR's different Meta cadence — 31 Meta windows in 1302 s here) is
possible on top, but not separable at N=1. What this fight does establish:
the engine runs AR fights end-to-end, the deltas are the same
under-mitigation direction/order, and **the AR gate is now "uncharacterized
with one data point" instead of "unobserved."** It stays a gate.

## 5. Verdict

`calibrated:` **stays `false`**, on three independent grounds:

1. **The deltas are far outside ±15%** at canonical K (+37.5 / +62.0 /
   +36.5). Painbringer's honest 3% closed ~6–11pp of a +43–73%
   over-prediction; the dominant residual is unchanged in kind.
2. **The Aldrachi Reaver gate** — all 3 corpus fights are Fel-Scarred; an AR
   fight was added below as first coverage, but one fight ≠ a validated
   build model.
3. **N=3(+1), single region-era** — even a within-±15% result on this corpus
   would be characterization-grade, not calibration-grade.

**Attribution correction for the next session.** #143's log-replay audit
attributed the zero-absorb magic hits taking 38–64% real DR to "the
Painbringer gap." At Painbringer's real 3% that attribution is dead: the
now-modeled magic stack (vers ~1% × Wards 12% × brand ~7% avg × PB 3%)
composes to ~21–22% — reality's 38–64% on those hits needs **another large
magic layer** that remains unidentified. Named leads from the SimC midnight
source, none yet characterized: **Immolation Aura** carries a parsed
damage-taken modifier hook (`parse_effects(buff.immolation_aura)` sits in
SimC's Vengeance DT chain — near-100% uptime buff if it carries a DT effect
in Midnight, label-modified like Painbringer was); **Fel Flame
Fortification** (magic-DR talent, SimC comment literally says "No
Implementation" — a SimC blind spot exactly like Demon Spikes' agi-armor);
**Void Reaver / Frailty** (target-gated DT reduction,
`parse_target_effects(frailty)`). Any of these must be pinned from
DBC/tooltip the way Painbringer was — the label-modifier mechanism is now a
known pattern to check first (a base-0 effect no longer means "no value").

## Caveats (inherited + new)

- All #143 caveats stand: synthetic healer externals make displayed deltas
  optimistic; anchor on pre-absorb for mitigation comparisons; ±20%
  single-fight noise band.
- Painbringer's live-path omission widens the known live-vs-replay layer gap
  (Meta armor, Demon Spikes real windows, now Painbringer). A linear flat DR
  *can* be safely uptime-averaged for the live path (unlike the non-linear
  armor multiplier) — deliberately not done here to keep this change
  fit-free; it needs its own ratification with an uptime constant that would
  currently come from N=3 (overfit risk).
- The party-DR values ({all: 0.05}) are the warrior/Guardian defaults, not
  VDH-measured; acceptable because the layer is opt-in-only today.
