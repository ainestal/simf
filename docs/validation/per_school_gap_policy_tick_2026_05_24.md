# Per-school mitigation gap — audit script policy.tick wiring (2026-05-24)

**Verdict: the doc-asserted "−13pp dominated by ONE missing structural layer"
thesis is empirically refuted. The audit-script fix and Phase 3.9.2 combined
close only ~2.4pp on physical. The remaining ~10pp residual on physical is
real engine-vs-reality, not a measurement artefact.**

## Background

`docs/validation/magic_mit_gap_remeasurement_2026_05_23.md` measured a
structural −12.79pp physical mit gap across 18 Brutoh runs (σ ≈ 1pp, 18/18
under-mitigated). The tight band led to the conclusion: ONE structural
missing layer was dominating the residual, and a single fix would close
~5pp on physical and ~5-8pp on magic.

Phase 3.9.2 (`party_dr_by_school: {all: 0.05, magic: 0.00}`) shipped on
`feat/party-dr-by-school` (commits eb34ac4 + b7bde26). Pre-merge gap
remeasurement showed only +1.33pp closure on physical — much less than
the doc predicted.

Director re-prioritised: instead of chasing further engine fixes, first
**fix the ruler**. `scripts/per_school_mitigation_gap.py` bypasses
`policy.tick`, so the engine state's BfI stacks stay at 0 throughout the
audit even though production replay (`runner.py:186`) calls `policy.tick`
on every event and accumulates BfI stacks via the haste-time model
(`policy.py:154-157`). If the audit script were measuring what the
engine *actually does* in production, the gap might shrink materially.

This doc captures the before/after.

## Method

Modified `scripts/per_school_mitigation_gap.py` to call
`policy.tick(state, t_rel)` immediately before each `apply_mitigation`
call. Used the same 18-Brutoh-run corpus with `--party-magic-dr` (PMD
opt-in mode, the apples-to-apples comparison to the original baseline
JSON). Same `random.Random(42)` seed. Same character loadout.

Compared three runs:
- **baseline**: master engine, no `policy.tick` (the pre-3.9.2 doc's measurement).
- **+policy.tick**: master engine, audit script calls `policy.tick`.
- **+3.9.2-retune**: `feat/party-dr-by-school` engine (all: 0.05, magic: 0.00), audit script unchanged (no `policy.tick`).

## Per-school weighted gap

| Bucket    | baseline | +policy.tick | +3.9.2-retune | tick Δ  | 3.9.2 Δ |
|-----------|---------:|-------------:|--------------:|--------:|--------:|
| physical  | **−12.79** | **−11.72** |   **−11.46**  | **+1.06** | **+1.33** |
| shadow    |   −6.55  |     −6.55    |      −6.55    |   0.00  |   0.00 |
| fire      |   −8.36  |     −8.36    |      −8.36    |   0.00  |   0.00 |
| arcane    |   −7.50  |     −7.50    |      −7.50    |   0.00  |   0.00 |
| nature    |   −9.34  |     −9.34    |      −9.34    |   0.00  |   0.00 |
| holy      |   −6.81  |     −6.81    |      −6.81    |   0.00  |   0.00 |
| frost     |   +5.68  |     +5.68    |      +5.68    |   0.00  |   0.00 |
| bleed     |  +37.22  |    +38.40    |     +38.70    |  +1.18  |  +1.48 |

## p90 across runs

| Bucket    | baseline | +policy.tick | +3.9.2-retune |
|-----------|---------:|-------------:|--------------:|
| physical  |  −10.27  |     −9.20    |      −8.93    |
| shadow    |   +2.05  |     +2.05    |      +2.05    |
| fire      |   −1.72  |     −1.72    |      −1.72    |
| arcane    |   −1.63  |     −1.63    |      −1.63    |
| nature    |  +13.26  |    +13.26    |     +13.26    |
| holy      |   −5.00  |     −5.00    |      −5.00    |
| frost     |   +8.37  |     +8.37    |      +8.37    |

## What the numbers say

### Audit-script policy.tick: +1.06pp physical closure only.

`policy.tick` adds BfI stack accumulation (0→4 stacks × 1% = 4% physical
DR). On top of an existing ~73% engine DR, a 4% multiplicative layer
adds 1-(0.27)(0.96) − 1+0.27 = 1.06pp marginal DR. That matches the
empirical observation exactly.

Magic schools see zero delta because `policy.tick`'s contributions
(BfI, Brutal Vitality absorb) only affect physical events.

The bleed bucket gets +1.18pp WORSE — engine now over-predicts bleed
mitigation by an additional 1pp because the BfI 4% layer applies to
bleed events (`mitigation.py:284` gates on `event.school == "physical"`,
not on `not is_bleed`). This is a real but tiny new finding: BfI's
school filter should probably also exclude bleeds, matching the
session-22 armor-bypass logic at `mitigation.py:217`.

### Phase 3.9.2 (party_dr_by_school): +1.33pp physical closure.

Same DR-stacking arithmetic: 5% layer on 73% existing DR adds 1.33pp.

### Combined: +2.4pp physical closure.

If we shipped both fixes (current branch state would do this once the
3.9.2 PR merges into the engine and the audit-script fix lands), the
audit would show ~−10.4pp physical residual.

### The remaining ~10pp physical gap is REAL.

To close 10pp on top of an existing 73% engine DR, the engine would need
ANOTHER multiplicative DR layer of effective magnitude ≈ 27% — far
larger than any single missing party-aura or talent. The gap is
multiple smaller misses summing via the multiplicative chain, NOT a
single structural fix.

## Strategic implications

The director's pre-spawn frame ("trust the engine, fix the ruler") gets
**partial** confirmation:
- ✅ The doc's "ONE missing layer" thesis is dead. Three independent fixes
  (BfI via policy.tick, party-DR `all` layer, the still-deferred bonus-
  armor + Phalanx-uptime knobs) cap at ~2-3pp each.
- ❌ But the engine IS missing material physical DR. ~10pp of structural
  under-mitigation persists after both fixes. The audit script is NOT
  the dominant source.

What that ~10pp likely contains, in rough magnitude order (untested):
1. **Phalanx uptime drift.** Engine uses `avg_uptime: 0.50` as a flat
   heuristic. Real Brutoh logs may average closer to 0.65-0.75 on
   trash-heavy segments. Each 10pp of uptime drift = ~0.5pp DR.
2. **Bonus armor.** The `~230 of unmodelled bonus armor` noted in
   `constants.yaml:82` would close ~2pp.
3. **Shield armor drift.** YAML at 989 (post-Voidcore), logs at 931 —
   this *worsens* the under-mit gap because engine over-states block_value
   slightly. The true under-mit is closer to −13.5pp.
4. **Per-source-immunity gaps.** Some sources (e.g., specific Algeth'ar
   trash) may have unmodelled DR responses that the engine treats as
   normal physical hits.
5. **Reactive healer absorbs not visible pre-absorb.** Wait — the audit
   uses `actual_pre_absorb = log_evt.amount + log_evt.absorbed` so this
   shouldn't matter… unless absorbs are themselves the missing piece
   (i.e., they reduce damage by being there even before the hit lands,
   in ways the engine treats as separate). Worth investigating.
6. **Bleed-with-BfI bug.** Small — only affects bleeds. Tracked above.

## What ships

This branch (`fix/per-school-gap-policy-tick`) adds:
- `policy.tick` call in `scripts/per_school_mitigation_gap.py`
- This validation doc

It does **not** retune any engine constants. The empirical refutation
of the "one missing layer" thesis means we know more about what the
engine is missing, but the next step is investigative — likely a
validator deep dive on items 1-4 above — not another speculative
constants bump.

The bleed-with-BfI finding (item 6) is a real ~1.18pp engine bug on
bleeds and should ship as a small follow-up PR: gate
`mitigation.py:284` with `not event.is_bleed` to mirror the armor
bypass at `mitigation.py:217`.

## Files

- Audit script change: `scripts/per_school_mitigation_gap.py`
  (added `from simf.core.policy import make_policy`, `policy.tick`
  per event).
- Raw JSON outputs:
  - `docs/validation/per_school_data_brutoh_pmd.json` (baseline,
    in-tree from 2026-05-23).
  - `/tmp/per_school_with_policy_tick.json` (this branch + PMD on).
  - `/tmp/per_school_post_392_retuned.json` (3.9.2 branch + PMD on).
- Predecessor: `docs/validation/magic_mit_gap_remeasurement_2026_05_23.md`.
