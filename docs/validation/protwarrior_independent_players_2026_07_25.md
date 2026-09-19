# Prot Warrior calibration against 8 independent players — first true out-of-sample test (2026-07-25)

**Status: measurement only. No engine/constants change, no calibration_tier change.** This
is the first time Prot Warrior's model has been checked against ANY player other than
Brutoh. It surfaces a bigger question than the one it was built to answer.

## Why this run

`docs/validation/protwarrior_kyfotg_wide_archive_2026_07_25.md` closed with a caveat: every
data point so far (16-log ratified corpus, 37-file wide archive) is Brutoh — one player,
one gear progression. The user asked whether WarcraftLogs public reports from OTHER
Prot Warriors would help. They do — this project's own WCL tooling (`character_from_wcl`,
gear-certain `CombatantInfo` hydrate) already supports this for other specs (it's how
Blood DK/Brewmaster/VDH got their first characterization data); it had just never been
pointed at Prot Warrior.

## Method

Queried WCL's `characterRankings` API (zone 47, "Mythic+ Season 1", all 8 dungeon
encounters) for top Protection Warrior parses, filtered to EU/US region, deduped to
distinct players. Picked one player per dungeon (8 total, all different players/servers),
ran each through `simf calibrate-k --wcl-url` on `calibration/protwarrior-kyfotg-magic-dr`
(KYFOTG credit live) at fixed K=3430, iterations=50.

Before trusting the numbers, checked two things that could have contaminated them:

1. **Does KYFOTG actually apply to each player?** Queried each fight's Buffs events for
   spell 438591 directly. **All 8 had it present** — Mountain Thane (or at least this
   proc) is apparently universal-to-near-universal among current top M+ Warrior pushers,
   so no player's result is spuriously "worse" for lacking the credit entirely.
2. **Does gear-certain hydrate know each player's REAL talent selections, or does it
   silently fall back to Brutoh's own build?** Checked `character_from_combatant_info.py`
   directly: `build_hydrate_result` sets `char_data["talents"]` to a generic per-spec
   loadout NAME (not player-specific), but SEPARATELY sets `decoded_talents` from
   `decode_combatant_info_entry_ids(ci.talent_spell_ids)` — a real, per-player, entry-id
   decode that is **Protection-Warrior-only** (per its own docstring) and takes precedence
   over the YAML loadout guess in `Character._talent_set()`. This is the same mechanism
   already relied on for VDH/Blood DK WCL calibration. So each of the 7 ACL-on players
   below has their OWN real Indomitable/Brace for Impact/etc. selections, not Brutoh's.

## Results (K=3430, KYFOTG credit live)

| Player | Dungeon | Key | ACL | Delta |
|---|---|---:|---|---:|
| AnonPlayerX4 | Algeth'ar Academy | +22 | yes | +7.3% |
| Player 1 | Magisters' Terrace | +22 | yes | +23.7% |
| Player 2 | Maisara Caverns | +21 | yes | +5.3% |
| Player 3 | Nexus-Point Xenas | +20 | yes | +31.4% |
| Player 4 | Pit of Saron | +21 | yes | +15.4% |
| Player 5 | Seat of the Triumvirate | +20 | yes | +10.0% |
| Player 6 | Windrunner Spire | +20 | yes | **−1.9%** |
| Player 7 | Skyreach | +21 | **no** | +18.9% (excluded from stats below — ACL off, uses Brutoh's fallback gear, not comparable) |

**Mean of the 7 ACL-certain runs: +13.0%, range −1.9% to +31.4%. 6 of 7 positive.**

## What this means

**The ratified Brutoh corpus's near-zero bias (+1.8%, RMSE 0.073) does not generalize.**
On 7 independent, gear-certain, correctly-talent-decoded players, the SAME code
(KYFOTG live) over-predicts damage by a mean of +13% — landing much closer to the OLD,
PRE-Vanguard Brutoh bias (+11.5% to +13.7%, see the Calibration section of CONTRIBUTING.md) than
to today's ratified number. This is the opposite direction from the KYFOTG-on-Brutoh
regression (−4.4%) reported two days ago — on independent players the model is nowhere
near under-predicting.

Two competing explanations, NOT distinguished by this data:

1. **Brutoh's own corpus is not representative of "Prot Warrior in general."** K=3430,
   Vanguard's 0.70 coefficient, and every Shield Block fix were tuned and validated
   exclusively against his logs. A model tuned this precisely to one player's gear/RNG/
   party comp landing near-perfectly on that SAME player's held-out runs, while missing
   by an order of magnitude more on everyone else, is a classic overfitting signature.
2. **A real key-level-dependent gap.** All 8 independent checks are +20-22 keys; Brutoh's
   ratified corpus tops out at +17. If some mitigation-relevant mechanic scales
   incorrectly above +17 (in either direction — a missing DR layer, or a mob-damage
   multiplier that isn't linear past a threshold), that alone could produce exactly this
   shape without implicating Brutoh's corpus at all. `magic_cast_scaling_gap_2026_07_09`
   already fixed ONE such bug; this could be a second, more localized one, or the same
   family (server-tuning cliffs at push-tier key levels) with headroom still unaddressed.

This dataset cannot tell the two apart: it has no independent LOW-key player and no
Brutoh HIGH-key run to compare against. Both are real, actionable next questions.

## Caveats

- Single fight per player — real per-pull mob-composition/RNG noise (the same "F-factor"
  variance already characterized for Brutoh's own corpus) is baked into each number
  individually. A mean over 7 points partially averages this out but not fully.
- Player 7's run (ACL off) is excluded from the headline stats — it used the fallback
  `--character` gear (Brutoh's), not Player 7's own, so it isn't a valid data point for
  this question and is reported only for completeness.
- `decode_combatant_info_entry_ids`'s reliability for talents OUTSIDE the ~13 modeled
  ones was not (and cannot be) checked — those talents aren't modeled regardless of
  whether they're correctly detected, so this doesn't change the analysis, but a player
  whose build differs from Brutoh's in a talent NOT among the 13 modeled ones would show
  a real gap this project has no way to attribute correctly today.
- No LOO-CV or corpus-manifest changes were made — these 8 fights are not proposed as a
  new ratified corpus, just a diagnostic cross-check.

## Recommendation (for the user, not decided here)

This is bigger than the KYFOTG decision. Before deciding whether to merge KYFOTG at all,
it may be worth deciding whether Prot Warrior's `calibrated` tier itself needs to be
re-examined — not because anything shipped was wrong, but because it has now been shown
to have only ever been validated against one player. The cheapest next step that would
distinguish the two hypotheses above: pull 1-2 more independent players at LOWER key
levels (+12-16, matching Brutoh's own range) through the same pipeline. If bias stays
around +13% even at matched key levels, that points hard at hypothesis 1 (overfit to
Brutoh). If it drops back toward 0% at matched key levels, that points at hypothesis 2
(a real, currently-unknown high-key-specific gap).
