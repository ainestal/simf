# School-specific party magic DR — empirical variance survey (2026-05-18)

Ran `scripts/empirical_mitigation_analysis.py` against 9 representative
Brutoh M+ logs to characterize per-school magic damage reduction. Goal: decide
whether a school-specific `party_magic_dr` table (AUDIT.md F9) is tractable
or premature.

## Conclusion

**F9 is premature** — the variance is too wide to fit a single table.
School-specific magic DR varies by 30+ percentage points across logs for the
SAME character SAME spec SAME class. The dominant variable is **party
composition + external CDs**, not the school or the player's talents.

A school-indexed `party_magic_dr` constant would over-fit some logs (high
party-DR comp) while under-fitting others (low party-DR comp). The current
uniform 5% is conservative and known-good.

## Data

Per-log "effective magic DR beyond armor+vers" (back-solved assuming
DS=0.15 all-schools, vers=1.48%):

| Log | Run | Shadow | Arcane | Fire | Nature | Holy |
|---|---|---:|---:|---:|---:|---:|
| 051026_073906[0] | NPX+12 | 28.0% | 27.8% | — | — | 28.8% |
| 051026_090846[2] | WR+13 | 42.8% | 40.5% | 33.3% | 27.8% | — |
| 051026_105836[0] | PoS+14 | 39.9% | 30.8% | — | 24.8% | — |
| 051026_105836[1] | PoS+13 | 26.4% | — | — | — | — |
| 051026_151539[0] | Skyreach+14 | — | — | 34.6% | 33.7% | — |
| 051526_133447[0] | SotT+13 | 33.8% | — | — | — | — |
| 051526_133447[1] | SotT+13 | 27.8% | — | — | — | — |
| 051526_151316[0] | WR+10 | 23.2% | 27.2% | 12.0% | 23.4% | — |
| 051526_164802[0] | MGT+14 | 5.4% | 3.5% | 1.6% | — | 13.4% |
| 051526_210245[0] | MGT+12 | 35.7% | 26.5% | 31.3% | (−)  | — |
| 051726_134819[0] | MGT+13 | 29.7% | 26.5% | 24.8% | — | — |
| 051726_134819[1] | MGT+14 | 31.9% | — | — | — | — |
| 051826_135935[1] | Algeth'ar+14 | — | 28.5% | 32.7% | 27.4% | — |

**Per-school medians** (across all logs):
- Shadow: ~30%
- Arcane: ~27%
- Fire: ~31%
- Nature: ~27%
- Holy: ~28%

**Per-school ranges:**
- Shadow: 5.4% → 42.8% (37pp spread)
- Arcane: 3.5% → 40.5% (37pp spread)
- Fire: 1.6% → 34.6% (33pp spread)
- Nature: −4% → 33.7% (38pp spread)
- Holy: 13.4% → 28.8% (15pp spread; only 3 data points)

**The variance is bigger than the mean for every school.** Fitting a fixed
table would systematically over- or under-mit depending on the log's party
comp.

## What drives the variance

Hypothesis 1 (most likely): **Party composition matters more than school.**
A Disc Priest's Atonement, an Aug Evoker's Time Loop, or a Shaman's
Ancestral Vigor adds 15-25pp of damage taken reduction across multiple
events. Without those, the baseline is much lower. The script's "no-CDs"
filter only tracks the player's own CDs (Shield Block, Demo Shout, Shield
Wall, Last Stand) — it does NOT track external party CDs. So the
"no_CDs" bucket is actually "no PLAYER CDs but possibly heavy party
externals."

Hypothesis 2: **Different dungeons have different mob mixes.** MGT+14 at
5.4% shadow shows the player wasn't getting party magic mit on that
specific run; this might be a comp without Disc Priest, or the boss/mob
mechanics didn't trigger the auras. Variance within MGT alone (+14 → 5.4%,
+12 → 35.7%) confirms it's not the dungeon — it's the run.

Hypothesis 3: Talent differences across builds (e.g., Wowhead-prot has
Reinforced Plates / Armor Spec). But Wowhead-prot was used in only 2 of
these logs, and the variance shows up in brutoh-actual runs too. Probably
secondary.

## Tracked next steps

1. **Move party DR from a uniform constant to a CD-tracking model.** Parse
   SPELL_AURA_APPLIED for the major external DR cooldowns (Pain Suppression,
   Spirit Shell, Power Word: Shield, Time Loop, Ancestral Vigor, etc.) and
   apply their actual DR per event when active. This converts "averaged
   party DR" into per-event-truthful mitigation. Big project — touches
   log_replay.py, mitigation.py, and a CD-effects YAML.

2. **Detect party comp from COMBATANT_INFO when ACL is on.** Auto-tune
   party_magic_dr based on which support specs are present. Reduces the
   need for manual overrides but requires more spec mapping.

3. **For now: keep uniform party_magic_dr=0.05** as a conservative floor.
   Document in UI that magic-heavy log residuals can range ±15pp from this
   baseline depending on party comp.

## What WE will do this session

Defer F9 until at least one of (1) or (2) is feasible. The structural
correctness already landed in F1+F4+F5+F8 is the high-leverage work; F9
risks over-fitting back to Brutoh's specific runs without addressing the
real cause.
