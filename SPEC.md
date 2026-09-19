# simf — WoW Retail Tank Damage Mitigation Simulator

> **Historical design doc** (v0 era). The "v0.5 / v1" milestones below have all shipped — most of the headline questions are answered in code today. For current state, current calibration, and the live queue, see [ROADMAP.md](ROADMAP.md). This file is preserved because the goals + non-goals + architectural framing in §1–§3 still describe what simf is.

> Monte Carlo simulator for tank survivability in Mythic+, targeted at **Midnight, patch 12.0.5**. Initial focus: **Protection Warrior**. Built to fill the gap left by Raidbots, which officially deprecated tank survivability metrics.

## 1. Goals

- Quantify **survivability** (not DPS) for tanks in M+ with realistic-enough fidelity to drive gearing and talent decisions.
- **Headline questions for v0**:
  1. Does the Earthen `Titan-Wrought Frame` racial (+10% item armor) materially improve survivability? By how much, against what damage profile?
  2. What's the optimal secondary-stat distribution for a Prot Warrior in M+ given a realistic damage profile and healer profile?
- **Headline question for v0.5**: Find the best defensive talent build by exhaustively (or heuristically) searching the talent space.
- **Headline question for v1**: Which race maximizes survivability for a given tank spec? (Compare all racial passives — Earthen `Titan-Wrought Frame`, Tauren `Endurance`, Dwarf `Stoneform`, Kul Tiran `Brush It Off`, Highmountain Tauren `Rugged Tenacity`, etc. — under identical character stats and damage profile.)
- Build the data model so log replay (v1) is a drop-in extension, not a rewrite.

## 2. Non-goals

- DPS rotation / threat / APL optimization. SimulationCraft does this; we won't duplicate.
- PvP, raid bosses, leveling.
- Healer agent simulation (mana, throughput limits, decision-making).
- Pixel-perfect dungeon scripting. Synthetic damage profiles parameterized by archetype (caster pull, tank-buster boss, etc.) are sufficient for v0.

## 3. Fidelity model

**Monte Carlo over a parameterized damage stream**, with a lightweight **active-mitigation policy** layered on. Rationale: closed-form misses Shield Block / Ignore Pain interaction and tail spike risk; full event sim is overbuilt for stat/talent comparison questions.

Each iteration:
1. Generate an event stream (mob auto-attacks, periodic specials, magic casts, DoTs, scripted tank busters) for `T` seconds.
2. Walk the timeline at `dt` resolution (default 100 ms). At each event:
   - Roll avoidance (dodge / parry).
   - Roll block (with crit-block from Mastery, +block-chance from Shield Block window).
   - Apply armor DR (physical) using `DR = Armor / (Armor + K)` where `K` is loaded from a calibration constant for current player level.
   - Apply per-school DR (magic schools, no armor).
   - Apply Versatility flat DR.
   - Consume active mitigation: Shield Block window, Ignore Pain absorb pool, talent absorbs, CDs (Shield Wall, Last Stand, Demo Shout, Spell Reflect).
   - Apply healing: baseline HPS floor + scheduled external CDs + tank self-heals (Impending Victory, Brutal Vitality, Battle-Scarred Veteran proc).
3. Track HP. Record death, time-to-die, DTPS, and rolling-window damage taken (5/10/15 s).
4. Aggregate over N iterations (default N=2000) → death rate, percentile windows, M+TMI (see §6).

### 3.1 Active mitigation policy

A simple priority list, not a full APL. For Prot Warrior:

```
1. Shield Block       if charges > 0 AND rage >= 30 AND not currently in SB window
2. Ignore Pain        if rage >= 40 AND IP absorb < cap AND incoming damage in last 3s > threshold
3. Demoralizing Shout if off CD AND rolling 5s incoming DTPS > threshold
4. Shield Wall        if HP < 40% AND off CD
5. Last Stand         if HP < 25% AND off CD AND Shield Wall on CD
6. Spell Reflect      if incoming magic cast detected AND off CD
```

Thresholds are configurable. Calibration target: AM uptime % within ±5% absolute of Kiratank's real WCL-derived uptimes.

## 4. Inputs

### 4.1 Character

**v0**: SimC import string (`/simc` in-game). Parse:
- Race (relevant: Earthen for Titan-Wrought Frame)
- Class / spec
- Talent loadout string
- Equipped gear (item levels, stats) — we recompute final stats rather than trust SimC totals, so we control buffs/multipliers
- Stamina / Strength / Armor / secondaries

**v1**: Blizzard API armory import (Battle.net OAuth: `character-profile`, `character-equipment` endpoints).

### 4.2 Encounter / damage profile

YAML, per-archetype:

```yaml
profile: m+_high_key_pull
duration_s: 60
mobs:
  - count: 4
    swing_timer_s: 2.0
    swing_damage_mean: 250000
    swing_damage_variance: 0.20
    school: physical
  - count: 1
    swing_timer_s: 2.0
    swing_damage_mean: 180000
    swing_damage_variance: 0.20
    school: physical
    casts:
      - spell: shadow_bolt
        cadence_s: 4.0
        damage_mean: 320000
        school: shadow
tank_busters:
  - time_s: 15.0
    damage: 1200000
    school: physical
    is_avoidable_by_spell_reflect: false
affix:
  fortified: true
  tyrannical: false
  seasonal_modifier: 1.0
```

Ship a small library of profiles: `m+_pull_caster`, `m+_pull_melee`, `m+_boss_tankbuster`, `m+_aoe_dot_pull`. Aggregating across multiple profiles in one run gives a "key composite" survivability score.

### 4.3 Healing profile

```yaml
profile: m+_high_key_healer
baseline_hps_pct_of_dtps: 0.85   # auto-scales to chosen damage profile
externals:
  - time_s: 30
    type: dr_cooldown
    amount_pct: 0.40         # 40% DR
    duration_s: 8
  - time_s: 60
    type: absorb
    amount: 8000000
    duration_s: 10
  - time_s: 90
    type: dr_cooldown
    amount_pct: 0.40
    duration_s: 8
  - time_s: 120
    type: absorb
    amount: 8000000
    duration_s: 10
```

**Default v0 preset (`m+_high_key_healer`)**:
- `baseline_hps`: 85% of mean DTPS for the chosen damage profile (auto-scaled).
- DR external every 90 s (40% DR / 8 s — Pain Suppression / Roar of Sacrifice class).
- Absorb external every 60 s offset by 30 s (8M absorb / 10 s — Ironbark class).

This is a deliberately good healer (high key assumption). v0.5 will add `m+_low_key_healer` (worse, more variance) and `m+_progression_healer` profiles.

## 5. Outputs

- **CLI summary**: death rate, M+TMI, p95/p99 window damage, mean DTPS, Earthen-on-vs-off Δ.
- **Stat weights**: numerical Δsurvivability per +1000 of each secondary, computed by perturbed-rerun. Output per-profile and aggregated.
- **A/B comparison mode**: two configs side-by-side with confidence intervals on the deltas.
- **JSON dump**: full per-iteration timeline for downstream tooling.
- **HTML report** (v0.5): one-page with damage histogram, HP trajectory traces, AM uptime breakdown.

## 6. Survivability metrics

Adopted from SimC's TMI but adapted for M+:

- **M+TMI**: `10⁴ · ln[(N₀/N) · Σ exp(10·MAᵢ)]` where `MAᵢ` is rolling **12-second** damage-taken minus healing/absorbs, normalized to max HP. (Original TMI's 6 s window predates modern M+ burst patterns.)
- **Death rate**: % of iterations where HP reaches 0.
- **Window damage**: p50/p95/p99 of largest 5 s / 10 s / 15 s rolling damage taken windows across iterations.
- **Mean DTPS** and **Mean DTPS net of self-mitigation absorbs**.

We report all of these. M+TMI is the headline single number; window damage is what you actually look at when comparing options because it's interpretable.

## 7. Earthen racial — encoding

`Titan-Wrought Frame` (spell 436340): *"Increases your base Armor from equipped items by 10%."*

Mechanically: `armor_from_gear *= 1.10`, applied **before** all subsequent armor multipliers (Bear Form, Plate Specialization, Shield Block buff, Demo Shout debuff, Mountain Thane talents). All those layers are separately multiplicative, so the racial preserves its full ~10% effective HP benefit regardless of other buffs active.

Toggle in character config (`race: earthen` → racial auto-applies). A/B mode with this toggle answers headline question 1 directly.

Expected sim result: ~10% physical EHP improvement, scaled by the physical-damage share of the encounter. For an 80%-physical M+ profile, expect ~8% reduction in p99 window damage and a proportional drop in death rate against marginal-survival scenarios.

## 8. Default talent loadout — `kiratank-defensive`

Source: [kiratank.com/resources/Protection-Warrior-Talents](https://kiratank.com/resources/Protection-Warrior-Talents) (12.0.5).

Hero tree: **Mountain Thane**.
- Path: Lightning Strikes → Crashing Thunder → Ground Current → Strength of the Mountain → Storm Surge → Thunder Blast → Conductivity → Flashing Skies → Burst of Power → Capacitance → Avatar of the Storm.

Class tree (consensus): Battle Stance, Defensive Stance, War Machine, Impending Victory, Thunder Clap, Crackling Thunder, Rallying Cry, Heroic Leap, Intervene, Spell Reflection, Pain and Gain, Rend, Shockwave, Honed Reflexes, Cruel Strikes 2/2, One-Handed Weapon Specialization, Armored to the Teeth 2/2, Reinforced Plates 2/2, Anger Management, Stance Mastery, Battlefield Commander.

Spec tree (Kiratank's defensive variant): Ignore Pain, Revenge, Demoralizing Shout, Devastator, Strategist, **Brace for Impact**, Brutal Vitality, Instigate, **Shield Wall**, **Thunderlord**, Defender's Aegis, Fueled by Violence, **Enduring Defenses**, **Unyielding Stance**, Avatar, Booming Voice, Violent Outburst, Shield Charge, **Battle-Scarred Veteran**, **Indomitable**.

**Apex / capstone: Phalanx skipped.** Kiratank's stated reasoning: better defensive options exist; Phalanx doesn't buff Thunder Blast.

Alt loadout: `archon-meta` — same as above but with **Phalanx 1/1** (the +10 meta default per Archon, ~100% usage). Available for A/B comparison.

For modeled-effect encoding, only mitigation-relevant talents are translated to in-engine effects; the rest are stored as opaque metadata for reference but don't affect the sim. (This is a founding v0 design doc — the `data/talent_loadouts/` file layout described here was later superseded by the `talents=` string decoder and the talent-facing UI removal; see CONTRIBUTING.md for current state.)

## 9. Talent optimizer (v0.5)

Goal: "find the best defensive talent build" by simulation, not theory.

Approach:
- Define a **flex set** of talents — nodes that meaningfully affect survivability and have reasonable swap candidates. Examples for Prot Warrior:
  - Tough as Nails ↔ Indomitable ↔ Bloodsurge
  - Heavy Repercussions ↔ Into the Fray
  - Phalanx 0/1 vs 1/1
  - Brace for Impact 0/2/2 levels
  - Apex / capstone choices
- Each flex point has a small set of values. Total combinations bounded (~32–256 in practice).
- Exhaustive Monte Carlo: sim each combination at lower iteration count (N=500), then re-sim top 10% at full N=2000.
- Rank by M+TMI, with death% and p99 10s window as secondary criteria.

**Caveat**: the optimizer assumes the sim's mitigation model is faithful. Talents that affect proc chains we don't model accurately will be mis-valued. v0.5 ships with a defined "modeled talents" allowlist — flex talents must come from this list.

## 10. Architecture

```
simf/
  pyproject.toml
  README.md
  SPEC.md                   # this file
  src/simf/
    core/
      timeline.py           # event stream, dt-step walker
      mitigation.py         # avoidance, armor, vers, AM stack
      character.py          # stats, talents, racials
      policy.py             # active mitigation decision logic
      metrics.py            # M+TMI, window damage, death rate
      rng.py                # seeded numpy RNG
    classes/
      warrior_prot.py       # talents, racials, AM rotation policy
    io/
      simc_import.py        # parse /simc string
      armory.py             # Blizzard API (v1)
      wcl_replay.py         # WCL v2 GraphQL replay (v1)
    optimizer/
      talent_search.py      # v0.5
      stat_weights.py
    reports/
      cli.py
      html.py               # v0.5
      json_dump.py
    data/
      constants/            # armor K, crit-block coefficients, school DR caps
      profiles/damage/      # M+ damage profile YAMLs
      profiles/healing/     # healer YAMLs
      talent_loadouts/      # named loadouts (kiratank-defensive, archon-meta)
  tests/
    test_mitigation_math.py # golden-file vs hand-computed scenarios
    test_simc_import.py
    test_metrics.py
```

## 11. Tech stack

- Python 3.13.
- NumPy for vectorized iteration where possible.
- Pydantic v2 for input schemas.
- `typer` for CLI.
- `rich` for output formatting.
- `pytest` for tests.
- ~1500 LOC for v0.

Constants live in YAML, not source code. Patches don't require a release — just data updates.

## 12. Data model — events (replay-shaped from v0)

All damage events conform to:

```python
@dataclass
class DamageEvent:
    time_s: float
    source_id: str          # mob/boss identifier
    school: Literal["physical", "fire", "shadow", "frost", "nature", "arcane", "holy"]
    raw_amount: float       # pre-mitigation
    attack_type: Literal["melee", "ranged", "spell"]
    is_dot_tick: bool = False
    is_tank_buster: bool = False
    is_avoidable: bool = True   # whether dodge/parry can apply
    is_blockable: bool = True   # whether block can apply
```

This is exactly the shape a Warcraft Logs v2 GraphQL `damage-taken` event yields. **v1 log replay = swap event source from "synthetic generator" to "WCL importer" — the engine downstream is unchanged.**

## 13. Phasing

| Version | Scope | Estimate |
|---|---|---|
| **v0** | Prot Warrior, kiratank-defensive default, 1 synthetic M+ profile, SimC import, CLI summary, A/B mode (Earthen on/off), stat-weight sweep, M+TMI + window damage metrics. | ~1 weekend |
| **v0.5** | 4–5 synthetic profiles, talent optimizer, archon-meta loadout, HTML report, scheduled-cooldown healer profile expansion. | +1 weekend |
| **v1** | Other 5 tanks (Blood DK, Brewmaster, Guardian, Prot Pal, Veng DH), Blizzard API armory import, log replay from Warcraft Logs (single fight at a time), **race optimizer** (rank racials by survivability for a given character + damage profile). | ~2 weekends |
| **v2** | Per-dungeon damage profiles auto-derived from a corpus of WCL logs; low/high-key healer profile differentiation. | open |

## 14. Calibration plan

The sim's predictions need to match reality. Calibration steps:

1. **Mitigation math**: golden-file tests against hand-computed scenarios (single hit, fixed armor, no AM) — pure formula correctness.
2. **AM uptime**: simulate Kiratank's policy and compare AM uptime % to real log-derived uptimes (Shield Block uptime, Ignore Pain absorb consumption rate). Target within ±5% absolute.
3. **DTPS calibration**: simulate against a known WCL run profile (manual import) and compare predicted vs actual DTPS. Target within ±10%.
4. **Sanity sweep**: stat weight signs match Skyhold/Kiratank consensus (Vers > Mastery > Haste > Crit, roughly, for high keys). If signs disagree, sim is wrong, not the consensus — investigate before publishing weights.

## 15. Open questions deferred

- Healer mana / throughput as a constraint (currently: free healer).
- Imperfect player AM usage (reaction lag, missed presses).
- Scoring "deterministic vs stochastic" survivability — some builds are smoother but lower TTL, some are spikier.
- Affix modeling precision (Fortified, Tyrannical, seasonal) — currently lumped into a flat damage multiplier.
- Low-key vs high-key healer profile differentiation (deferred to v0.5+).

## 16. What this tool is *not*

- Not a replacement for SimulationCraft for DPS questions.
- Not authoritative for content we don't model (raid mechanics, PvP).
- Not a substitute for warcraftlogs analysis of your actual runs — log replay (v1) bridges that gap, but synthetic profiles are pedagogical, not predictive of any specific dungeon.

---

## Appendix A — Why this exists

Per Seriallos' *"Tank Sim Changes"* post on Raidbots, all tank survivability metrics on Raidbots have been **deprecated**. Top Gear and stat weights for tanks now optimize DPS only. TMI still exists in SimulationCraft but has been unmaintained since Theck (the metric's author) left the project after Legion. There is currently no maintained, M+-aware tank survivability simulator in the public ecosystem. simf fills that gap.

## Appendix B — Sources

- Earthen racial: [Titan-Wrought Frame – Wowhead Spell 436340](https://www.wowhead.com/spell=436340/titan-wrought-frame)
- Kiratank build: [kiratank.com/resources/Protection-Warrior-Talents](https://kiratank.com/resources/Protection-Warrior-Talents)
- Archon meta: [archon.gg Prot Warrior M+ +10](https://www.archon.gg/wow/builds/protection/warrior/mythic-plus/talents/10/all-dungeons/this-week)
- TMI: [Theck-Meloree Index Standard Reference](https://sacreddutydotnet.wordpress.com/theck-meloree-index-standard-reference-document/)
- SimC tank docs: [github.com/simulationcraft/simc/wiki/SimcForTanks](https://github.com/simulationcraft/simc/wiki/SimcForTanks)
- Raidbots deprecation: [Tank Sim Changes – Seriallos](https://medium.com/raidbots/tank-sim-changes-4ce705182a3b)
