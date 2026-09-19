# Calibration glossary — what K, RMSE, and `calibration_tier` mean

This file exists because a handful of terms — **K**, **RMSE**, `calibrate-k`,
and `calibration_tier` — are mentioned constantly across CONTRIBUTING.md,
ROADMAP.md, `constants.yaml`, and the `docs/validation/` reports, but were
never defined in one findable place. Start here.

---

## K — the armor mitigation constant

**The one-sentence version:** K is the denominator constant in WoW's armor
damage-reduction formula. It sets how much physical damage a given amount of
armor actually mitigates.

```
DR = Armor / (Armor + K)
```

- `Armor` is the tank's total armor (gear + buffs + Bear Form etc.).
- `K` is a single scalar that depends on the **level of the attacking enemy**.
- `DR` is the fraction of a physical hit that armor removes (clamped at 85%).

### The intuition

K is the "armor is worth this much" knob. **Higher K → armor mitigates less.**

Worked example, a tank with 10,000 armor:

| K       | `10000 / (10000 + K)` | Physical DR |
|---------|-----------------------|-------------|
| 2700    | 10000 / 12700         | **78.7 %**  |
| 3430    | 10000 / 13430         | **74.5 %**  |

Same armor, different K, ~4 percentage points of difference in how much every
physical hit is reduced. Because nearly every physical-damage calculation in
the engine runs through this formula, K is the single most leverage-heavy
number in the whole model — which is why it gets mentioned everywhere.

### Its value, and where it comes from

```yaml
# data/constants.yaml
armor:
  k_constant: 3430
```

**3430** is not a number we invented or fit — it is SimulationCraft's canonical
DBC value, `dbc->armor_mitigation_constant(level)` evaluated at **level 90**
(Midnight's level cap). Primary source:
`docs/simc-reference/expected_stat_level_90.txt` (SimC commit `fd60a63`, WoW
build `12.0.5.67602`). Because it comes straight from the game's data tables and
is **character-agnostic**, it generalises across tanks instead of overfitting to
one player.

### Why you'll also see "K=2700" in old notes

Before the 2026-05-18 structural fix, K was a **self-fit** value (~2700) — the
number that minimised error across Brutoh's 16 logs. But that low K was
secretly doing a second job: it was inflating armor's value to paper over
mitigation the engine *wasn't applying yet* (chiefly Defensive Stance's 15%
all-school reduction). Once Defensive Stance was applied as its own layer, the
honest armor constant turned out to be the canonical DBC **3430**. Empirical
back-solves on physical-heavy logs landed K ≈ 3116–3569 depending on the DS
value assumed, and 3430 sits right in that range.

So: **K=2700 = the old self-fit that hid a missing layer; K=3430 = the real
DBC anchor with that layer now modelled.** When you see both in the history,
that's the transition being described.

### Where it's used in code

`core/mitigation.py::calculate_armor_resist(value, k, multiplier)` is the
direct port of SimC's `util::calculate_armor_resist`:

```python
v = value * multiplier
return min(v / (v + k), MAX_ARMOR_DAMAGE_REDUCTION)   # 0.85 cap
```

It is called for **two** things:
1. **Armor DR** on physical events (`multiplier = 1.0`).
2. **Block value**, which is treated as armor-equivalent rating
   (`multiplier = 1.0` for a regular block, `2.0` for a critical block).

---

## `calibrate-k` — the K sweep

`simf calibrate-k` re-derives K *empirically*: it replays a corpus of real
combat logs at a range of candidate K values and reports which one minimises
prediction error (RMSE) against the actual damage taken.

```bash
simf calibrate-k                                              # ratified 16-log corpus (default)
simf calibrate-k --full-scan --logs-dir examples              # explore ALL of examples/ (opt-in)
simf calibrate-k --wcl-url <report-url> --wcl-target <name>   # single WCL fight
```

**Plain `calibrate-k` no longer scans a directory.** It auto-resolves a
versioned manifest (`data/calibration_corpora/prot_warrior_2026_05.yaml`)
pinning the exact 16 ratified replays, so a growing `examples/` folder can
never again silently redefine what "the 16-log corpus" means — see
`docs/validation/calibrate_k_corpus_manifest_2026_07_12.md` for the incident
this fixed (a directory scan quietly grew the corpus to 79 replays and RMSE
from 0.068 to 0.124, entirely via corpus scope creep — no engine change).
Pass `--full-scan` to explicitly opt into scanning `--logs-dir` for new,
not-yet-ratified logs; that path dedupes duplicate files, warns when a log's
date drifts far from the calibration character's gear snapshot, and flags
replays sourced from another character's dedicated subdirectory.

**We do not blindly adopt the sweep's winner.** Early on, the empirical
minimum drifted a little run-to-run (e.g. 3200, 3400, 3450 across different
fixes) but stayed within ~30 of 3430, which was the basis for a
"stay-the-course band" rule: *if the empirical best is close to the canonical
DBC 3430, K stays at 3430.* That changed after the 2026-07-18 Demo
Shout/Phalanx double-count fix (PR #387): re-run on the fixed replay chain,
the empirical best-fit K moved to **~2905–2930** — about 500 away from 3430,
well outside the old band. K stayed at 3430 anyway, but now for a different,
more explicit reason: **methodology, not proximity**. K is
SimulationCraft's real DBC armor constant, shared across every spec and
every armor-based gear recommendation — it is not a free parameter to re-fit
around a mob-side bug, and doing so would silently absorb a still-unexplained
external layer (the "structural physical-mit gap" — see CONTRIBUTING.md's
Next-priorities list) into K instead of naming it. We trade real
Brutoh-specific accuracy for a constant that is primary-source and
generalises to every tank. The sweep is still a **confidence check on the
canonical anchor** — it currently reads "deliberately overridden on
methodology grounds," not "confirmed close," and that's a real, named
trade-off, not a hidden one.

---

## RMSE — how we score a fit

**Root-mean-square error** between simf's predicted survivability/damage-taken
and the ground truth from the logs. Lower is better. RMSE is the number we
watch when any engine change ships — if it regresses, the change usually
made the model *less* like reality. (One important exception, below: a
regression can also mean a previously-hidden bug just got exposed — that's
the model becoming more *honest*, even though the number gets worse.)

The Prot Warrior headline has moved twice since this file was first written,
across the SAME ratified 16-log corpus at the SAME canonical K=3430, and
both moves were real bugs being found and fixed, not the model regressing:

| Date | RMSE | Mean signed bias | Within ±15% | What changed |
|------|------|-------------------|-------------|--------------|
| through 2026-07-17 | 0.068 | ~0% (looked unbiased) | 16/16 | *(later found to be two errors netting to ~zero — see below)* |
| 2026-07-18 (PR #387) | 0.138 | +11.5% over-predict | 10/16 | Fixed a replay-side double-count of two attacker-side mob debuffs (Demo Shout + Phalanx) that were already baked into the logged damage |
| 2026-07-21 (ratified, PRs #406-411) | 0.162 | +13.7% over-predict | 8/16 (50%) | Fixed two further real, SimC-source-confirmed bugs in Shield Block: a multiplier-order error in `calculate_armor_resist`, and a fabricated flat 30% double-count layer that doesn't exist in SimC's real mitigation chain |
| 2026-07-21 (same day) | 0.073 | +1.8% over-predict | 16/16 (100%) | Shipped Vanguard — a previously-unmodelled Prot Warrior spec passive (bonus armor = Strength × 70%) — closing most of the gap the Shield Block fixes had just widened |
| 2026-07-22 (ratified, PR #416) | 0.073 | +1.8% over-predict | 16/16 (100%) | Same fit as above — the LOO-CV gate (the 4th and last `calibrated`-promotion criterion) was wired into `simf calibrate-k` itself and PASSED against this corpus, so `calibration_tier` was re-promoted `characterized` → `calibrated` |
| 2026-07-25 (merged) | **0.080** | **−4.4% under-predict** | **14/16 (88%)** | Merged KYFOTG (Keep Your Feet on the Ground, a Mountain Thane hero-talent proc) on correctness grounds — real, sourced two independent ways, reconfirmed at 3.5x scale on a wider 37-file sample. Widens the gap and flips its sign, but every same-player promotion-bar gate still clears; the cross-player gate above is the sole reason the tier doesn't move |

**0.080 is the current ratified Brutoh-only figure (as of the 2026-07-25
KYFOTG merge) — but `calibration_tier` is `characterized`**, downgraded
2026-07-25 for reasons unrelated to this specific number. The RMSE fit itself
was never the problem: the tier briefly cleared all five promotion-bar
sub-criteria (≥8 F-consistent runs, |bias|≤5%, ≥75% within ±15%, RMSE≤0.15,
LOO-CV pass) at the 0.073 figure, but every one of those criteria had only
ever been checked against Brutoh's own 16-log corpus. The first time the SAME
bar was applied to 15 independent Prot Warrior players (found via WCL
`characterRankings`), it failed: mean bias +11.0% (bar ≤5%), 67% within ±15%
(bar ≥75%), RMSE 0.150 (bar ≤0.15, exactly on the line). See
`docs/validation/protwarrior_calibrated_downgrade_2026_07_25.md` and the
`calibration_tier` section below for the full writeup — this generalization
gap is now itself a named, permanent addition to the promotion bar (see
`docs/validation/protwarrior_cross_player_validation_gate_2026_07_25.md`).
The KYFOTG merge that same day is a separate decision, made independently of
the downgrade: it still clears every SAME-PLAYER gate (|bias| 4.4%≤5%, within
±15% 88%≥75%, RMSE 0.080≤0.15, LOO-CV PASS) even though it moved the number —
see `docs/validation/protwarrior_kyfotg_wide_archive_2026_07_25.md` for why
the widening is evidence the OLD 0.073 fit was partly coincidental, not
evidence the new mechanic is wrong.

The two intermediate RMSE widenings (0.068→0.138, 0.138→0.162) were both real
bugs being found and fixed, not the model regressing: the old 0.068 headline
was two double-counting bugs silently canceling a separate, already-known
cross-spec gap (the "structural physical-mit gap" — see CONTRIBUTING.md's
Next-priorities list); fixing them didn't make the model worse, it stopped it
from lying about being better than it is. Vanguard then closed nearly all of
what those fixes exposed. Two caveats still stand on top of the tier
question: the cross-spec ~5-7% run-scoped wedge is still open (it affects
other specs, e.g. Brewmaster, ProtPal — not resolved by anything above), and
a separate ~11% magic-dominated "clean wedge" residual
(`scripts/full_chain_wedge.py`) is untouched by Vanguard (a physical-only
armor term). The per-dungeon breakdown has NOT yet been formally re-derived
under this fixed chain — `constants.yaml`'s `calibration.per_dungeon` block
stays flagged `⚠ STALE` on purpose (a dedicated re-derivation task, not a
side effect of any of the fixes above) — see
`docs/validation/protwarrior_loo_cv_vanguard_2026_07_22.md`.

---

## `calibration_tier` — `placeholder` / `characterized` / `calibrated`

A per-spec field in `constants.yaml` (`specs.<spec>.calibration_tier`). It
replaced an older `calibrated: true/false` boolean (Top-5 #4, PR #289,
2026-07-06) — a spec with real log-characterization work (bugs found and
fixed against real data, gap direction and size documented) reads very
differently from a spec nobody has ever run a real log through, but a
boolean collapsed both cases to `false`. See `core/constants.py`'s
`CALIBRATION_TIERS` and `spec_is_calibrated()` for the authoritative
definitions.

- **`placeholder`** — default/estimated constants; not yet checked against
  any real log.
- **`characterized`** — real log-characterization work has happened
  (promotion from `placeholder` needs ≥2 real logs at canonical K, per-run
  deltas published in a `docs/validation/` doc, dual-validator run). We know
  where the model is off and roughly by how much, but the aggregate fit
  doesn't yet clear the `calibrated` bar. **This is not "broken"** — it's "we
  know the gap, but won't claim trust we haven't earned with data yet." The
  UI surfaces the caveats honestly for every spec at this tier.
- **`calibrated`** — the top tier. Promotion from `characterized` requires
  clearing ALL of: ≥8 F-consistent runs (see `scripts/measure_run_f.py`),
  |mean signed bias| ≤5%, ≥75% of runs within ±15%, RMSE ≤0.15, a passing
  LOO-CV (leave-one-out cross-validation) gate — implemented once in
  `scripts/calibrate_spec_from_logs.py::_run_loo_cv`, and reused (not
  reimplemented) by `simf calibrate-k` itself since 2026-07-22 (PR #416), so
  the gate always runs against whichever corpus that command resolved (the
  ratified manifest by default) rather than a second, possibly-different
  corpus — AND (added 2026-07-25, human-ratified, see below) a passing
  **cross-player validation gate**: ≥5 independent players' WCL fights run
  through `scripts/cross_player_validation.py` at the spec's canonical K,
  with |mean signed bias| ≤8% and ≥70% of players within ±15% — looser than
  the same-player bar above, since a cross-player corpus is one fight per
  player with no repeated same-player runs to average per-pull noise out of.
  Only the top tier means K, the spec's mitigation constants, and its policy
  dispatch are log-anchored well enough, AND shown to generalize across
  independent players, to trust the absolute numbers unconditionally.

**As of 2026-07-25, no modeled spec holds `calibrated` — all 6 sit at
`characterized`.** Prot Warrior and Guardian Druid have each held
`calibrated` at different points and were both downgraded from real,
data-driven investigations (not mystery regressions):

- **Prot Warrior** — `calibrated` through 2026-07-17 on what turned out to be
  a falsely-good RMSE 0.068 (see the RMSE section above: two double-counting
  bugs netting to ~zero). Downgraded 2026-07-18. Widened further to
  `characterized` at RMSE 0.162 (mean bias +13.7%, 8/16 within ±15%) after
  two real Shield Block fixes (2026-07-21) — then Vanguard, a
  previously-unmodelled spec passive, closed nearly all of it the same day
  (RMSE 0.073, mean bias +1.8%, 16/16 within ±15%). Re-promoted to
  `calibrated` 2026-07-22 once the LOO-CV gate — then the 4th and last
  promotion criterion — PASSED (K stability + 16/16 held-out folds within
  ±15%, the tightest LOO-CV result this project has measured for any spec).
  See `docs/validation/protwarrior_loo_cv_vanguard_2026_07_22.md`. **Downgraded
  again 2026-07-25**, three days later — the first time any spec's promotion
  bar was checked against players other than the one it was calibrated on.
  15 independent Prot Warrior players (found via WCL `characterRankings`),
  run through the identical pipeline at the identical K=3430, fail the same
  bar Brutoh's corpus cleared: mean bias +11.0% (bar ≤5%), 67% within ±15%
  (bar ≥75%), RMSE 0.150 (bar ≤0.15, exactly on the line). Two rival
  explanations (a WCL-hydrate data artifact; a key-level-driven effect) were
  checked and ruled out. `global_rmse` was 0.073 (unchanged by the downgrade
  itself) — this is a scope finding, not a regression: the Brutoh-only fit
  is still excellent, it just doesn't transfer to other players' gear and
  builds. See `docs/validation/protwarrior_calibrated_downgrade_2026_07_25.md`.
  **Separately, later the same day, KYFOTG was merged** (a Mountain Thane
  hero-talent proc, real and sourced two independent ways, held unmerged
  since 2026-07-22 pending this exact re-measurement) — `global_rmse` moved
  0.073→0.080, mean bias flipped +1.8%→−4.4%, within ±15% 16/16→14/16, but
  every same-player promotion-bar gate still clears (LOO-CV still PASSES).
  This is unrelated to why the tier stays `characterized`: the cross-player
  gate above is the sole blocker either way, since the independent-player
  corpus was already measured with KYFOTG live. See
  `docs/validation/protwarrior_kyfotg_wide_archive_2026_07_25.md`. The two
  caveats named at the 2026-07-22 promotion are still open regardless of
  tier: a cross-spec ~5-7% run-scoped wedge (affects other specs too) and a
  separate ~11% magic-dominated residual — KYFOTG's own `resid_out` shows
  this specific residual reads as diffuse (no single aura/ability correlate),
  likely the same wedge, not a second findable passive like Vanguard was.
- **Guardian Druid** — `calibrated` on 2026-06-29 (AnonGuardian1 16-log
  single-player corpus: unbiased mean +0.2%, 12/16 within ±15%, RMSE 0.119).
  Downgraded 2026-07-17 when the LOO-CV gate above — added 2026-07-06, with
  Guardian's tier grandfathered past it at the time — was actually run
  against the real 17-run corpus and failed at both scopes checked (full
  corpus 12/17 = 71%, F-consistent subset 9/13 = 69%; both under the ≥75%
  bar, and the shortfall isn't explained away by Guardian's own known
  F-outlier runs). The underlying aggregate fit (RMSE 0.119, unbiased) is
  **unchanged** — this reflects the project's own cross-validation standard
  not being cleared yet, not a regression in the fit itself. See
  `docs/validation/phase4_guardian_loo_cv_f_consistent_2026_07_08.md`.

The other four modeled specs (Brewmaster Monk, Prot Paladin, Blood DK,
Vengeance DH) have each had at least one characterization pass of their own,
with named, documented gaps — see `docs/validation/` for each spec.

---

## Where simf's survivability metric comes from

simf's core metric family (`m_plus_tmi`, `tmi_12`, `etmi_12`, ...) is modeled
on Theck's TMI (Theck-Meloree Index) — a tank-community metric that scores a
simulated pull's damage-taken distribution rather than a single point value,
so death-adjacent spikes count against a build even when the mean damage
taken looks fine. This lineage matters because of what's happened to it
upstream: SimulationCraft removed the TMI/ETMI metric family from its own
engine in 2024 (PR #8811) — maintainer attrition on the tank-specific code
path, not a finding that the metric was wrong. Talent-leftovers deep-review
research (2026-07-11) confirmed no other free tool has picked it back up:
Wowhead's talent calculator computes nothing, Raidbots ranks every spec
(including tanks) through its shared DPS pipeline, and Murlok.io/Archon/WCL
show popularity or throughput with no survival framing at all. That's the
direct, dated basis for this project's "only free tank survivability sim"
claim — not an assertion, a gap other tools left open when they walked away
from this exact metric family.

---

## Related constants you'll see alongside K

- **`hp_per_stamina: 22`** — max HP is linear in stamina at this slope (the
  stamina→HP anchor used in calibration).
- **`max_armor_dr: 0.85`** — the 85% hard cap on armor DR, matching SimC's
  `MAX_ARMOR_DAMAGE_REDUCTION`.
- **`stat_conversion`** — rating→percent conversions (e.g. haste 44, crit 46,
  versatility 54 rating per 1% in 12.0.7). These pair with the
  `secondary_dr` diminishing-returns breakpoints.

---

## TL;DR

| Term               | What it is                                                                 |
|--------------------|----------------------------------------------------------------------------|
| **K** (`k_constant`) | Armor constant in `DR = Armor / (Armor + K)`. **3430**, from SimC's DBC table at level 90. Higher K = armor worth less. |
| **K=2700 (old)**   | A retired self-fit that hid missing Defensive Stance mitigation; superseded by the honest 3430. |
| **`calibrate-k`**  | CLI that sweeps K against real logs. We keep 3430 on methodology grounds (K is SimC's real DBC constant, not a free parameter) even though the post-2026-07-18 empirical best (~2905-2930) now sits ~500 away, well outside the old "stay close" band. |
| **RMSE**           | Fit-quality score vs ground truth. **0.080** for Prot Warrior's Brutoh-only corpus as of the 2026-07-25 KYFOTG merge (was 0.073, was 0.162, was 0.138, was 0.068 — each move widened or narrowed the gap by fixing a real bug or crediting a real mechanic, never a free re-fit). Lower = better. |
| **`calibration_tier`** | Per-spec 3-tier trust field: `placeholder` → `characterized` → `calibrated`. As of 2026-07-25, all 6 modeled specs sit at `characterized`; none is at `calibrated` (Prot Warrior and Guardian Druid each held it earlier and were both downgraded on real findings, not regressions — Prot Warrior's latest downgrade is the first from a cross-player generalization check, not a same-corpus one). |
