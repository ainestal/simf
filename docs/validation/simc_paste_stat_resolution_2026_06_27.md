# /simc paste stat resolution — the paste path was gear-only, omitting base character stats

**2026-06-27 (diagnosis) → corrected + fixed 2026-06-28 with in-game ground truth.**
Found while dogfooding the gem suggester on a real Guardian (`AnonGuardian1`, EU/AnonRealm1).
Three distinct bugs surfaced on the `/simc` paste path; all are addressed here +
the agility-drop sibling fix.

## Symptom

Loading AnonGuardian1's current-gear `/simc` paste (16 items, mostly ilvl 289) produced
stats far below their real character-sheet values:

| stat | paste-resolved | in-game tooltip | gap |
|---|---|---|---|
| agility | 1,072 | **2,011** | +939 |
| stamina | 17,327 | **24,179** | +6,852 |
| bear-form HP | ~560k | **749,115** | −25% |

## Root cause — gear-only resolution (NOT "Wowhead returns half")

> ⚠️ The first-pass diagnosis (2026-06-27) blamed Wowhead for serving ~half-scale
> primary/stamina. **That was wrong.** The in-game tooltip (provided 2026-06-28)
> shows Wowhead's *gear* stats are correct; the paste path was simply **gear-only**.

A `/simc` paste resolved from item IDs sums **gear item stats only**. A character's
real stats are `gear + base character stats (level 90) + always-on buffs (Mark of
the Wild …) + gem/enchant stats`. simf added none of the non-gear parts, so:

- **Base character stats** are the dominant gap: ~6,852 stamina and ~939 primary at
  level 90. These do not appear on any item, so neither Wowhead nor Blizzard returns
  them — they come from the character's level/class.
- Gems + enchants add a smaller amount (mostly secondaries; the meta gem adds ~32
  primary).

Decisive ground-truth check (confirms simf's HP model + that gear stats are right):

```
tooltip stamina 24,179 × hp_per_stamina(22) = 531,938 ≈ 535,091 humanoid HP ✓
                                              × Bear 1.40 = 744,712 ≈ 749,115 bear ✓
gear stamina 17,327 + base 6,852            = 24,179 (self-consistent) ✓
```

A "Wowhead is half" world would *overshoot* the tooltip (gear 2× + base > 24,179), so
it's refuted. (No Wowhead `&lvl`/`&ilvl` param changed its values, and the Blizzard
API returns `None` for these new items — both consistent with gear stats being correct
and the gap being non-gear base stats.)

## The fixes (this PR — supersedes the agility-only PR #212)

1. **Agility added to the copy list.** `load_from_simc` copied `strength` but not
   `agility`, so every agility tank (Guardian/Brewmaster/VDH) paste-loaded with
   agility = 0 (primary stat dropped). (Was PR #212; folded in here.)
2. **Base character stats added** on the resolver path:
   `stat_conversion.paste_base_stamina = 6852`, `paste_base_primary = 939` (applied
   to the spec's primary — agility for agi tanks, strength for plate). Calibrated
   from AnonGuardian1's tooltip; **single-point, medium confidence** (folds in always-on
   buffs + the meta gem's primary). After the fix the paste reproduces the tooltip
   **exactly**: agility 2,011, stamina 24,179.
3. **`stats_estimated` flag + caveat** (kept) — the resolver path is still an estimate
   (gems/enchants unresolved, base approximated), so the load is flagged and a caveat
   rides the load toast. The export-`gear_stats` path and the log/WCL hydrate path are
   left untouched (the latter carries exact totals already).

## Residual / follow-ups

- **~4% HP over** for AnonGuardian1 after the fix (modeled bear 781,949 vs real 749,115).
  This is a *separate* pre-existing issue: simf applies the Tauren +5% HP racial on
  top of `stamina × 22`, but the in-game bear HP ≈ `stamina × 22 × 1.40` with no
  visible +5% (i.e. real hp/stam ≈ 21 with the racial, or the racial shouldn't stack
  here). Worth a small `hp_per_stamina`/racial recheck against the tooltip; it was
  masked before because gear-only stamina was 25% low.
- **Base stats are single-point** (one Tauren Guardian, lvl 90). Stamina base is
  ~class-independent; primary base is similar in magnitude across classes, so the
  same constants apply approximately to plate tanks — but a second data point (a
  warrior/DK tooltip) would firm this up. The `stats_estimated` flag keeps it honest.
- **EC detection still needs a log.** Even with correct stats, a paste can't detect
  Elune's Chosen (no buff data), so the paste-path gem/enchant ranking omits haste's
  Ironfur-uptime value. The EC-aware recommendation comes from the log/WCL path.
- **Export-`gear_stats` path** is also gear-only in SimC's model, so it likely
  undercounts base too — but it's left untouched here (not validated; avoids
  perturbing the calibration corpus, which loads via that path / via logs).
