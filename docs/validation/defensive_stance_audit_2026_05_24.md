# Defensive Stance audit — 2026-05-24

## Hypothesis (briefed)

Icy Veins reads Prot's Defensive Stance as **"20% flat all-schools + 5% extra
magic + 15% extra on hits ≥20% max HP."** simf's `defensive_stance_dr: 0.15`
(applied to all schools) was suspected to be only the big-hit clause, with
the base 20% layer missing — a candidate explanation for the residual ~10pp
physical mitigation gap from sessions 22/23.

## Verdict: REFUTED

simf's current encoding (`constants.yaml:597` `defensive_stance_dr: 0.15`,
applied unconditionally to all schools at `mitigation.py:236-239`) is the
**complete and correct** representation of the live Defensive Stance buff.

## Citation chain (all retrieved 2026-05-24)

1. **Wowhead spell 386208** (DBC primary): Effect 1 = `Mod % Damage Taken
   (Arcane, Fire, Frost, Holy, Nature, Physical, Shadow) Value: -15%`.
   No 20% effect exists on this spell. Effect 2 (`Mod Damage Done % -10%`)
   is the offense penalty disabled for Prot.
2. **SimC `parse_effects(buff.defensive_stance, effect_mask_t(true).disable(2, 5, 6))`**
   (per internal `docs/simc-reference/warrior_target_mitigation.cpp` +
   `AUDIT.md` F4). Effect 1 is the only damage-taken DR active; effects 5/6
   (Stance Mastery chunk DR, the "≥20% maxHP" clause) are explicitly
   disabled by SimC with the comment `// Stance Mastery for Defensive stance
   is not working in game as of Dec 04 2025`. simf correctly follows SimC.
3. **simf application path**: `src/simf/core/mitigation.py:236-239` applies
   `defensive_stance_dr` to the damage variable inside the
   `protection_warrior` block with no `event.school` guard — i.e. all schools,
   matching DBC Effect 1's school mask 127.

## Mapping briefing claims to DBC reality

- **"+15% on hits ≥20% max HP"** = Effects 5/6 (Stance Mastery chunk DR),
  broken in-game per the December 2025 SimC comment. Correctly excluded.
  If Blizzard fixes the in-game implementation, this becomes a re-open
  trigger.
- **"20% flat" + "+5% magic"** could not be reproduced. The WebFetch of
  https://www.icy-veins.com/wow/protection-warrior-pve-tank-guide on
  2026-05-24 returned "no mention of Defensive Stance" in the rendered
  content. The briefing quote is therefore unverifiable today — possibly
  outdated, possibly from a different page section the fetcher missed.
  Not labelling Icy Veins as wrong; labelling the quote as not reproducible.

## DR-stacking arithmetic (per project discipline)

Even if the hypothetical extra 20% existed, the marginal closure on the
~10pp physical gap would be `0.20 × (1 - existing_DR)` against an existing
physical DR stack of ~0.65 = `0.20 × 0.35 = 7.0pp`. Not 20pp. The
brief's "~10pp marginal" estimate was already in the right ballpark via
the discipline rule, but the empirical premise is gone.

## Implications for the open ~10pp physical mit gap

**This audit does NOT close the residual gap.** It eliminates one
hypothesis. The remainder remains multi-source per session 23's conclusion;
candidates surveyed there (additional party auras, physical-only DR talents,
encounter-level multipliers) are unaffected by this finding. The next
investigator should look elsewhere.

## Side observations (non-blocking)

- `constants.yaml:367` defines `unyielding_stance` (spell 452494) but
  `mitigation.py:241-251` and `AUDIT.md` F4 attribute spell 452494 to
  **Fight Through the Flames**. Possible doc/talent rename drift. Not
  load-bearing for this audit, but worth a one-line clarification on
  the next pass through the constants file.
- Session 13's structural fix (K=2700 → 3430 + DS to all schools at 0.15,
  atomic) remains the correct anchor. No follow-up code change is needed
  here.

## Outcome

- No code changes.
- No constants_version bump.
- No new branch.
- Doc lands as a closure artifact for the hypothesis so future sessions
  don't re-litigate.
