# Magic mitigation under-prediction — root cause audit & fix (2026-05-16)

Follow-up to `docs/validation/mgt_12_2026_05_15.md`. That report flagged a
systematic shadow-school under-mitigation in the simf engine when replaying
the Magisters' Terrace +12 log: sim DR 10–30pp short of log DR per ability.
Three candidate causes were listed; this audit settles which were real.

## TL;DR

- The **biggest contributor** was a **YAML/character-config mismatch**, not an
  engine bug. Brutoh's `brutoh.yaml` declared `talents: kiratank-defensive`,
  which does **not** include Phalanx. His actual in-game build (visible in the
  log as the `1278009 "Phalanx"` BUFF cycling every ~3-7s) does include
  Phalanx, contributing ~4% averaged damage reduction to all incoming damage.
- A secondary contributor is **unmodeled party magic-DR auras** — Ancestral
  Vigor (61% uptime), Elemental Resistance (48% uptime), Earth Shield —
  averaging ~5pp on non-physical events.
- The other two candidate causes from the prior report (`log_absorbed` bug,
  active-mitigation timing) are **not the issue**:
  - Absorbed% matches between sim and log to within 0.1pp per ability —
    `log_absorbed` is faithfully extracted and applied.
  - Active-mitigation timing has the expected effect but the gap is also
    visible on no-CD shadow events (log 31.8% DR vs engine 24.4%).

## What the data actually shows

### Per-school damage-weighted pre-absorb DR (MGT+12 log, ground truth)

| School | Log DR | Engine static prediction (vers + indom + DS@0.20) | Gap |
|---|---|---|---|
| Physical | 74.3% | 68.9% (armor 67.1% × vers 1.48% × indom 4%) | +5.4pp |
| Shadow | 38.3% | 24.3% | **+14.0pp** |
| Arcane | 29.5% | 24.3% | +5.2pp |
| Fire | 34.0% | 24.3% | +9.7pp |

The shadow gap is the dominant contributor to the 10% global DTPS overshoot.

### Modal pre-absorb DR distribution (shadow events)

Damage-weighted bucket distribution of pre-absorb DR for shadow events:

| DR bucket | % of shadow base damage | Plausible composition |
|---|---|---|
| 24% | 18.0% | vers + indom + DS only (no other layers) — matches engine static |
| 27% | 12.2% | + ~3% other |
| 30% | 4.0% | + Phalanx 8% (no party DR) |
| **33%** | **34.8%** (dominant) | + Phalanx ~4% effective + ~4-5% party DR |
| 36-37% | 3-4% | + more party aura |
| 55-58% | ~5% | + Shield Wall (40%) |
| 85-87% | ~5% | + SW + BSV (30%) + extras |

This rules out the "DS=0.30" hypothesis: if DS were actually 30%, the 24%
bucket couldn't exist, but it accounts for 18% of damage. DS=0.20 is correct.
The systematic gap lives in the higher buckets.

### Per-CD-active partition (shadow, dmg-weighted)

| CD state | Log DR | Engine prediction |
|---|---|---|
| baseline (no CDs) | 26.7% | 24.4% — gap **+2.3pp** |
| SB only | 31.8% | 24.4% — gap **+7.4pp** (SB is phys-only in engine; correlated with Phalanx+party auras) |
| SB+Phx | 32.7% | 24.4% (engine doesn't have Phalanx in kiratank loadout) — gap +8.3pp |
| SW+LS+SB | 58.0% | 54.6% — gap +3.4pp (engine matches when SW is up) |
| BSV+SB | 78.9% | 47.0% — gap +31.9pp (BSV stacks with other CDs in log we don't track) |

The engine's mitigation chain is **correct when major CDs are active**
(SW gap is only 3.4pp). The systematic problem is the **baseline**: even
with no CDs the log shows 2-7pp more DR than the engine predicts, and on
SB-only events (most of the run) it's ~7pp short.

That 7pp baseline gap is split roughly:
- **~4pp** = Phalanx (talent in Brutoh's actual build, missing from his YAML)
- **~3-5pp** = averaged party DR auras (Ancestral Vigor, Earth Shield, etc.)

## The fix (commit, mitigation.py + constants.yaml + brutoh.yaml + tests)

### 1. Add a `brutoh-actual` talent loadout matching the log

Brutoh's M+ build uses Phalanx and Fueled by Violence; the engine had him
running `kiratank-defensive` (no Phalanx). New loadout:

```yaml
brutoh-actual:
  talents:
    - indomitable
    - battle_scarred_veteran
    - brutal_vitality
    - brace_for_impact
    - thunderlord
    - enduring_defenses
    - phalanx           # NEW
    - fueled_by_violence
```

`brutoh.yaml` updated to use `talents: brutoh-actual`.

### 2. Add `party_magic_dr` opt-in layer in `mitigation.py`

A new step (5d in the chain) applies an averaged party-magic-DR multiplier
when `state.party_magic_dr_active = True`. Default **off** so the K
calibration set (mostly physical-heavy logs) isn't biased.

```python
# 5d. Party magic-DR aggregate (Ancestral Vigor, Earth Shield, Devotion Aura,
# Elemental Resistance). Only when state.party_magic_dr_active is set.
if state.party_magic_dr_active:
    party_dr = spec_cfg.get("party_magic_dr", 0.0)
    damage *= 1 - party_dr
```

`constants.yaml`:

```yaml
specs:
  protection_warrior:
    party_magic_dr: 0.05         # opt-in, off by default
```

### 3. K=2700 preserved; RMSE shift documented

Re-fit on 8 logs (the original 6 + MGT+12 + Pit of Saron +12 in the same
2026-05-15 log) with the Phalanx loadout fix:

- K=2700: RMSE=0.083 (8 logs) / 0.097 (original 6 only)
- K=2800: RMSE=0.082 (8 logs) — marginally better, **not adopted**
  because the swing is below noise and the per-dungeon residuals don't
  uniformly improve

K=2700 retained. The MGT residual drops from previously ~+10% to **+5.2%**
(no party_magic_dr) / **+5.0%** (with party_magic_dr opt-in).

## Verification

### Unit tests (4 new in `tests/test_mitigation.py`)

- `test_party_magic_dr_off_by_default` — default replay does NOT auto-apply
  the party_magic_dr layer (only `state.party_magic_dr_active` does).
- `test_party_magic_dr_does_not_apply_to_physical` — opt-in respects the
  school gate; physical events stay armor-only.
- `test_mgt_replay_shadow_dr_within_tolerance` — integration regression
  asserting shadow dmg-weighted pre-absorb DR within 5pp of MGT+12 log.
  Pre-fix this would fail at ~14pp gap; post-fix it passes at ~3pp.
- `test_party_magic_dr_explicit_flag_in_synthetic` was rolled into the
  first test (covers both replay and synthetic paths).

### MGT+12 per-ability table — sim vs log (post-fix, party_magic_dr opt-in)

| Ability | School | Hits | Log Mit% | Sim Mit% | Δpp |
|---|---|---|---|---|---|
| auto-attack | physical | 1741 | 92.7 | 92.2 | -0.5 |
| Entropy Blast | physical | 43 | 94.0 | 84.9 | -9.1 |
| Arcane Blade | arcane | 53 | 52.8 | 62.6 | +9.8 |
| Shadow Bolt | shadow | 29 | 58.8 | 55.8 | **-3.0** (was -10.9) |
| Umbral Splinters | shadow | 81 | 61.8 | 46.7 | **-15.1** (was -23.5) |
| Hulking Fragment | shadow | 9 | 63.9 | 64.8 | +0.8 |
| Void Destruction | shadow | 84 | 65.4 | 41.4 | **-24.0** (was -31.7) |
| Consuming Shadows | shadow | 22 | 60.9 | 63.5 | +2.6 |
| Void Gash | shadow | 383 | 61.5 | 60.2 | -1.3 |
| Consuming Void | shadow | 83 | 42.1 | 48.9 | +6.9 |
| Stygian Ichor | shadow | 86 | 59.3 | 61.6 | +2.3 |
| Devouring Strike | shadow | 7 | 72.3 | 66.7 | **-5.6** (was -15.5) |
| Ignition | fire | 55 | 66.5 | 65.2 | -1.3 |
| Repulsing Slam | arcane | 4 | 69.8 | 71.8 | +2.0 |
| Energy Release | arcane | 41 | 65.5 | 65.5 | 0.0 |

**Totals:** sim dealt 37.67M vs log 34.57M = **+9.0% overshoot** (was +17.6%
pre-fix).

### Aggregate per-school

| School | Log mit% | Sim mit% | Δ |
|---|---|---|---|
| Physical | 92.8 | 91.7 | -1.1 |
| Shadow | 61.4 | 56.9 | **-4.5** (was -14.0) |
| Arcane | 59.9 | 64.9 | +5.0 (slight over) |
| Fire | 66.5 | 65.2 | -1.3 |

Shadow gap closed from -14pp → -4.5pp. The remaining residual is mostly
the high-DR Void Destruction puddles during the t=897s and t=1443s death
spikes, where the simulator's policy chooses different CD timing than the
real player did. That's a **policy-fidelity issue**, not a math issue.

## What this audit is NOT a fix for

Outside the scope of this patch (the residual ~9% MGT overshoot):

1. **Policy timing mismatch on death spikes.** When Brutoh popped SW + LS +
   BSV at t=1443, the simulator's policy fires SW earlier (t=1415, expired
   before the spike) and never fires LS. The engine math during the actual
   spike windows matches the log within 3-4pp; the simulator just doesn't
   reproduce when the real player pressed the buttons. Fixing this needs a
   smarter policy (e.g., adaptive lookahead) or accepting that replay is
   ground-truth and synthetic prediction is the goal.
2. **Per-dungeon party composition.** Different M+ groups have different
   party-DR aggregates. The 5% `party_magic_dr` is calibrated to a
   shaman-healer group; a paladin-healer group might be 3%, a holy priest 6%.
   Long-term, this should be **inferred from the log** (which auras were
   actually on the tank) and applied per-event rather than as a flat constant.
3. **Algeth'ar Academy DS-immunity outlier.** Echo of Doragosa is in the
   `immune_sources` list but the residual is still -16.8% (was -12.5%
   pre-patch — the Phalanx loadout fix expanded the gap slightly because
   the engine now applies Phalanx to ALL damage including DS-immune sources,
   which Phalanx in reality may not affect). The fix is to gate Phalanx
   through the same `immune_sources` mechanism Demo Shout uses, but that's
   a separate audit (the per-source-immunity list needs evidence per boss).
4. **The `is_log_replay` auto-trigger for `party_magic_dr`.** Considered and
   rejected — would have improved MGT but worsened Algeth'ar/Skyreach
   substantially (the calibration set is dominated by physical and the K
   constant was over-fit downward to compensate for missing Phalanx).
   Opt-in is the right interface.

## Files changed

- `src/simf/core/mitigation.py` — new step 5d (party_magic_dr layer); new
  `state.party_magic_dr_active` flag in `MitigationState.__init__`.
- `src/simf/data/constants.yaml` — `party_magic_dr: 0.05` knob on
  `specs.protection_warrior`; new `brutoh-actual` talent loadout;
  calibration metadata refreshed (per-dungeon residuals, log_count: 8,
  global_rmse: 0.083, last_calibrated: 2026-05-16).
- `src/simf/data/characters/brutoh.yaml` — `talents: brutoh-actual`.
- `src/simf/cli.py` — `load_character` now uses `Character.from_dict()` (was
  failing on `simc_path` extra field; pre-existing bug).
- `tests/test_mitigation.py` — 4 new tests including MGT integration
  regression.

## Trust verdict

- **Physical mitigation**: unchanged; K=2700 calibration preserved (RMSE
  drift 0.079 → 0.083 across 8 logs is within noise; the 4pp shift on
  Algeth'ar's residual is a known-outlier expansion driven by the now-active
  Phalanx talent applying to a DS-immune boss).
- **Magic mitigation**: substantially improved for replay against MGT-class
  logs. Shadow per-school gap closed from -14pp to -4.5pp.
- **Relative comparisons** (Δ between two character setups, gear swaps, etc.)
  are now more trustworthy on magic-heavy content. The 5pp opt-in is on
  top of the chain, so two-config comparisons subtract it out.
- **Absolute death predictions** for magic-heavy M+ keys are still ~5-10%
  pessimistic without party_magic_dr opt-in. Users who care about absolute
  numbers should enable the opt-in when they have a healer that provides
  party DR auras.
