# Healer throughput cap + death_rate noise floor — 2026-07-06

Top-5 #3 from the 2026-07-06 retrospective (`ROADMAP.md`'s Active Triage
Queue): "Cap the healer's throughput, then surface death_rate." Sequenced
strictly after Top-5 #2 (tail-risk surfacing).

## The problem

`death_rate` was not an honest number. `data/profiles/healing/m+_high_key_healer.yaml`'s
reactive-heal layer was **uncapped** — a burst heal fired whenever HP
dropped below 40%, gated only by a 6s cooldown, with zero accounting for
how much healing had already been spent. The YAML's own comment said this
existed specifically to fix "the 100% death-rate artifact in log replay."
Shipping `death_rate` as a trust-facing number against that faucet would
have been showing a number decided by a fictional safety net, not a real
healer's throughput limit.

A second, separate bug undercounted Prot Paladin deaths specifically: the
Ardent Defender cheat-death gate checked cooldown-readiness
(`now >= ardent_defender_cd_until`) instead of whether the AD buff window
was actually active (`now < ardent_defender_until`). Since `cd_until`
starts at `-1.0` ("always ready") before the first ever press, this gave
a free save to the very first lethal hit of a fight with zero button
presses — the opposite of SimC's `buffs.ardent_defender->check()` gate.
Fixed as a hard prerequisite (see `core/runner.py`, gated on
`character.class_spec == "protection_paladin"` — zero effect on any other
spec, confirmed by full-suite pass and by construction).

## Design: token-bucket healer budget

calibration-scientist review (2026-07-06) recommended a token bucket over
either a hard skip-the-burst cap (reintroduces the artifact — a burst the
bank can't fund gets scaled down, never skipped outright) or an
uncapped-with-decay model (harder to reason about / validate).

- **Capacity B** and **refill rate R** on a shared bank. Both the smooth
  baseline heal and the reactive burst draw from it; an isolated burst at
  full bank is always fully funded (bank starts full), only *sustained*
  chains of reactive bursts drain it faster than R replaces it.
- Charged at the **nominal** (pre-`heal_mult`) rate. Mastery (Nature's
  Guardian etc.) makes a *given* heal land for more effective HP on the
  receiving tank — it doesn't make the healer's cast cost less mana. The
  first implementation multiplied by `heal_mult` before the budget draw,
  which made higher mastery drain the bank faster for no in-game reason;
  caught immediately by `test_guardian_mastery_has_positive_survival_marginal`
  flipping sign, fixed before merge.
- `healer_budget_refill_pct_of_max_hp_per_s` / `healer_budget_capacity_pct_of_max_hp`
  on `HealingProfile`, both defaulting to `0.0` (disabled — bit-identical
  to pre-cap behavior for any profile or test fixture that doesn't set
  them).

## Measuring R and B from data, not a free fit

`scripts/measure_healer_budget.py` (new) parses real healing-received-on-tank
from the same two corpora CONTRIBUTING.md calls calibrated, via a new
`combat_log_healing.py` parser (mirrors `combat_log_damage.py`'s
end-anchored suffix slicing — the ACL power-type block in the middle is
variable-length). Only TIMED runs (`success=True`, via the same
`parse_challenge_modes` + `detect_party_roles` path `calibrate_spec_from_logs.py`
uses), and only **external** heals (`source_name != target_name` — the
tank's own self-heals like Ignore Pain/Word of Glory/Death Strike are
already modeled per-spec elsewhere, not through this budget).

```
R = p95 of the rolling 60s external-heal-received HPS across all timed runs
B = max observed rolling-10s external-heal-received sum, minus R*10s
```

| Corpus | Runs | R (raw) | R (% max_hp/s) | B (raw) | B (% max_hp) |
|---|---|---|---|---|---|
| Prot Warrior (`examples/*.txt`, Brutoh-Uldum-EU) | 16 | 30,689 HP/s | 3.81% | 1,319,916 HP | 163.7% |
| Guardian Druid (`examples/anonguardian1-guardian/`, AnonGuardian1-AnonRealm1-EU) | 17 | 33,145 HP/s | 4.31% | 1,955,294 HP | 254.1% |

R agrees within ~13% across two different tanks/key-level mixes — a
reasonable signal it's measuring something real rather than corpus noise.
B disagrees more (~55%) because the Guardian corpus skews to higher key
levels (+15-17 vs Warrior's +12-14) with bigger bursts. Shipped as the
**average** of the two (R=4.0%, B=2.1×max_hp) rather than picking either
corpus alone — under-provisioning the bank would fail saves that reality
doesn't fail, reintroducing the artifact this exists to prevent.

## Re-validation (fixed K, not a refit)

Per calibration-scientist: refitting K to absorb a healer-model change
would be exactly the self-fit leakage the K=2700-era self-fit taught this
project to avoid. Procedure was a controlled before/after at the
**canonical, unchanged** K=3430, isolating just this change via
`git stash` (the healer-cap + AD-fix files only) on the same log corpus,
same command, same seed.

**Prot Warrior** (`simf calibrate-k --logs-dir examples --k-min 3430 --k-max 3430 --k-step 25`,
24 logs matched — note: recursive glob under `examples/` pulls in
`examples/mai/` and `examples/Logs/` too, so this is NOT the canonical
16-log headline corpus; it's still a valid controlled comparison since
before/after used the identical set):

| | RMSE |
|---|---|
| Before (uncapped healer, old AD gate) | 0.1106 |
| After (capped healer, fixed AD gate) | 0.1154 |

Δ = +0.0048 (+4.3% relative). No log crossed the ±15% trust threshold that
wasn't already crossing it before the change; the one existing outlier
(Algeth'ar Academy, -37.4%→-40.9%) moved further in the same direction it
was already flagged for, not a new failure mode.

**Guardian Druid** (`scripts/calibrate_spec_from_logs.py calibrate guardian_druid --logs-dir examples/anonguardian1-guardian`,
17 timed runs):

| | RMSE at canonical K=3430 |
|---|---|
| Before | 0.1183 |
| After | 0.1199 |

Δ = +0.0016 (+1.3% relative). One run moved from -14.0% to -15.2% (just
past the ±15% line it was already sitting on); the tool's strict
all-within-15% verdict was already `False` before this change (consistent
with CONTRIBUTING.md's own note that the original Guardian calibration had 4/16
runs outside ±15% and used RMSE + bias judgment, not a strict rule, to
flip `calibrated: true`).

**Verdict:** both corpora hold within a small, honestly-reported margin.
Neither spec's `calibrated: true` status is threatened by this change.

## death_rate noise-floor treatment

`core.metrics.death_rate_stderr_pp` (promoted from a private helper that
previously lived only in `ui/log_cd_plan.py`, so both the CD-plan verdict
card and the key-level verdict panel share one formula) computes the
binomial standard error in percentage points. The key-level verdict
panel's per-key bullet list now reads `death **5.5%** (±1.6pp)` instead
of a bare percentage, with a caption explaining what the range means and
pointing at the Talent A/B panel (higher iteration count) for closer
comparisons.

## 2026-07-07 dual-validator review — a real accounting bug, fixed

Three independent reviewers (engine-math validator, log-replay validator,
calibration-scientist) all converged on the same critical defect before
merge: the bucket was charged on the **gross/offered** heal amount at both
draw sites (`runner.py`'s baseline heal and reactive burst), while R and B
were **measured** from the log corpora on **net-of-overheal** healing
(`measure_healer_budget.py` computes `net = amount - overhealing`). That
mismatch drains the bank far faster than reality justifies — a topped-off
tank's baseline heal is nearly all overheal, and charging it in full burns
real budget a real healer's throughput was never actually spent on. One
validator reproduced two real, verified **zero-death** Warrior log runs
flipping to **100% simulated death_rate** under the shipped code — the
exact "100% death-rate artifact" this whole cap exists to prevent,
reintroduced by a units bug invisible to the RMSE-only re-validation above
(RMSE is a DTPS/mitigation metric; it doesn't see death_rate at all).

**Fix:** both draw sites now charge `actual_heal / heal_mult` (the
landed amount, converted back to nominal/pre-`heal_mult` units) instead of
the full `nominal_heal`/`nominal_burst`. An isolated burst or a heal into a
non-full tank is unaffected (nothing was overhealing); a heal into an
already-topped tank now costs the bank ~nothing, matching how R/B were
measured. Five new engine-level tests (`tests/test_healer_budget.py` —
previously the token bucket itself, the PR's headline mechanism, had
**zero** direct tests; only the log parser was tested) pin: an isolated
burst from a fresh bank is bit-identical capped-vs-uncapped; a chain of
overheal-only "heartbeat" ticks doesn't meaningfully drain a bank a later
real burst needs; a bank genuinely too small still binds (the fix isn't a
no-op); `capacity=0.0` (every pre-existing profile) is bit-identical to an
explicitly-zeroed profile; and higher Guardian mastery only ever helps
under a binding budget (regression pin for a sign-flip bug the PR's own
commit message says was caught once already during development — nominal,
not `heal_mult`-scaled, charging is what keeps that true).

Also fixed: `HealReceivedEvent.amount`'s docstring wrongly claimed the
field was "already net of overhealing" (it's gross — real CLEU behavior,
proven by the PR's own full-HP test fixture); the `HealingProfile` field
comment wrongly implied `refill == 0.0` disables the cap (it's `capacity
== 0.0`; a `capacity > 0, refill == 0.0` profile is a valid, intentionally
harsh "bank that never refills" config, not a disabled one); and
`death_rate_stderr_pp` rendering `±0.0pp` at `p_hat == 0` (the most common
comfortable-key reading) read as false certainty — the key-level verdict
panel now shows a one-sided rule-of-three upper bound (`≤Npp`) at that
edge instead, captioned as such.

## 2026-07-07 — a second, deeper finding: log-replay can still flip on long fights

Re-validating the fix against real logs (not just the RMSE sweep, which
doesn't see death_rate) surfaced a **second, separate** issue the
accounting fix does not resolve: on long log-replay fights, the
**mitigation model's own DTPS over-prediction** — a pre-existing,
independently-tracked gap (CONTRIBUTING.md's "Structural physical-mit gap",
roadmap item 5) — can drain even a *correctly-charged* bank and flip a
real zero-death run to 100% simulated death_rate.

Concrete example: `WoWCombatLog-051026_105836.txt[1]`, Pit of Saron +13,
a real 1,658.8s (~27.6 min) TIMED clear with **zero real deaths**. Real
DTPS from the log is 24,856/s; the sim's own `mean_dtps` on a replay of
the *same* events is 32,986/s — **+33% over-prediction**, independent of
the healer entirely. The old, uncapped reactive burst was an infinite
safety net that could always cover this gap, so it was invisible. With a
realistically finite healer (R≈31.5k/s, close to but below the sim's own
32,986/s demand), sustained baseline draws alone outpace the refill rate
over a 27-minute fight, the shared bank drains, and every subsequent
reactive burst — needed for real spike damage — is starved. Post-fix, this
run still simulates 100% death_rate (capped) vs 0% (uncapped); it is the
**only** genuine cap-caused flip found in a 5-run spot-check (3 other
"flips" in the same check were already at 100% *uncapped* too — a
pre-existing, unrelated mitigation over-prediction on those specific runs,
not caused by the cap).

**Scope decision:** this is a real, separate, and materially harder
problem than the accounting bug (it requires chasing down *why* the
mitigation model over-predicts DTPS on specific runs — the same open
question roadmap item 5 has carried for months) — not something to solve
inside this PR. The **verdict-sweep surface** (the key-level panel this
PR's death_rate-honesty claim is actually about) is unaffected: it uses
short synthetic damage profiles, not 27-minute log replays, and a
validator's own sweep check (+2..+24, 200 iters) found the cap "inert
through +21" even *before* this accounting fix (and the fix only makes the
cap less binding, never more). The **log-replay surface** — the CD-plan
panel on the Why-Died page, and calibration tooling — inherits this
caveat: `ui/log_cd_plan.py`'s existing "pessimistic regime" branch
(triggered at `death_rate >= 50%`) now explicitly names the healer-cap /
DTPS-over-prediction interaction rather than letting a 100% reading pass
as confirmed danger. **`death_rate` is honest as shipped for the
verdict-sweep panel; it is not yet a fully-trustworthy number on
long/high-residual log replays**, and that gap is now named, tested for,
and cross-referenced to roadmap item 5 rather than silently shipped.

## Full test suite

2024 passed, 19 skipped after the 2026-07-07 fixes (accounting fix + 5 new
`test_healer_budget.py` tests + 1 new `test_cd_plan_tab.py` caveat test;
skip-count delta vs the original 2030/7 is environment-gated network/creds
skips on this sandbox, not new failures). Run via the repo's pre-push hook
(full xdist suite) with the same result.
