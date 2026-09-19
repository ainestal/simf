# Season 2 "+25% player health / +25% creature damage" rescale — no-op sanity check (2026-07-26)

## Summary

Blizzard confirmed a global rescale for Season 2: **+25% player health and
+25% creature damage at max level** (official forum post, cross-checked
2026-07-26 — see ROADMAP.md's Season 2 Readiness section). ROADMAP.md's
bucket A asked for one sim confirming this nets to a no-op on eHP/death_rate
before launch, to prevent a launch-week false alarm ("did the model just
break, or did the game just get bigger numbers?").

**Result: it's a real no-op, confirmed empirically, provided the rescale is
applied correctly** — and one genuine methodological gotcha plus one real
(currently inert) caveat were found along the way.

## Method

`scripts/season2_health_damage_rescale_check.py` runs paired (common-random-
number, same seed=42, 5000 iterations) Monte Carlo sims against a real
Prot Warrior character (Brutoh's stat block) and the `m+_boss_tankbuster`
damage profile, comparing:

1. **baseline** — no changes.
2. **scaled_naive** — character `max_hp` ×1.25 (via the raw `max_hp_override`
   field), damage profile ×1.25 (`scale_damage_profile` — mob swings, casts,
   and tank-busters), healing profile unchanged.
3. **scaled_consistent** — same as (2), but the healing profile's absolute
   `HealingExternal.amount` fields (flat absorbs in raw HP units) are also
   scaled ×1.25.

The healer's baseline output is deliberately reduced from the default
`m+_high_key_healer` profile (`baseline_hps_pct_of_dtps` 1.2→0.95, budget
capacity/refill also reduced) — a real Brutoh-geared character never dies
against the standard profile at all (`death_rate` pinned at 0% baseline and
scaled alike), which would make the comparison uninformative. The reduced
profile produces a real, non-saturated `death_rate` (~3.5%) to compare.

## Finding 1: correctly scaled, it's an exact no-op

| metric | baseline | scaled_naive | scaled_consistent |
|---|---|---|---|
| death_rate | 3.50% | 3.50% | 3.50% |
| mean_dtps | 40,621 | 50,777 (×1.2500) | 50,777 (×1.2500) |
| etmi_12 | 97747.6167 | 97747.6167 | 97747.6167 |
| p99_10s_window | 957,917 | 1,197,396 (×1.2500) | 1,197,396 (×1.2500) |
| p5_min_hp_pct | 2.71% | 2.71% | 2.71% |

`death_rate`, `etmi_12`, and `p5_min_hp_pct` (all percentage/ratio-of-max_hp
metrics) are **bit-identical** between baseline and scaled. `mean_dtps` and
`p99_10s_window` (absolute-HP metrics) scale by **exactly** 1.2500×, matching
the rescale factor precisely — not approximately. This matches the analytical
expectation: armor/avoidance/block/DR% depend on rating vs. attacker level,
none of which the rescale touches, so the mitigation *fraction* is invariant;
scaling both the numerator (max_hp) and the denominator-side inputs (raw
incoming damage) by the same factor leaves every ratio-based outcome (does
this hit kill you, what fraction of a key pull's damage lands) unchanged.

## Finding 2 (methodological gotcha): scale the raw `max_hp_override`, not `Character.max_hp()`

An earlier iteration of this check scaled `brutoh.max_hp()` (the fully
computed, post-talent-multiplier value) and fed it back in as the scaled
character's new `max_hp_override`. `Character.max_hp()` re-applies the
Indomitable talent's +4% max-HP multiplier on top of `max_hp_override`
whenever it's called — so doing this **double-applies** that multiplier on
the scaled character, producing an effective ×1.30 scale (1.25 × 1.04) instead
of the intended ×1.25. Caught by checking the printed ratio
(`scaled_char.max_hp() / brutoh.max_hp()`) against the expected 1.2500 and
finding 1.3000 instead. Fixed by scaling the **raw** `max_hp_override` field
directly. Worth flagging for whoever eventually wires a real Season 2 rescale
into the engine (if it ever needs to be a first-class code path rather than a
one-off check): the same double-count trap applies to any code that reads
`Character.max_hp()` and writes the result back into `max_hp_override`.

## Finding 3 (real, currently inert caveat): the healer profile has two hardcoded absolute values

`data/profiles/healing/m+_high_key_healer.yaml` has two `HealingExternal`
entries with `type: absorb, amount: 8000000` (flat HP amounts) — everything
else in `HealingProfile` (`baseline_hps_pct_of_dtps`,
`reactive_burst_pct_of_max_hp`, the healer-budget fields) is already a
percentage of DTPS/max_hp and self-scales automatically. These two absolute
values would **not** automatically track a real Season 2 rescale.

Tested whether this actually matters: ran the same comparison with the
healing profile's absorb amounts also scaled ×1.25 (`scaled_consistent`) vs.
left unscaled (`scaled_naive`). **Bit-identical results in every scenario
tested** — moderate intensity (3.5% death_rate) and a 2.2× stress-test
(100% death_rate, chosen because scaling the profile further just pins both
ends at 0%/100% with nothing to compare). The 8,000,000 absorb is
~9× the character's own max_hp and vastly larger than any single 10s window's
raw incoming damage even at 2.2× intensity — it never becomes the binding
constraint in this profile, so whether it's nominally 8,000,000 or
10,000,000 doesn't change any outcome tested here.

**This is a real caveat, not a false alarm, but it's currently inert**: if a
future damage profile (or a real, much harsher Season 2 dungeon) pushes
incoming damage-in-window close to or past 8,000,000, the unscaled absorb
would start reading as proportionally weaker than intended post-rescale, and
`scaled_consistent`-style scaling would matter. Re-run this script (or add a
harsher `--stress-mult`) once real Season 2 damage profiles exist, rather
than assuming this stays inert forever.

## Conclusion / action

- **No engine change needed for the rescale itself** — simf's mitigation
  math is correctly scale-invariant under a uniform health+damage rescale.
- **Do not encode the +25%/+25% numbers into `constants.yaml`** — matches
  ROADMAP.md's own "explicitly do NOT do right now" bucket C item (PTR
  numbers will move before launch); this check only confirms the *engine
  behavior* would be correct if/when real Season 2 damage profiles are built,
  not that any specific number should be hardcoded now.
- **When real Season 2 damage profiles are authored**, remember: `data/
  profiles/healing/*.yaml`'s absolute `HealingExternal.amount` fields (and any
  other hardcoded absolute-HP values elsewhere) need their own explicit
  rescale — they will not track a global health/damage multiplier
  automatically the way percentage-based fields do.
