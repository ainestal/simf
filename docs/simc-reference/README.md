# SimC reference — canonical damage/mitigation source

Source extracts from SimulationCraft, used as ground truth when auditing
`simf/core/mitigation.py`, `character.py`, and `constants.yaml`.

**Licensing:** every `.cpp`/`.txt` file in this directory is verbatim
third-party code excerpted from SimulationCraft, which is licensed under the
GNU GPL v3 — see `./LICENSE` (fetched verbatim from gnu.org). This is
distinct from the rest of the repo (AGPL-3.0-or-later): these files are
GPL-3.0-or-later, kept here for audit/reference purposes, each carrying its
own SPDX header pointing back here.

- **Repo:** https://github.com/simulationcraft/simc
- **Branch:** `midnight`
- **Commit:** `fd60a6384dcd55f3737f18f31755141c1fe1e540` (2026-05-18)
- **WoW build referenced in DBC:** 12.0.5.67602

## Files in this directory

| File | What it contains | SimC path |
| --- | --- | --- |
| `target_mitigation.cpp` | `player_t::target_mitigation()` — armor + block application | `engine/player/player.cpp:8728` |
| `composite_mitigation_multiplier.cpp` | The "DR-multipliers" bucket — versatility, stoneform, pain_suppression, AoE avoidance | `engine/player/player.cpp:6189` |
| `calculate_armor_resist.cpp` | The armor-curve formula `armor / (armor + K)`, clamped to `MAX_ARMOR_DAMAGE_REDUCTION` | `engine/util/util.cpp:3713` |
| `composite_player_target_armor.cpp` | Target armor lookup with attacker armor-penetration | `engine/player/player.cpp:6180` |
| `composite_armor.cpp` | Player's own armor with base/bonus/multiplier stacking | `engine/player/player.cpp:5379` |
| `warrior_target_mitigation.cpp` | Prot Warrior override — adds Crit Block as a second armor-curve pass with multiplier=2.0 | `engine/class_modules/sc_warrior.cpp:9139` |
| `assess_damage_flow.cpp` | Where `target_mitigation` and `composite_target_mitigation` are called in the action pipeline | `engine/action/action.cpp:3236, 5011, 4362` |
| `expected_stat_level_90.txt` | DBC table excerpt — armor_constant, primary/secondary stat values per level | `engine/dbc/generated/expected_stat.inc` |
| `MAX_ARMOR_DAMAGE_REDUCTION.txt` | `0.85` cap on armor + block resist | `engine/sc_enums.hpp:89` |

## What's NOT in `target_mitigation`

Reading the SimC pipeline left-to-right, the canonical order is:

1. **Avoidance** (dodge/parry/miss) — resolved upstream in attack roll; if the attack misses, `target_mitigation` is never called.
2. **`composite_target_mitigation` = `composite_mitigation_multiplier` × `composite_mitigation_from_player_multiplier`** — this is the *DR-multipliers bucket*. Versatility, stoneform, pain_suppression, AoE-only avoidance multiplier live here. Class/spec overrides add their flat-DR buffs here too (Defensive Stance, Indomitable, Shield Wall, Demo Shout debuff, etc.).
3. **`target_mitigation`** — armor + block, both via the same `calculate_armor_resist` curve. Spec overrides (warrior) add crit-block as a second pass.

Each `1.0 - x` factor in `composite_mitigation_multiplier` is its **own** bucket — they stack multiplicatively. There is no "additive DR pool" inside this function. Within a single buff's effects, multiple effect rows on the same spell can stack additively before being applied as a single multiplier — that's the only place additive bucketing happens at this layer.

## Rating → percent

SimC pulls ratings-per-percent from DBC `combat_ratings_multiplier_by_class` × the level-derived `combat_rating_constant`, then applies the diminishing-returns curve from `def_dr.*`. Linear up to the DR threshold (~30% for secondaries in Midnight), then increasingly expensive past each breakpoint (30 / 39 / 47 / 54 / 66 → +0/+10/+20/+30/+40% rating cost; hard cap at 113.3%).

The "uniform `rating_per_pct: 100`" in `simf/data/constants.yaml` is a self-consistent fudge — it works for relative comparison between two builds of the *same* character at the *same* gear level, but distorts cross-stat weighting because real conversion rates differ per stat and DR makes them nonlinear.
