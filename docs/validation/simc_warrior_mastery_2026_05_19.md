# SimC source verification — Prot Warr mastery chain (2026-05-19)

Primary-source verification of the F11 hypothesis using SimulationCraft's
actual source code on the `midnight` branch. The audit's "per Wowhead /
Icy Veins" framing in the original F11 entry was a soft reference; this
doc replaces it with bulletproof code excerpts.

## TL;DR

Mastery: Critical Block (spell 76857) for Protection Warrior in Midnight
12.0.5 modifies **three** things, none of which is block VALUE:

| Effect | What it does | SimC accessor | Coefficient (effectN().mastery_value()) |
|---|---|---|---|
| effectN(1) | Critical Block Chance | `cache.mastery() × effectN(1).mastery_value()` | **1.5** |
| effectN(2) | Block Chance | `cache.mastery() × effectN(2).mastery_value()` | **0.5** |
| effectN(5) | Melee Attack Power | `1.0 + effectN(5).mastery_value() × cache.mastery()` | (DPS, not survivability) |

**Block VALUE has zero mastery contribution.** F11's structural hypothesis
is confirmed; what was missing in the original audit was that mastery's
contribution surfaces in TWO chance dimensions (block + crit block), not
one. simf currently models neither block-chance scaling nor uses the
correct crit-block coefficient.

## Primary source

SimulationCraft `engine/class_modules/sc_warrior.cpp` on branch `midnight`,
fetched 2026-05-19. Line numbers as fetched (file may drift).

### Block chance from mastery (line 8893)

```cpp
// warrior_t::composite_block ================================================

double warrior_t::composite_block() const
{
  // this handles base block and and all block subject to diminishing returns
  double block_subject_to_dr = cache.mastery() * mastery.critical_block->effectN( 2 ).mastery_value();
  double b                   = parse_player_effects_t::composite_block_dr( block_subject_to_dr );

  // shield block adds 100% block chance
  if ( buff.shield_block -> up() )
  {
    b += spell.shield_block_buff -> effectN( 1 ).percent();
  }
```

Mastery contributes to block chance via `effectN(2).mastery_value()` and
is subject to a diminishing-returns curve (`composite_block_dr`). simf
does not model either the mastery contribution or the block-chance DR.

### Block VALUE from mastery (line 8910) — **none**

```cpp
// warrior_t::composite_block_value ===========================================

double warrior_t::composite_block_value( const action_state_t* s ) const
{
  double bv = parse_player_effects_t::composite_block_value( s );

  if ( buff.brace_for_impact->check() )
  {
    bv *= 1.0 + buff.brace_for_impact->check() *
                  talents.protection.brace_for_impact->effectN( 1 ).trigger()->effectN( 2 ).percent();
  }

  return bv;
}
```

Block value is the base from parsed effects (shield item + Shield Block 2
spell effectN, etc.) and the Brace for Impact talent stack multiplier.
**Mastery does not appear.** The current simf coefficient
`mastery_block_value_scaling: 0.5` has no basis in SimC source.

### Critical block chance from mastery (line 8990)

```cpp
auto crit_block_chance = cache.mastery() * mastery.critical_block->effectN( 1 ).mastery_value();

if ( rng().roll( block_chance ) )
{
  if ( rng().roll( crit_block_chance ) )
    return BLOCK_RESULT_CRIT_BLOCKED;
  else
    return BLOCK_RESULT_BLOCKED;
}
```

Mastery × `effectN(1).mastery_value()`. Per Wowhead spell data (also
fetched 2026-05-19), `effectN(1).mastery_value() = 1.5`. simf's F7
fix set this at 1.0; correct value is **1.5**.

### Tooltip cross-check

Warcraft Wiki / Wowhead in-game tooltip for Mastery: Critical Block
(verified 2026-05-19): *"Increases your chance to block by 4.0% and your
chance to critically block (blocking twice the amount) by 12.0%. Also
increases your attack power by 8.0%."*

The tooltip reference mastery is 8% (where SP mod × mastery gives the
shown bonus):
- Block chance: 0.5 × 8 = 4.0% ✓
- Crit block chance: 1.5 × 8 = 12.0% ✓
- Attack power: 1.0 × 8 = 8.0% ✓ (implies AP coefficient is 1.0)

Three independent sources (SimC source, Wowhead spell data, in-game
tooltip) all converge.

## Implications for simf

### What's wrong today (constants.yaml)

```yaml
base:
  block_chance: 0.10           # base only — mastery NOT added
  mastery_block_value_scaling: 0.5    # WRONG: should be 0.0
  mastery_crit_block_scaling: 1.0     # WRONG: should be 1.5
  # MISSING: mastery_block_chance_scaling (should be 0.5)
```

### What's right (per SimC source)

```yaml
base:
  block_chance: 0.10           # base; mastery contribution applied dynamically
  mastery_block_chance_scaling: 0.5   # NEW (SimC effectN(2).mastery_value())
  mastery_crit_block_scaling: 1.5     # SimC effectN(1).mastery_value() — was 1.0
  mastery_block_value_scaling: 0.0    # SimC has no mastery in composite_block_value
```

Wiring required in `character.py`:

```python
def base_block(self) -> float:
    c = load_constants()
    if self.class_spec == "protection_warrior":
        from_mastery = self.mastery_pct() * c["base"]["mastery_block_chance_scaling"]
        # TODO: apply diminishing returns (F14)
        return c["base"]["block_chance"] + from_mastery
```

### Per-event impact (at Brutoh's ~16% mastery)

| Quantity | Current simf | SimC-correct |
|---|---|---|
| Block chance | 10% | 10% + 0.16 × 0.5 = 18% (pre-DR) |
| Block VALUE | 30% + 0.16 × 0.5 = 38% | 30% (flat) |
| Crit block chance | 16% (F7 coefficient 1.0) | 24% (coefficient 1.5) |
| Crit block VALUE | 76% via curve | 60% via curve |

Net per blocked event:
- Current model: 10% × (0.84 × 0.38 + 0.16 × ~0.76) ≈ 4.4% expected mitigation
- SimC model: 18% × (0.76 × 0.30 + 0.24 × ~0.60) ≈ 6.7% expected mitigation

Direction: SimC model should mitigate MORE per pull (~50% more total block
mitigation than current simf). Empirical calibration may want a higher K
to compensate, possibly shifting the empirical minimum closer to canonical
3430 — but this needs a sweep to verify after the chain is rewired.

## Why the previous F11 sweep regressed

The earlier F11 attempt (constants flip 0.5 → 0.0, this session) removed
the value-side fudge in isolation. Block VALUE went 38% → 30% with NO
compensating gain on the chance side. That's pure mitigation loss —
exactly the systematic under-mitigation seen in the calibration
(RMSE 0.062 → 0.106). To match SimC, the value removal MUST land with
the chance additions (mastery → block chance + crit block 1.5).

This is why F11/F12 should not have been ranked as the most critical fixes 
either, and why this session's revert was correct given the constraints.
The correct change is a coupled refactor of the mastery chain, not a
single-coefficient flip.

## Remaining open

- **Block chance diminishing returns** (`composite_block_dr`). SimC applies
  a DR curve to block chance similar to dodge/parry. Filed as F14. Need
  to find the curve constants in DBC or class spec data.
- **Brace for Impact block value multiplier** is already modeled in simf
  (via talent damage reduction), but should be cross-checked against
  SimC's `talents.protection.brace_for_impact->effectN(1).trigger()->effectN(2).percent()`
  pattern (which modifies BLOCK VALUE, not damage-taken).

## Decision

**Do not ship engine changes in this session.** The mastery-chain redesign
is multi-component (3 coefficients changed/added + block chance DR + base
block wiring) and the calibration impact won't be predictable until they
land atomically. Schedule the work for the session after Brutoh provides
a fresh key log (Algeth'ar Academy +13/+14 with ACL on, per the open ask),
so the calibration target set is current and the multi-axis change can
be validated empirically before being trusted.

Sources:
- `engine/class_modules/sc_warrior.cpp` lines 8893, 8910, 8990 — fetched
  2026-05-19 from `midnight` branch via
  `https://raw.githubusercontent.com/simulationcraft/simc/midnight/engine/class_modules/sc_warrior.cpp`
- `https://www.wowhead.com/spell=76857` — Mastery: Critical Block effect breakdown
- `https://warcraft.wiki.gg/wiki/Mastery:_Critical_Block` — in-game tooltip
- Prior session: `docs/validation/k_calibration_f11_revert_2026_05_19.md`
