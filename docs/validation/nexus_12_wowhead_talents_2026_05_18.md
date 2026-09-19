# Nexus+12 (Wowhead-build talents) — empirical mitigation analysis

**Log:** `examples/WoWCombatLog-051826_212525.txt`
**Run:** Nexus-Point Xenas +12, started 2026-05-18 22:02:17, completed 22:26:12 (1436s)
**Outcome:** Completed. Brutoh died once at 22:03:15 (~58s in — first-pull wipe).
**Talents:** Wowhead-recommended build (different from `brutoh-actual`). Exact talent string not extractable — log has `ADVANCED_LOG_ENABLED=0`, so no COMBATANT_INFO events.

## Headline finding

**The talent loadout materially changes the empirical K.** Comparing physical no-CD events:

| Log | Talents | K back-solved (DS=0.15) | K back-solved (DS=0.20) |
|---|---|---:|---:|
| Windrunner Spire +12 | brutoh-actual | 3116 | 3431 |
| Algeth'ar Academy +12 | brutoh-actual | 3237 | 3569 |
| **Nexus-Point Xenas +12** | **Wowhead build** | **2557** | **2797** |

The Wowhead build's effective K is ~600 lower than `brutoh-actual` for the same character. That's a **19% delta** in armor effectiveness, which cannot be explained by RNG/measurement noise (1432 events).

## Hypothesis

The Wowhead build likely takes one or more armor-multiplier talents that `brutoh-actual` does not:

- **Reinforced Plates** (class talent, spell 382939, effect 2): +5% armor (subtype 101 "Modify Armor%")
- **Armor Specialization** (Prot spec talent, spell 1234769, effect 1): +6% armor
- **Vanguard** (Prot spec passive, spell 71): adds bonus armor from Strength

Stacking +5% × +6% = +11.3% effective armor. With Brutoh's 5584 displayed armor, that would push effective armor to ~6210, which lowers the K needed to match the same DR.

Verification math: for the no-CD physical bucket (mit_ratio 0.0817 incl block/absorbs, dmg_taken 0.2563 excl block/absorbs):
```
(1 - armor_DR) × (1 - vers) × (1 - DS) = 0.2563
With armor = 5584 × 1.06 × 1.05 = 6213, DS = 0.15:
  → armor_DR = 0.686
  → K = 6213 × (1/0.686 - 1) = 2843  (still lower than 3430 SimC DBC; remaining gap likely Vanguard)
```

## Implications for simf

`Character.armor_from_gear` is currently sourced from the SimC export's `gear_armor` field, which is **gear-only** (pre-talent-multipliers). `total_armor()` applies only the Earthen +10% racial. **Prot Warrior armor-multiplier talents are unmodeled.**

When the user switches talent loadouts (e.g., to a Wowhead build that picks armor-multiplier talents), simf's predicted K stays at 3430 with armor=5584 (post-Earthen) → predicts armor_DR ≈ 61.9%. Real game would show ~68% DR with the additional multipliers. **simf would over-predict damage taken by ~7pp** in that scenario.

## Magic-school DR also varies

| School | Nexus+12 (Wowhead) | WR+12 (brutoh-actual) |
|---|---:|---:|
| Shadow | 33.0% effective DR | 17.0% |
| Arcane | 36.9% | 24.9% |
| Holy | 32.3% | (insufficient events) |
| Fire | (insufficient events) | 26.8% |
| Nature | (insufficient events) | 33.7% |

Effective magic DR roughly DOUBLES from Brutoh-actual to Wowhead build. Possible causes:
1. Talents that add school-specific magic DR (e.g., Honed Reflexes, Tough as Nails)
2. Different party composition / different auras up (party comps differ run-to-run)
3. Indomitable (4% all-damage DR) may interact differently with school distribution

Without knowing the exact talents, we can't fully attribute. Current `defensive_stance_dr: 0.15 all-schools` is correct per spell data; the additional 15-20pp magic DR in Nexus is unmodelled passive/talent/party mitigation.

## Death reconstruction (first pull, 22:03:15)

Brutoh was being hit by **3 Shadowguard Defenders** simultaneously, each landing ~50k auto-attacks. Plus various other adds (Nexus Adept "Crusading Strikes", Shadowguard "Frost Splinter"). Buffs active at moment of death: Brace For Impact, Ignore Pain, Shield Slam (Phalanx), Atonement (Disc priest), Rallying Cry, Avatar, Solarflare Prism trinket. He had ~167k HP coming into the final swing; three SWING_DAMAGE_LANDED events of 24760 + 50226 + 48241 = 123k physical damage in the same tick wiped him.

This is a textbook "first pull is too big" wipe — three mob auto-attacks landing simultaneously is unavoidable damage that no amount of single-target mitigation can fully soak. The interesting analytical question (deferred): would simf's death-reconstruction pipeline have flagged this pull's incoming-DPS profile as exceeding survivability budget?

## Recommendations

Tracked as tasks for follow-up:

1. **Add Prot Warrior armor-multiplier talents to simf** — Reinforced Plates +5%, Armor Specialization +6%. Map them via `talent_loadouts` in constants.yaml. Re-run empirical analysis to confirm K converges to SimC's 3430.
2. **Add Vanguard strength→armor scaling** for Prot Warrior. Spec passive, always-on.
3. **School-specific party_magic_dr table** — even after talent fixes, schools clearly differ. Likely a YAML table indexed by school + per-realm-typical-party-comp.
4. **Talent inference for ACL-off logs** — when COMBATANT_INFO is missing, infer talent set from cast patterns (Phalanx casts → Phalanx talent active; Champion's Spear → that talent; etc.). Already partly modelled in log_replay; extend.
