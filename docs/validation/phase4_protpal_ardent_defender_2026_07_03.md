# Ardent Defender parameter fix + a 4th Bruttah log (2026-07-03)

Same-day follow-up to `phase4_protpal_block_value_2026_07_03.md` (F15, the
block-value armor-curve port). That doc's own "Next steps" named two open
items this entry closes one of: "more Bruttah logs at higher key levels" and
"confirm or refute the Ardent Defender parameter mismatch... against a second
source." A 4th log landed same-day (`WoWCombatLog-070326_191813.txt`, Seat of
the Triumvirate +9) — the highest key in the corpus by a wide margin — and it
carries 12 clean Ardent Defender activations, clean enough to settle the
question outright.

## TL;DR

- **Ardent Defender was wrong on all three axes.** Modeled `20% DR / 8s / 120s
  CD`; correct values are `30% DR / 12s / 90s CD` (spell 31850). Fixed in
  `constants.yaml` (`constants_version` 36→37) and in `cooldown_planner.py`'s
  duplicate `LONG_CD_BUTTONS` registry, which would otherwise have kept
  planning around the old numbers.
- **Confirmed by four independent sources**, not one: (1) 12/12 clean
  AURA_APPLIED→REMOVED log samples all at 12.00s±0.02s, (2) a real recast at
  71.7s and a `SPELL_CAST_FAILED "Not yet recovered"` at 61.8s that falsify
  the old 120s CD outright, (3) Wowhead's live tooltip (30%/12s/90s), (4) an
  independent validator pull of SimC's `midnight`-branch source
  (`sc_paladin_protection.cpp`), which sources the buff from spell 31850's own
  DBC data and agrees. A 5th, internal signal: `constants.yaml`'s own coaching
  registry already had `cooldown_s: 90` for this spell — the mitigation model
  and the coaching feature were quietly contradicting each other.
- **The 4-log corpus reveals the gap grows with key level, not just
  persists.** Pre-fix deltas at canonical K=3430: +19.8% / +4.3% / +31.5% /
  **+55.4%** (Pit of Saron +2 / Algeth'ar Academy +2 / Magisters' Terrace +5 /
  Seat of the Triumvirate +9). RMSE 0.216→0.335 with the 4th log added. This
  is a more serious read than the 3-log doc's "small, plausibly-explainable
  shortfall" — the worst residual sits exactly where the product's +14-18
  verdict target extrapolates into.
- **Still `calibrated: false` after the fix, and not close.** Constants-only
  (what actually shipped): +19.8% / +1.7% / +29.7% / +47.5%, RMSE 0.297 — an
  improvement, but 3 of 4 runs remain far outside ±15%. The AD fix narrows and
  reshapes the gap; it doesn't close it. Next-leverage lead: a **holy-power
  economy shortfall** (below), ahead of the previously-named Divine Bulwark
  spell-block chance and blocked-DoT absorb.
- **A real, separate bug found and deliberately NOT fixed here**: the
  cheat-death grant in `runner.py:294-304` checks only whether Ardent
  Defender's cooldown is ready, never whether its buff window is actually
  active. SimC gates the death-save on `buffs.ardent_defender->check()`
  (buff-active), not cooldown-ready. This systematically **under-counts
  deaths** for Protection Paladin — the sim will free-revive a Paladin who
  never pressed AD at all, as long as the cooldown happens to be up. Flagged
  as a dedicated follow-up; shortening the CD 120s→90s in this fix makes the
  wrong path fire ~33% more often, so it should be picked up before any
  ProtPal death-rate/verdict work leans on this surface.

## The new log

`examples/bruttah-prot/WoWCombatLog-070326_191813.txt` — Seat of the
Triumvirate +9, timed, tank=Bruttah-Uldum-EU/protection_paladin, confirmed via
`scripts/calibrate_spec_from_logs.py inventory --logs-dir examples/bruttah-prot`.
A 5th file in the same directory (`WoWCombatLog-070326_074225.txt`) remains
excluded — different character (Brutoh, Protection Warrior), incomplete —
same as the original 3-log doc already noted.

## Ardent Defender forensics

Raw log evidence (spell 31850, "Ardent Defender", on Bruttah), 12
AURA_APPLIED→AURA_REMOVED pairs across the run:

| # | Applied | Removed | Duration |
|---|---|---|---:|
| 1 | 19:19:58.094 | 19:20:10.118 | 12.024s |
| 2 | 19:21:46.703 | 19:21:58.721 | 12.018s |
| 3 | 19:26:11.980 | 19:26:23.986 | 12.006s |
| 4 | 19:27:43.427 | 19:27:55.427 | 12.000s |
| 5 | 19:29:04.347 | 19:29:16.340 | 11.993s |
| 6 | 19:30:41.236 | 19:30:53.236 | 12.000s |
| 7 | 19:32:10.322 | 19:32:22.326 | 12.004s |
| 8 | 19:33:21.984 | 19:33:33.991 | 12.007s |
| 9 | 19:35:21.428 | 19:35:33.446 | 12.018s |
| 10 | 19:36:42.014 | 19:36:54.001 | 11.987s |
| 11 | 19:38:05.159 | 19:38:17.154 | 11.995s |
| 12 | 19:43:08.592 | 19:43:20.604 | 12.012s |

Every sample lands at 12.00s ± 0.02s — about as clean as real telemetry gets.
This alone falsifies the modeled `ardent_defender_duration_s: 8.0`.

Cast-to-cast gaps between the same 12 casts: 108.6s, 265.3s, 91.4s, 80.9s,
96.9s, 89.1s, 71.7s, 119.4s, 80.6s, 83.1s, 303.4s. Several (71.7s, 80.6s,
80.9s) are shorter than both the modeled 120s **and** Wowhead's base 90s —
which would be impossible under a flat cooldown. A `SPELL_CAST_FAILED "Not yet
recovered"` line at 61.78s after a prior cast, followed by a successful
recast at 71.66s, brackets the *effective* cooldown to (61.8s, 71.7s].
Wowhead's Unbreakable Spirit (spell 114154) talent lists Ardent Defender among
the abilities it cuts by 30% — 90s × 0.7 = 63.0s, matching the bracket almost
exactly. Bruttah's COMBATANT_INFO carries trait-entry IDs this codebase can't
decode into named talents, so Unbreakable Spirit is inferred from telemetry +
spell data, not read directly off her talent string — treat it as
well-corroborated, not certain.

**Base values only were shipped.** `ardent_defender_cooldown_s: 90.0` is the
un-talented base; Unbreakable Spirit's further reduction is talent-gated and
NOT modeled — filed as a named follow-up, not baked into the base constant.

DR magnitude (20% modeled vs. 30% tooltip) isn't independently checkable from
this log alone as cleanly as duration/cooldown — a rough in-window vs.
out-of-window magic-hit ratio landed near 0.70 (matching 30%) but is
confounded by other cooldowns firing in the same windows, so it's
corroboration, not proof. Wowhead's tooltip and a validator's independent
pull of SimC's `midnight`-branch spell data (`sc_paladin_protection.cpp`,
which sources the buff's magnitude straight from spell 31850's DBC data
rather than a literal) both agree on 30%.

## Calibration: 4-log corpus, canonical K=3430, 300 iterations

| Run | real_dtps | delta, pre-fix | delta, post-fix (constants only, independently reproduced) |
|---|---:|---:|---:|
| Pit of Saron +2 | 16,634 | +19.8% | +19.8% |
| Algeth'ar Academy +2 | 18,599 | +4.3% | +1.6% |
| Magisters' Terrace +5 | 21,220 | +31.5% | +29.7% |
| Seat of the Triumvirate +9 | 33,540 | +55.4% | +47.5% |

RMSE: 0.335 pre-fix → 0.2972 post-fix (300 iter, both figures independently
reproduced after the fix landed). Full post-fix K-sweep: best-K=2500
(RMSE=0.1230); canonical K=3430 is well up the RMSE curve from there (monotonic
increase from K=2500 to K=5000). `specs.protection_paladin.calibrated` **stays
`false`** — no run clears ±15% at canonical K, and the two hardest keys (+5,
+9) are still 30-48% off. The gap is not magic-shaped (the +9 run has the
*lowest* pre-mitigation magic share of the corpus, 7.4% vs 11-21% on the
others), so it isn't primarily the previously-named Divine Bulwark spell-block
/ blocked-DoT candidates — those remain real but likely smaller contributors
than what's below.

## The policy question — reactive vs. proactive, deliberately NOT shipped

`calibrate`'s replay drives the model's own **reactive** Ardent Defender
trigger (`protection_paladin.py:177`, presses at `hp/max_hp < 0.40`) against
real incoming damage. Bruttah's real play is **proactive** — she presses AD
on-cooldown regardless of HP (11-12 casts across every run in the corpus,
including the +2 keys where she's rarely near 40% HP). Measured on the
corpus at canonical K:

| Configuration | deltas | RMSE |
|---|---|---:|
| Pre-fix (20%/8s/120s, reactive) | +19.8 / +4.3 / +31.5 / +55.4 | 0.335 |
| **Shipped** (30%/12s/90s, reactive) | +19.8 / +1.7 / +29.7 / +47.5 | 0.297 |
| Constants + proactive on-CD press (NOT shipped) | +11.2 / −1.2 / +28.8 / +38.7 | 0.248 |

The proactive-policy row's press counts (10/11/13/14.4 per key) nearly match
Bruttah's real cast counts (11/11/10/12) — she presses on cooldown at every
key level, and that's the empirically faithful model of her play, not the
sim's own reactive heuristic. **Not shipped in this pass** because (a) it's a
behavioral policy change, not a parameter correction — bigger blast radius
than this PR's scope, (b) it risks overfitting the *policy* to one player's
personal habit (proactive-on-CD is a skill/style choice, not a universal
constant — this project already has a skill-ladder concept for exactly this
kind of play-pattern variation), and (c) it interacts with the deferred
Unbreakable Spirit talent: shipping both an assumed CDR *and* an unconditional
on-CD press would double-press relative to reality. Left as a named,
evidenced follow-up rather than folded in here.

## Two more things found, deliberately not fixed here

- **`runner.py:294-304`'s cheat-death gate** — see TL;DR above. Confirmed by
  an independent validator pass against SimC source
  (`buffs.ardent_defender->check()` gates the real death-save on the buff
  being *active*; simf's runner only checks the cooldown). Medium severity,
  systematically flatters ProtPal survivability, gets more frequent after
  this fix's CD reduction. Needs its own PR with its own before/after
  characterization — not a drive-by fix.
- **`cooldown_planner.py`'s `LONG_CD_BUTTONS` registry duplicates
  constants.yaml by hand** for every spec (not just ProtPal) and doesn't read
  the YAML — confirmed load-bearing (`consume_due_presses()` writes the
  window/cadence straight from this tuple when a Cooldown Planner plan is
  active). The ProtPal row is fixed to `(90.0, 12.0, ...)` in this PR so the
  Planner and the reactive heuristic agree; making the whole registry
  YAML-driven for all 6 specs is separable, larger-scope, pre-existing debt —
  named here, not attempted.

## New lead: holy-power economy

**FOLLOWED UP 2026-07-04 — the lead was real but this section's arithmetic
was built on a false premise.** The "~0.70 HP/s combined spend" figure summed
SotR *and* WoG at 3 Holy Power each; per-cast advanced power fields in the
same logs show Word of Glory costs **50,000 mana** for 93.5% of casts, with
only a rare (6.5%) hybrid draw also touching Holy Power, in Midnight 12.0.5
(pool-conservation-proven, 917/919 pre-cast pool readings — 909 SotR + 10
hybrid WoG). The real defect was structural: the *model* charged EVERY
WoG 3 Holy Power with first priority, starving SotR coverage in the low-HP
regime — reality's competition for the pool is negligible by comparison. See
`phase4_protpal_holy_power_economy_2026_07_04.md` for the full 4-log economy
extraction, the corrected constants (`holy_power_per_second_base` 0.55→0.46
from measured uptime, WoG moved to a measured mana pool,
`wog_heal_pct_of_max_hp` 0.30→0.18) and the post-fix calibration table.

Original text (kept for the record): Bruttah sustains ~86% Shield of the
Righteous uptime *and* 68 Word of Glory casts across the +9 run (~0.70 HP/s
spent) against the model's `holy_power_per_second_base: 0.55` (~0.60 at her
haste). In the sim's low-HP regime, WoG spending competes with SotR uptime
for Holy Power in a way that doesn't match her real resource economy — a
plausible next-leverage target, ahead of Divine Bulwark spell-block and the
blocked-DoT absorb named in the prior doc, given the residual isn't
magic-shaped.

## What was NOT done (named, not fixed)

- Unbreakable Spirit talent-gated Ardent Defender CD reduction.
- The proactive-vs-reactive Ardent Defender press policy.
- `runner.py`'s buff-window-blind cheat-death gate.
- `cooldown_planner.py`'s YAML-independent `LONG_CD_BUTTONS` registry, for
  the other 5 specs (only ProtPal's row was touched, to stay in sync with
  this fix).
- Divine Bulwark's spell-block chance, the blocked-DoT absorb, Sanctuary's
  Consecration-linger DR (carried over from the prior doc, still unmodeled).
- The holy-power economy shortfall named above.
- Re-running VDH's #143 characterization against the fixed
  `_select_combatant_info` (carried over from the prior doc, still cheap and
  still not done).

## Next steps

1. ~~Model the holy-power economy shortfall~~ DONE 2026-07-04 — turned out to
   be a structural currency error (WoG is mana-funded in Midnight and never
   competed with SotR for Holy Power), not just a rate shortfall. See
   `phase4_protpal_holy_power_economy_2026_07_04.md`.
2. `runner.py` cheat-death buff-window fix, as its own characterized PR.
3. Divine Bulwark spell-block chance + blocked-DoT absorb.
4. More Bruttah logs, especially anything in the +14-18 range the key-level
   verdict actually targets — the corpus still tops out at +9, a wide
   extrapolation distance.
