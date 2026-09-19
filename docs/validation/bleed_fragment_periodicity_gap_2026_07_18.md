# Bleed-fragment false positives on direct hits — fixed, K held at 3430 (2026-07-18)

**Status: fix implemented, tests + full suite green, dual-validator audit requested before merge**
(calibration-affecting change on both currently-`characterized` flagship corpora — Warrior and
Guardian — per `feedback_dual_validator_pre_merge_audit`).

## Summary

`core/bleed_detection.py`'s `is_bleed()` flags an event as a bleed (WoW's armor-bypass mechanic)
by spell-name fragment match only (`"Rend"`, `"Rake"`, `"Rip"`, ...). Several abilities that
fragment-match a bleed name **also have a direct, non-periodic damage component under the same
displayed name** — a boss's direct "Searing Rend"/"Rending Gore" swing alongside its own DoT
tick of the same name, or (in principle) Rake's initial strike. Production's two log-replay call
sites (`io/log_replay.py`, `io/wcl_replay.py`) set `is_bleed=is_bleed(spell_name)` with **no gate
on periodicity at all** — so a one-time direct hit sharing a bleed's name fragment was skipping
armor DR it should not have, inflating the model's predicted damage for that hit.

This is not a new bug class: the Blood DK characterization doc
(`phase4_blood_dk_characterization_2026_07_03.md`) already forensically pinned Player 1's
entire "bleed" residual (26.2M base damage, the sole cause of that corpus's one out-of-band run)
to Searing Rend, calling it "an armor-affected physical DoT" and filing "reclassifying touches
the calibrated Warrior + Guardian corpora" as a cross-spec follow-up. That follow-up is this fix
— except the root cause turned out to be sharper than "one mislabeled ability": Searing Rend's
actual periodic tick is **holy-school**, not physical, so it was never the tick that leaked
through the old `is_dot`-gated audit script (`scripts/per_school_mitigation_gap.py`, unaffected
by this bug). It was Searing Rend's **direct, non-periodic hit** (school=physical,
`SPELL_DAMAGE`, not `SPELL_PERIODIC_DAMAGE`) that production's *ungated* `is_bleed()` call
mislabeled — a distinct, previously-undocumented mechanism from what the original doc assumed.

## Discovery

Per-ability forensics (mirroring the Blood DK doc's method: compare an ability's real
`(amount+absorbed)/base_amount` mitigation% against the same fight's ordinary physical
armor-chain mitigation%) restricted to the two ratified local-log calibration corpora — Prot
Warrior's 16-log manifest (`data/calibration_corpora/prot_warrior_2026_05.yaml`) and Guardian
Druid's 17-run AnonGuardian1 corpus (`examples/anonguardian1-guardian/`) — surfaced every event matching a
`BLEED_SPELL_FRAGMENTS` entry, split by whether the event was periodic:

| spell (event type) | n (hit events) | real DR% | fight's physical armor-chain DR% | verdict |
|---|---|---|---|---|
| Searing Rend `[SPELL_DAMAGE]` | 81 | 79.0% | 81.9% | **false positive** — armor-mitigated |
| Rending Gore `[SPELL_DAMAGE]` | 8 | 89.0% | 84.9% | **false positive** — armor-mitigated |
| Mirrored Rend `[SPELL_DAMAGE]` | 2 | 75.7% | 78.9% | **false positive** — armor-mitigated |
| Open Wound `[DOT]` | 99 | 33.9% | 84.9% | true bleed — correctly classified already |
| Rending Gore `[DOT]` | 102 | 30.8% | 84.9% | true bleed — correctly classified already |

`n` counts actual damage-dealing hit events (`SPELL_DAMAGE`/`SPELL_PERIODIC_DAMAGE`) aggregated
across both corpora — the number the DR%/false-positive verdict is computed from. It is NOT the
same count as a raw `grep -c "Searing Rend"` over one log file (295 in
`WoWCombatLog-051026_073906.txt` alone), which also matches `SPELL_CAST_START`,
`SPELL_CAST_SUCCESS`, `SPELL_AURA_APPLIED[_DOSE]`, `SPELL_AURA_REMOVED`, `SPELL_ABSORBED`, and
`SPELL_MISSED` lines for the SAME ability across every event type — a raw text count, not a
hit-event count.

Every direct (`SPELL_DAMAGE`) hit's real mitigation tracks its fight's ordinary physical armor
chain to within ~3pp — indistinguishable from non-bleed physical. Every periodic (`SPELL_PERIODIC_DAMAGE`)
tick shows the ~50pp armor-bypass signature a true bleed should. WoW has no mechanic for a
one-shot bleed; the fragment match alone was never sufficient.

Direct confirmation Searing Rend's periodic tick is genuinely non-physical: raw log inspection
(`examples/WoWCombatLog-051026_073906.txt`) shows spell id 1257745/1255335 ("Searing Rend",
`SPELL_PERIODIC_DAMAGE`) carrying school flag `0x2` (holy), while spell id 1257736/1255208 (same
name, `SPELL_DAMAGE`, direct hit) carries `0x1` (physical) — two different spell IDs sharing one
display name, one mechanic each. Independently corroborated by `io/wcl_api.py`'s own 2026-05-27
live-probe comment, which names Searing Rend as one of the abilities WCL's `tick: true` field
correctly flagged only on its periodic component.

## The fix

`core/bleed_detection.py::is_bleed()` gains a required `is_periodic: bool` parameter (no
default, so a new call site can't silently reintroduce the gap) — a non-periodic event always
returns `False` regardless of name. Every production call site now passes the real signal:

- `io/log_replay.py`: `event_type == "SPELL_PERIODIC_DAMAGE"`
- `io/wcl_replay.py`: `dte.tick_flag` (WCL's own periodicity field — `event_type` stays generic
  there, per `io/wcl_api.py`'s existing comment; `tick_flag` is the only reliable signal)
- `scripts/measure_run_f.py` (the F-factor gate feeding LOO-CV corpus admission): same
  `event_type` check
- `core/coaching.py`'s injected `is_bleed_fn` (the hit-vs-coverage join, gates whether an
  armor/parry-scoped lever can "cover" a hit): a new `core.bleed_detection.event_is_periodic()`
  helper derives periodicity from either signal uniformly, used at every UI/coaching call site
  that has a real per-event object available
- Cosmetic-only badge call sites with no single real event to read from (aggregated
  "top abilities across this pull" tables, a kill-blow spell name reduced to a display string
  upstream) keep `is_periodic=True` — same name-only behavior as before, explicitly commented as
  a deliberate simplification since they don't feed the sim
- `scripts/per_school_mitigation_gap.py` had its own **stale duplicate** of
  `BLEED_SPELL_FRAGMENTS`/`is_bleed()` (contradicting the module's own docstring claim that this
  script "re-uses" the canonical helper) — replaced with a real import; its own `is_dot` gate
  was already correct, so this is a drift-prevention fix, not a behavior change for that script

New regression tests pin the periodicity gate at every layer: `is_bleed(frag, is_periodic=False)`
is `False` for every fragment in the allowlist; a synthetic direct "Rend"-named
`SPELL_DAMAGE` hit still runs the armor curve in `measure_run_f`; a direct `Rake` WCL event
(`tick_flag=False`) yields `is_bleed=False` while its periodic sibling (`tick_flag=True`) yields
`True`; source-level assertions confirm both `log_replay.py` and `wcl_replay.py` wire the gate,
not just the call. Full suite: 2802 passed (was 2802 before this change — same count, no tests
removed/added net beyond the ones covering this fix). Zero new mypy errors (28 pre-existing on
master, unchanged).

## Measurement — clean before/after, same manifest, same seed, `simf calibrate-k` / `calibrate_spec_from_logs.py`

Both runs below use identical iteration counts and seed against unmodified master (`git stash`)
vs. this fix, so the delta is attributable to the code change alone, not sampling noise.

### Prot Warrior — ratified 16-log manifest, pinned canonical K=3430, 2000 iterations, seed 42

| | RMSE @ K=3430 | mean signed error | within ±15% |
|---|---|---|---|
| **Before fix** | 0.1377 (0.1362 excl. 2 partials, n=14) | +11.44% (over-predict) | 10/16 |
| **After fix** | 0.1339 (0.1318 excl. partials) | +10.94% | 10/16 |

Exactly 3 of 16 runs changed (all others bit-identical): **Nexus-Point Xenas +12** (the run with
295 Searing Rend hits) +13.1% → **+7.0%** (−6.1pp); Maisara Caverns +12, +11.9% → +10.4%
(−1.5pp); Maisara Caverns +14, +15.9% → +15.6% (−0.3pp). A modest but real, correctly-directed
improvement — only 3/16 runs contained the affected abilities, and even within those runs the
affected damage was a fraction of total DTPS. The within-±15% count is unchanged (the improved
Nexus-Point run was already inside the band; no run's pass/fail status flipped for Warrior).

### Guardian Druid — AnonGuardian1's 17-timed-run corpus (`examples/anonguardian1-guardian/`), canonical K=3430, 300 iterations, seed 42

| | RMSE @ K=3430 | full-corpus LOO-CV | F-consistent-subset LOO-CV |
|---|---|---|---|
| **Before fix** | 0.1183 | **12/17 (71%) — FAIL** | **9/13 (69%) — FAIL** |
| **After fix** | 0.1189 | **13/17 (76%) — PASS** | **10/13 (77%) — PASS** |

RMSE moved negligibly (0.1183→0.1189, within noise) — but **both LOO-CV gates flip from FAIL to
PASS**. This is the exact gate CONTRIBUTING.md names as the reason Guardian was downgraded from
`calibrated` to `characterized` on 2026-07-17. Mechanism: 4 runs changed at the canonical-K
level (both Nexus-Point Xenas pulls, +8.9%→+0.9% and −5.8%→−13.2%; both Maisara Caverns pulls,
marginally). One LOO-CV fold flips pass/fail as a second-order consequence: with fewer corrupted
runs, the per-fold best-fit K search lands on 3430 instead of 3250 for the
`Algeth'ar Academy +12 (062026_17[0])` fold, moving its held-out delta from −15.3% (FAIL) to
−14.0% (PASS) — the same "K relocates to the interior/canonical value" quality signal the Blood
DK doc treated as evidence of a real mechanism fix, not a coincidence.

**This does NOT flip Guardian back to `calibrated`.** The plain "all runs within ±15%" verdict
stays `False` before and after — two outlier runs untouched by this bug (Maisara Caverns +15,
Magisters' Terrace +15, both ~+23-25% at canonical K) remain far outside band, and
`core.constants.spec_is_calibrated`'s bar requires the ±15% verdict AND the LOO-CV gate AND
≥8 F-consistent runs, not the LOO-CV gate alone. `calibration_tier` is left unchanged
(`characterized`) — a tier promotion is a headline-affecting decision on its own, deliberately
not bundled into this fix's PR (same precedent as PR #361/the Demo Shout fix: the user sees a
calibration-tier change before it ships, not as a side effect of an unrelated bug fix). Filed as
a fresh, positive lead for whoever next revisits Guardian's tier: the LOO-CV blocker specifically
named in the 2026-07-17 downgrade doc is resolved; what remains is the 2 outlier runs' own
unexplained gap (consistent with the existing "single-build haste slope + medium-confidence
mastery + small magic residual" caveats — unchanged, not re-investigated here).

### Blood Death Knight — NOT independently re-run this session

The original characterization (`phase4_blood_dk_characterization_2026_07_03.md`) sourced its
5-fight corpus from public WCL reports; the exact report codes / fight IDs / source IDs were
never persisted to a manifest (unlike Warrior's `data/calibration_corpora/*.yaml`), so an
apples-to-apples re-run isn't possible without rediscovering the same fights. Rather than guess
or substitute a different corpus, this is named as a gap, not silently skipped. The original doc
already forensically isolated Player 1's entire out-of-band residual to Searing Rend and
pre-computed this fix's expected effect from first principles: "Est. effect here: ~+15.5M of
Player 1's +51M gap (their pre-absorb would drop to roughly +12%)" — i.e., closing 4/5 → likely
5/5 within ±15% and clearing Blood DK's last named blocker short of the multi-hero-talent-build
caveat. That is a forward-looking estimate from the original session, not a number this session
re-verified against fresh data.

### VDH / Brewmaster / Protection Paladin — not checked this session

The fix is spec-agnostic (both replay paths are shared across all specs), so any corpus
containing a trash mob with a similarly-named direct-hit-plus-DoT ability could see the same
class of improvement. Neither those three specs' existing characterization corpora nor any new
WCL fights were re-scanned for this fragment-collision pattern this session — named as
unexplored scope, not asserted clean.

## Why this stays a fix, not a re-tier

Per `feedback_engine_batch_ratification` — this PR is scoped to the bug fix and its *measured*
consequences on the two corpora cheap to re-run locally. It deliberately does not: (a) chase
down Blood DK's WCL fight identifiers to force an independent re-measurement, (b) re-scan
VDH/Brewmaster/ProtPal, or (c) flip Guardian's `calibration_tier` despite its LOO-CV gate now
passing. Each of those is a legitimate, named follow-up for a dedicated session, not a scope-creep
risk worth absorbing into a bug-fix PR that's already touching two replay paths, one coaching
module, five UI badge call sites, and one dataclass.
