# F12 — Shield Block Value Sourcing Recon
**Date:** 2026-05-19
**Status:** Recon complete. F12 is well-scoped engineering, not a research blocker.
**Context:** Closes the gap left by the F11 revert (k_calibration_mastery_chain_2026_05_19.md).
The empirically load-bearing `mastery_block_value_scaling: 0.5` fudge masks a missing
shield-item contribution to `block_value`. F12 sources that contribution from item data
so the mastery chain can be re-attempted.

## Question

Where does shield `block_value` come from in modern WoW (Midnight 12.0.5), and which
data source should simf use to per-item it?

## Sources tested

| Source | Result |
| --- | --- |
| Wowhead HTML (`/item=237831`) | Item name only; tooltip is JS-rendered. **No block field.** |
| Wowhead XML (`/item=237831&xml`) | Returns base armor + stats. **No block field.** Doesn't honor bonus_ids reliably (armor=931 for a ~ilvl 671 crafted shield). |
| SimC `item_data.inc` GitHub raw | >10 MB, exceeds WebFetch limit. Not searchable from here. |
| **SimC engine source (`engine/player/player.cpp:1681`)** | **Revealed the formula. Block value is NOT a stored item field — it is derived from shield armor at runtime.** |

## Finding — the SimC model

### Block value (flat)

`engine/player/player.cpp:1681`:

```cpp
// Currently block reduction is 2.5x the armor value of the shield
if ( items[ SLOT_OFF_HAND ].dbc_inventory_type() == INVTYPE_SHIELD )
  base.block_value = items[ SLOT_OFF_HAND ].stats.armor * 2.5;
else
  base.block_value = 0;
```

**Equation:** `block_value (flat) = shield.armor × 2.5`

This is stored on the player as `current.block_value`. `player_t::composite_block_value()`
just returns this attribute (player.cpp:5586).

### Block reduction (%) on a blocked hit

`engine/class_modules/sc_warrior.cpp:9145-9146`:

```cpp
double block_value = s->target_block_value;
double block_resist = util::calculate_armor_resist( block_value, s->action->player->current.armor_coeff, 2.0 );
```

Where `calculate_armor_resist` (util.cpp:3713):

```cpp
double resist = armor / ( armor + armor_coeff );
resist *= multiplier;          // 2.0 for block
resist = clamp( resist, 0.0, MAX_ARMOR_DAMAGE_REDUCTION );
return resist;
```

**Equation:** `block_reduction_pct = clamp( (block_value / (block_value + armor_coeff)) × 2.0, 0, MAX_ARMOR_DR )`

`armor_coeff` is the **attacker's** armor coefficient (note `s->action->player->current.armor_coeff`),
which is a level-based constant. So block effectiveness depends on enemy level — a +14 key
gives a different % reduction than a +20 key for the same shield.

### `MAX_ARMOR_DAMAGE_REDUCTION` (the cap)

`engine/sc_enums.hpp`:

```cpp
// Maximum damage reduction from armor / block
constexpr auto MAX_ARMOR_DAMAGE_REDUCTION = 0.85;
```

simf already has this — `constants.yaml: armor.max_armor_dr: 0.85`. Matches SimC.

### Talent modifiers (already-known)

- **Brace for Impact** (Protection talent): each stack multiplies `block_value` by `effectN(2).percent()`
  (sc_warrior.cpp:8916-8920).
- **Mastery — Critical Block**: per the F11 audit
  (`docs/validation/simc_warrior_mastery_2026_05_19.md`), SimC's structural values are
  `mastery_block_value_scaling=0.0`, `mastery_block_chance_scaling=0.5`,
  `mastery_crit_block_scaling=1.5`. This is what the F11 attempt tried to ship and reverted.

### Strength does NOT affect block value

Vanguard adds `STR × N%` to `composite_bonus_armor` (character armor), **not** to shield armor,
and not directly to block_value (sc_warrior.cpp:8867-8891). The block_value formula reads
`items[SLOT_OFF_HAND].stats.armor` — the shield item's raw armor stat — not the character's
total armor. Vanguard is a separate axis.

## Why simf's current model misfires

simf already routes block through the armor curve (`core/mitigation.py:210`):

```python
block_dr = calculate_armor_resist(state.cached_block_value_rating, K, multiplier)
damage *= 1 - block_dr
```

Where `block_value_rating()` (character.py:237-258) **inverts the armor curve from a
flat baseline**:

```python
def block_value_pct(self) -> float:
    return c["base"]["block_value_pct"] + self.mastery_pct() * c["base"]["mastery_block_value_scaling"]
    # = 0.30 + mastery_contribution

def block_value_rating(self) -> float:
    bv_pct = self.block_value_pct()
    k = c["armor"]["k_constant"]
    return bv_pct * k / (1.0 - bv_pct)    # inverts: pct = R/(R+K) → R = pct*K/(1-pct)
```

So simf:
- Starts with a target % (`0.30 + mastery_contribution`)
- Inverts to derive an armor-equivalent rating
- Runs it through `calculate_armor_resist` with `multiplier=1.0` (regular block) or
  `2.0` (crit block, with the 0.85 cap correctly applied)

This is **mostly correct** — for regular block it's algebraically identical to the flat
30%, and for crit block it gives the right armor-curve sublinear response.

**The actual gap:** simf derives `block_value_rating` from a **constant target percentage**,
ignoring per-shield armor. SimC derives `block_value` directly from `shield.armor × 2.5`,
which scales with shield ilvl. Two consequences:

1. Per-shield gear comparisons can't distinguish two shields with different armor.
2. The `mastery_block_value_scaling: 0.5` fudge has to absorb the missing shield-armor
   contribution, which is why removing it (F11) regressed RMSE — the formula has the
   right shape but the wrong magnitude axis.

There's also a subtler issue: SimC's block resist uses the **attacker's** `armor_coeff`
(`s->action->player->current.armor_coeff`, sc_warrior.cpp:9146), not the player's K. simf
uses `c["armor"]["k_constant"]` (player K). For the empirically calibrated K=3430 this
may wash out, but at extreme key levels the curve will drift.

## Implementation path for F12 proper

### Data needed

1. **Shield armor for the equipped off-hand item.** simf's `item_db.py` already pulls armor
   via `fetch_item_stats_wowhead` (which IS bonus-id-aware — confirmed), but
   `resolve_equipped_stats` (item_db.py:374) **aggregates across all slots** into a single
   `armor_from_gear` total. F12 needs per-slot armor preserved for the SLOT_OFF_HAND shield
   specifically — a parallel `resolve_per_slot_armor()` returning `{slot: armor}` is the
   minimal change.
2. **`armor_coeff` by enemy level.** Need to add a constant to `constants.yaml` keyed by
   M+ level (or, simpler, a curve / lookup). SimC computes it from a DBC curve; for simf
   a sparse table by key level (e.g. K+2/+10/+14/+18/+22) is enough.
3. **MAX_ARMOR_DAMAGE_REDUCTION.** SimC constant — confirm value (likely 0.85 or 0.95;
   needs a 5-min source check).

### Code changes

1. **`io/item_db.py` — preserve per-slot armor.** Either return a slot→stats mapping, or
   expose a sidecar that holds OFF_HAND specifically. The aggregated `armor_from_gear`
   total should keep working unchanged for downstream callers.
2. **`core/character.py` — replace the `block_value_rating()` derivation.** Instead of
   inverting from a flat baseline, compute `block_value_rating = shield_armor × 2.5`
   directly. The downstream `calculate_armor_resist` chain in `mitigation.py:210` stays
   put — it's already correct.
3. **`data/constants.yaml`:**
   - Add `block_value_armor_multiplier: 2.5` (the SimC coefficient).
   - Set `mastery_block_value_scaling: 0.0` (the SimC value, was 0.5 fudge).
   - (Optional, later) add per-key-level `armor_coeff` table if the player-K
     approximation drifts at extreme key levels — defer until measured.
4. **`core/mitigation.py` — no change required.** Already uses `calculate_armor_resist`
   with the right multipliers (1.0 / 2.0) and cap (0.85).

### Calibration check

Once shipped, re-run the K sweep with `mastery_block_value_scaling=0.0` (the SimC-truthful
value). If RMSE at canonical K=3430 holds at ~0.066 (or improves), the F11 mastery chain
can be re-shipped. If it regresses, F12 is incomplete — likely missing the per-enemy
armor_coeff scaling, or `MAX_ARMOR_DR` is wrong.

## Validation pathway (post-implementation)

1. **Pick a known shield** — start with Brutoh's Spellbreaker's Rebuke (item 237831,
   bonus_ids 12214/12497/12066/8960/12384/8790/13622).
2. **Get its actual armor.** Wowhead XML returned base armor=462 (item-level 44 base) and
   even with bonus_ids: armor=931 / ilvl=285 — both are clearly wrong (this is a crafted
   ~ilvl 671 shield). **Wowhead's bonus_id-honored XML is unreliable for crafted items.**
   Need a better source — likely the Blizzard `/data/wow/item/{id}?bonus_lists=...` endpoint
   (using credentials in `~/.simf/blizzard.yaml`).
3. **Compute expected block_value** = armor × 2.5.
4. **Compare to in-game tooltip block value** — Brutoh has the character, can read off
   the C panel and confirm the number.

## Recommendation

F12 is a **clean engineering ticket** — the formula is known, the data path is the
existing Wowhead/Blizzard item lookup, the only architectural work is preserving per-slot
armor. Estimated ~half-day with tests.

The right order is:

1. **F12.1** — preserve per-slot armor in `item_db.py`. Single-purpose commit, test that
   `resolve_equipped_stats` still returns the same totals while exposing `slot_armor[]`.
2. **F12.2** — add formula + armor_coeff curve to constants and `mitigation.py`. Per-shield
   block_value computed once at character load.
3. **F12.3** — drop `mastery_block_value_scaling` to 0.0 (the SimC value) and re-sweep K.
   Atomic: ship 12.1+12.2+12.3 together, since reverting 12.3 is meaningless without 12.1+12.2.

## Open questions for the next session

- **What is `armor_coeff` for level 80 enemies at M+ key levels?** SimC computes from
  DBC curves; we need either a small lookup table or to confirm that the existing
  player K=3430 is empirically close enough. Worth one more SimC fetch into `dbc.cpp`
  to see the curve shape.
- ~~**What is `MAX_ARMOR_DAMAGE_REDUCTION`?**~~ — **Resolved: 0.85.** Same constant
  for armor and block. simf already matches.
- ~~**Does the Blizzard item API return bonus_id-adjusted armor?**~~ — **Resolved
  via the simpler Wowhead path (no Blizzard creds needed).** simf's existing
  `fetch_item_stats_wowhead(item_id, bonus_ids)` (item_db.py:231) uses the URL
  format `https://www.wowhead.com/item={id}?bonus={a}:{b}:{c}&xml` (note `?bonus=`
  not `&bonus=`) and IS bonus-id-aware. Verified on Brutoh's shield:
  - Without bonus_ids: armor=89, strength=5, stamina=7 (base ilvl)
  - With bonus_ids: armor=931, strength=60, stamina=842, +40 crit, +40 haste
  The exact armor magnitude (931 — does this match Brutoh's in-game tooltip?)
  is the one ground-truth piece still missing; Brutoh can confirm by opening C
  in-game and reading the shield tooltip. The data PATH works regardless.
- **Per-enemy armor or aggregate**: SimC uses the attacker's armor_coeff for block.
  simf uses player K. At the calibrated K=3430 this is probably close enough at
  central key levels, but worth a sweep once F12 is in to see if the residual depends
  on key level.
