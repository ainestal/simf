# Phase 4 — Brewmaster calibration attempt from local ACL logs (2026-06-07)

First real-log validation of a non-warrior tank spec. Source: local ACL-on
combat logs in `examples/Logs/` (a AnonRealm2 group's M+ runs), **not** WCL —
local logs source gear AND fight from the same `COMBATANT_INFO` block, so
there's no gear/fight mismatch the `calibrate-k --wcl-url` adapter would
introduce when a public ranking log's gear doesn't match our character YAML.

Tooling: `scripts/calibrate_spec_from_logs.py` (`inventory` + `calibrate`).

## TL;DR

- **Brewmaster Monk: NOT calibrated.** At the canonical global K=3430 the sim
  **over-predicts** damage taken by **+27.3%** (Player 1 +12 Windrunner Spire) and
  **+57.0%** (AnonBrewmaster1 +17 Seat of the Triumvirate) — far outside the ±15%
  Phase 4 bar. Best fit pins K at the sweep floor (2000), the textbook
  signature of the model under-mitigating. **`specs.brewmaster_monk.calibrated`
  stays `false`.** Gap is real (not a harness artifact — see positive control).
- **Vengeance DH:** only **1 timed** local run (Player 2, Maisara Caverns +10) —
  cannot meet the "≥2 logs" bar. Not attempted past inventory.
- **Bonus finding (arguably more important): a shipped hydrate bug.** The ACL
  "skip SimC paste" path (`hydrate_character`) never populates `shield_armor`,
  so **Prot Warrior / Prot Paladin characters loaded from an ACL log get block
  value ≈ 0 → ~24pp over-pessimistic survivability verdicts** in the live UI.
  The SimC-paste path is unaffected. Fix is gated on the same item-DB
  shield-armor sourcing as F12/F15. **Filed, not fixed.**

## Method

1. `inventory` runs `detect_party_roles` over each CHALLENGE_MODE run to
   auto-detect the tank (name / spec / key / dungeon / success).
2. For the target spec's **timed** runs, `calibrate` hydrates each tank's
   `Character` from that run's `COMBATANT_INFO` (`hydrate_character`), loads the
   replay (`load_replay`), and sweeps K with per-replay characters.
3. **Judge at the canonical global K=3430** (`armor.k_constant`), not the
   sweep's best-K. K is one global constant; a spec's best-K drifting far from
   3430 is a *spec mitigation gap*, not a license to set a per-spec K.

## Inventory (`examples/Logs/Archive-*.txt`)

| Spec | Tank | Dungeon | Key | Result | Usable |
| --- | --- | --- | --- | --- | --- |
| brewmaster_monk | Player 1 | Windrunner Spire | +12 | timed | ✅ |
| brewmaster_monk | AnonBrewmaster1 | Seat of the Triumvirate | +17 | timed | ✅ |
| brewmaster_monk | AnonBrewmaster1 | Seat of the Triumvirate | +17 | failed/abandoned ×2 | excluded |
| vengeance_demon_hunter | Player 2 | Maisara Caverns | +10 | timed | ✅ (only 1) |
| vengeance_demon_hunter | Player 2 | Skyreach | +14 | failed | excluded |

Two **different** Brewmasters with different gear/dungeons/keys — a stronger
generalisability test than two runs from one character.

## Verdict criteria (fixed before the sweep)

| Per-run deltas at K=3430 | Decision |
| --- | --- |
| both within ±15% | flip `brewmaster_monk.calibrated: true`, document, test |
| misses small + positive (Ironskin under-count) | document, lean conservative |
| large/systematic miss or best-K far from 3430 | keep `false`, file the gap |

## Brewmaster sweep (iters=300, seed=42, 2 timed runs)

```
  K= 2000: RMSE=0.135  [-8.3%, +17.2%]  <-best
  K= 2250: RMSE=0.187  [-0.2%, +26.4%]
  K= 2500: RMSE=0.248  [+6.9%, +34.4%]
  K= 2750: RMSE=0.307  [+13.2%, +41.4%]
  K= 3000: RMSE=0.362  [+18.8%, +47.6%]
  K= 3250: RMSE=0.412  [+23.9%, +53.1%]
  K= 3430: RMSE=0.445  [+27.3%, +56.8%]  [CANONICAL]
  ...
  K= 5000: RMSE=0.660  [+48.7%, +79.6%]
CANONICAL K=3430: RMSE=0.4452   Best K=2000 (RMSE=0.1349)
```

Hydrated stats (sanity vs Brutoh's in-game-calibrated `brutoh.yaml`: str 2182 /
sta 34176 / armor 5015 — Midnight 12.0.5 is heavily stat-squished, so the
log's small values are real, not corrupt): Player 1 armor≈1641, hp≈677k;
AnonBrewmaster1 armor≈1312, hp≈689k. Both leather; low armor is correct for the spec.

**Outcome: third row of the criteria table — large systematic miss, best-K far
below 3430. Keep `calibrated: false` and file the gap.**

## Positive control — is the gap real or a harness artifact?

The `hydrate_character → sweep` pipeline had never reproduced a known-good
number (the trusted RMSE=0.068 came from the `brutoh.yaml` path, which does
**not** hydrate). And `hydrate_character` hard-codes `race="human"`. So the
same harness was run on a **Brutoh Prot Warrior** run (Nexus-Point Xenas +12,
real DTPS 23,844), where `brutoh.yaml` is the in-game-calibrated reference:

| Variant | Δ at K=3430 | mastery | shield_armor |
| --- | --- | --- | --- |
| `brutoh.yaml` (trusted) | **+3.5%** | 1608 | 989 |
| hydrated as-is | +27.6% | 396 | 0 |
| hydrated + `shield_armor=989` | **+5.4%** | 396 | 989 |
| hydrated + yaml secondaries only | +27.5% | 1608 | 0 |
| hydrated + secondaries + shield_armor | **+3.3%** | 1608 | 989 |

The entire +24pp warrior bias is **`shield_armor=0`**; secondaries move DTPS
≈0pp. This proves two things:

1. **The harness is sound** — given correct inputs it reproduces the trusted
   +3.3% ≈ +3.5%.
2. **The shield_armor bug does not touch Brewmaster** (monks have no shield),
   and secondaries don't drive Brewmaster mitigation either → **the
   +27%/+57% Brewmaster over-prediction is a real model gap.**

## Why Brewmaster over-predicts (candidate causes — not yet root-caused)

Decomposition of Player 1's run (incoming, non-self):

```
  Σ base_amount (pre-mit) = 263,101,704
  Σ amount (landed)       =  45,955,405      implied total mit = 82.5%
  Σ absorbed              = 114,214,391
  Self Stagger DoT ticks  =  37,073,611
  by school: physical 84.3% mit | nature 67.6% | fire 64.7% | shadow 67.5% | arcane 64.1%
```

WoW logs Brewmaster **Stagger as `absorbed`** on the incoming hit (bundled with
Celestial Brew + healer shields), then part of it returns as separate Stagger
DoT ticks and part is purified away. The replay path's per-event
`min(damage, log_absorbed)` subtraction **clips** absorb that exceeds a single
hit's post-armor value, and the ~65% mitigation on *magic* schools (which armor
can't touch) is not reproduced. The higher-magic +17 key (+57%) misses worse
than the +12 (+27%), consistent with **magic-absorb handling**, not armor,
being the dominant gap. `log_replay.py` already warned: *"K calibration from
Brewmaster logs over-estimates K reduction; use Prot Warrior logs for canonical
K."* Root-causing/fixing this is engine work for the validator + user
ratification, not a same-session patch.

## Finding 2 — `shield_armor` dropped on the ACL hydrate path (shipped) — **FIXED**

> **Update (same day): fixed.** `hydrate_character` now sources `shield_armor`
> from the equipped off-hand via a `resolve_stats_fn` (item_db lookup by
> item_id), decoupled from the all-ratings-zero fallback; the UI's
> `_cached_hydrate_character` passes that resolver. **Verified by verdict
> delta** on the Brutoh control run: hydrated `shield_armor` 0 → 931 (auto-
> sourced), delta **+27.6% → +6.1%** (within ±15% — bug closed; the residual
> vs the trusted +3.5% is the secondary-rating scale, not chased here).
> **Dropped-field audit** (per the "is this one of a *class* of dropped
> fields?" risk): hydrate omits only two other `Character` fields, both
> correctly defaulted — `detected_talent_spell_ids` (COMBATANT_INFO field [24]
> is already post-racial/post-talent total armor, so re-applying would
> double-count) and `max_hp_override` (both load paths derive HP from stamina
> absent an explicit override; HP doesn't enter the DTPS metric). So
> `shield_armor` was the lone dropped load-bearing input — one bug, not a class.
> Shipped as the stacked fix PR (Director's Move A).

Original diagnosis (kept for the record):


- `_do_simc_load` (SimC paste) → `load_from_simc(..., resolve_stats_fn=_resolve_equipped_stats)`
  → `item_db` derives `shield_armor` from the off-hand slot. **Correct.**
- `_cached_hydrate_character` → `hydrate_character(...)` with **no**
  `resolve_stats_fn`. `hydrate_character` only sets `shield_armor` inside the
  `resolve_stats_fn` fallback, which fires solely when *all ratings are zero*
  (malformed log). For a normal ACL log it stays 0. Nothing in `ui/*.py`
  re-derives it from the hydrated `equipped`.
- Net: a shield tank loaded via the ACL "skip SimC paste" flow runs with
  `shield_armor=0` → block value ≈ 0 (`character.py:314`,
  `block_value = shield_armor × 2.5`). Directly affects Brutoh if he loads
  himself from an ACL log instead of pasting `/simc`.
- **Confidence / method.** The +24pp magnitude was *measured* on the exact
  `char_data` construction the UI's verdict consumes (the positive-control
  table above feeds hydrate's `char_data` to `run_simulation`, same as
  `Character.from_dict(_ss()["char_data"])`). The claim that the *live* UI
  doesn't silently refill `shield_armor` is *code-trace-confirmed*, not
  AppTest-driven: `_resolve_equipped_stats` (the sole `shield_armor` deriver,
  via `item_db.py:406`) has exactly one call site — `_do_simc_load`
  (`app.py:1145`, SimC-paste path). Nothing writes `char_data["shield_armor"]`
  on the hydrate path, and `_stats_for_item` / `_ITEM_DB` is a per-item
  display/Δ helper, not a `char_data` writer. An end-to-end AppTest over the
  log-load → verdict flow would be the stronger confirmation and a good
  regression for the eventual fix.
- **Fix** (deferred): route the hydrate `equipped` off-hand through
  `item_db`'s `shield_armor` derivation (`item_db.py:406`). Gated on item-DB
  shield-armor availability — the same data dependency as F12/F15.

## Next steps

1. Brewmaster model gap → validator (log-replay mode): rework replay-mode
   Stagger/Celestial-Brew/healer-absorb handling so aggregate (not per-event
   clipped) absorb + magic mitigation reproduce the log. Re-run this tool to
   re-judge at K=3430.
2. `shield_armor` hydrate fix → bundle with F15 item-DB shield-armor work.
3. VDH: needs a 2nd timed log (another local ACL log or a gear-matched source)
   before a calibrate attempt is meaningful.
4. Guardian / Blood DK: no timed local runs in the current corpus — still
   blocked on logs.
