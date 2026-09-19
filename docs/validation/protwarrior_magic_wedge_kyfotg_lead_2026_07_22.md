# Prot Warrior "clean wedge" magic residual — decomposed, and a real candidate found: "Keep Your Feet on the Ground" (2026-07-22)

**Update (2026-07-25): implemented and merged.** Despite this doc's own
"do not add off this measurement alone" recommendation below, the credit was
implemented the same day (commit `0870ede`, window-gated as this doc
specified) and held un-ratified on a branch pending the re-measurement this
doc called for. That re-measurement happened — see
`docs/validation/protwarrior_kyfotg_wide_archive_2026_07_25.md` for the
37-file reconfirmation and `docs/validation/protwarrior_cross_player_validation_gate_2026_07_25.md`
for the merge decision itself. Ratified corpus at canonical K=3430:
RMSE 0.073→0.080, mean bias +1.8%→−4.4%, 16/16→14/16 within ±15%,
LOO-CV still PASSES. `constants.yaml`'s `global_rmse` and `constants_version`
are both updated accordingly.

**Original status (2026-07-22, superseded by the update above): measurement
+ a new, sourced, quantitatively-matched engine-bug lead. No constants.yaml value
changed, no `calibration_tier` change, no engine code touched.** This follows up
`docs/validation/protwarrior_full_chain_wedge_2026_07_21.md` and
`docs/validation/protwarrior_vanguard_strength_armor_2026_07_21.md`'s open item: the "clean wedge"
(~0.8896, hits outside every Shield Block/Shield Wall/Battle-Scarred-Veteran window) was asserted —
same day, never shown derived — to be "~81%-magic-weighted" and named as a fully separate,
untouched-by-Vanguard lead. This doc runs that decomposition for real, cross-checks it against
Vengeance DH's own (much larger) magic residual, and turns up a specific, previously-unmodeled
Mountain Thane hero-talent proc as the leading partial explanation.

Method: a 4-agent parallel research round (doc synthesis across every spec's magic-residual
history, a live per-hit re-decomposition of the ratified 16-log corpus, a log-semantics audit for
measurement artifacts, and independent Wowhead/SimC source research) followed by 2 independent
adversarial verification passes, then spot-checked directly against the raw logs in this session
(not taken on faith from any agent).

## The "~81%-magic-weighted" figure, actually derived

Splitting the clean bin (33,235 total clean-eligible hits, 8,920 outside every CD window) three
ways by school, damage-weighted:

| class | n hits | % of clean damage | wmean residual | implied gap |
|---|---:|---:|---:|---:|
| frost (all) | 652 | 7.2% | **0.9900** | +1.0% (essentially fully explained) |
| non-frost magic (shadow/arcane/nature/fire/holy) | 7,453 | 73.6% | **0.8598** | +16.3% |
| physical + bleed | 815 | 19.2% | **0.9460** | +5.7% |

Frost + non-frost-magic = 80.8% of clean damage — this is the real derivation behind the
previously-asserted "81%-magic-weighted" figure. But the framing was imprecise: **frost is fully
explained; the gap is concentrated in everything EXCEPT frost**, not spread evenly across "magic."
Physical+bleed's own 5.7% residual lines up closely with the older, broader
"universal ~5-7% always-on cut" named in
`docs/validation/phase4_brewmaster_physical_gap_decomposition_2026_07_04.md` — that finding and
this one are likely the same thing, now isolated by school for the first time.

Ruled out for the non-frost-magic gap specifically (all independently re-derived, not assumed):

- **Key-level scaling bug** — non-monotonic (+12 → 0.8722, +13 → 0.8953, +14 → 0.8589; +13 is the
  *smallest* gap), which a scaling multiplier couldn't produce.
- **Dungeon-specific mechanic** — apparent per-dungeon clustering (Pit of Saron sitting high in the
  raw table) is fully explained by frost-damage share (Pit of Saron is 32.2% frost, every other
  dungeon 0-0.6%); once split by school, every dungeon's non-frost-magic residual lands in the same
  0.83-0.89 band.
- **Server-side partial-resist baked into `base_amount`** — the `resisted` field is empirically
  zero across all 42,409 non-physical hits against Brutoh in the full local archive (partial-resist
  rolls against players were removed from the game long ago; there's no live mechanic to hide
  behind). The suffix field-position parse was independently cross-checked against a raw log line's
  redundant school byte and confirmed correctly anchored, not off-by-one.
- **Parser asymmetry between magic and physical event handling** — `combat_log_damage.py` uses an
  identical code path for both; school only branches in the wedge script's own modeling logic.
  (A separate, unrelated `r > 1.0` artifact was found on 2.1% of magic hits — stacking-debuff
  abilities like Arcane Orb where `amount` exceeds `base_amount` — but this biases the residual
  *upward*, the opposite direction, so if anything the true gap is slightly larger than measured,
  not smaller.)
- **A single dominant ability or mob** — no, 60+ distinct non-frost-magic abilities each carry
  <2.5% of clean damage, clustered in a fairly tight ~0.78-0.90 band across 6 mob families and 6
  dungeons. This looks like an always-on layer, not a missing single-ability mechanic.

## The lead: Keep Your Feet on the Ground (spell 438591)

Aura-attribution (inside-vs-outside-window residual comparison) on the clean population surfaced
one candidate with real explanatory power:

| population | spell | name | resid_in | resid_out | ratio | share of clean-in time |
|---|---|---|---:|---:|---:|---:|
| all clean hits | 438591 | Keep Your Feet on the Ground | 0.8385 | 0.9318 | **0.900** | 49% |
| non-frost-magic only | 438591 | Keep Your Feet on the Ground | 0.8233 | 0.9035 | **0.911** | 54% |

**Independently confirmed in this session** (not just by the researching sub-agents):

- **Raw-log ground truth**: `grep -c 438591` against every one of the 12 unique log files behind
  the ratified 16-run corpus shows the aura firing 103-670 times per file
  (`WoWCombatLog-050626_153703.txt`: 221, `...051526_210245.txt`: 670, etc. — all 12 files nonzero).
  Sample line: `SPELL_AURA_APPLIED,Player-REDACTED,"Brutoh-Uldum-EU",...,438591,"Keep Your Feet
  on the Ground",0x1,BUFF` — self-applied by Brutoh onto himself, refreshing every 2-9 seconds.
- **Tooltip, independently re-fetched**: Wowhead spell 438591 direct fetch — "Apply Aura: Mod %
  Damage Taken [Arcane, Fire, Frost, Holy, Nature, Physical, Shadow] -8%, 5 sec, instant, no
  cooldown, no resource cost." Warcraft Wiki (via search): "Physical damage taken reduced by 2%.
  Thunder Blast reduces damage you take by 8% for 5 sec" — describing the same talent's base
  passive (a separate, always-on 2% physical-only component with no discrete buff-application
  event, structurally invisible to aura-window scanning — the same "invisible until you go looking"
  shape as Vanguard's own armor bonus) plus the 438591 proc.
- **Talent placement**: a Mountain Thane Warrior hero talent, exclusive-choice with "Steadfast as
  the Peaks" (`sc_warrior.cpp`, `find_talent_spell(talent_tree::HERO, "Keep Your Feet on the
  Ground")` per SimC source cross-check).
- **Magnitude match**: the measured 0.900/0.911 in/out ratio sits within 2-3pp of the tooltip's
  exact -8% (=0.92) prediction — a tight match, the same standard Vanguard/Painbringer/Riposte were
  each held to before shipping.
- **Not currently modeled**: confirmed by grep — spell 438591, "Keep Your Feet on the Ground", and
  "Mountain Thane" do not appear anywhere in `src/simf/` or `docs/`.

Avatar (spell 107574, a burst-DPS class cooldown with no known defensive clause) showed a similar
but weaker correlation (ratio 0.934/0.945) and fires near-simultaneously with KYFOTG in the raw
logs — almost certainly a pull-phase confound (both get used at similar moments), not a rival
causal candidate. The original research pass's own unrestricted aura-scan tool
(`scripts/aura_attribution.py`) correctly surfaced KYFOTG as the #1-ranked correlate by power score;
the miss in the first pass was not checking what the top-ranked ability actually does before
dismissing it as "Avatar's companion effect."

## What this does NOT close

- **KYFOTG's own `resid_out`** (hits with the buff confirmed down) still sits at 0.90-0.93 — an
  unexplained ~7-10% cut remains even outside its window. This is a real, substantial partial
  explanation, not the whole non-frost-magic gap.
- **The physical+bleed ~5.7% "universal wedge"** is untouched by this finding — KYFOTG's own
  passive 2%-physical component could be part of it (structurally invisible to aura scanning, not
  yet isolated the way the proc's 8% component was here), but this is not verified, only flagged as
  a plausible next thread.
- **Cross-spec framing is likely wrong.** A dual adversarial pass caught a units mismatch: Warrior's
  ~11% ("clean wedge") is a *narrow, CD-window-excluded* residual; VDH's +37-64% ("magic residual")
  is a *whole-run* aggregate bias — never measured the same way, so comparing them directly is
  apples-to-oranges. A magnitude ceiling check on every currently-sourced, currently-inert party
  buff (Devotion Aura 3% all-school, Elemental Resistance 6% fire/frost/nature only —
  `party_dr_by_school`, confirmed structurally inert in production replay/calibrate-k today) caps
  at ≤8.8% even in the best case and only for 3 of 7 schools — an order of magnitude short of VDH's
  gap regardless of uptime assumptions. **Treat Warrior's KYFOTG lead and VDH's magic residual as
  separate problems that merely rhyme, not one shared root cause.** A smaller, genuinely
  interesting side-result: splitting the corpus's frost hits by whether Elemental Resistance was
  present that run shows frost residual ≈1.0 in its absence and a real ~4.3pp gap in its presence
  (2 runs / 164 hits) — directionally consistent with ER being real but currently un-triggered in
  replay, not yet strong enough to act on alone.

## Recommendation (for human ratification, not a decision made here)

**Do not add KYFOTG to `constants.yaml`/`character.py` off this measurement alone.** The lead is
real, sourced two independent ways, and quantitatively tight — the same bar Vanguard/Painbringer/
Riposte cleared — but:

1. It would need real window-gating (like Shield Wall/BSV, via
   `parse_self_buff_windows` on spell 438591), not a flat always-on term, since measured uptime
   inside the clean population is only ~49-54%, not 100%.
2. It directly affects the Prot Warrior corpus ratified `calibrated` **today** (RMSE 0.073, LOO-CV
   PASS) — any change here needs a re-run of `simf calibrate-k` + the LOO-CV gate against the same
   ratified manifest, with before/after numbers brought back for explicit ratification, exactly as
   Vanguard was.
3. The passive 2%-physical component and the still-open `resid_out` residual mean this closes at
   most part of the non-frost-magic gap, not all of it — a second pass after implementing the proc
   credit would still be needed to see what, if anything, remains.

## Files produced (all new, nothing existing modified)

- `scripts/clean_wedge_decomposition.py` — new one-off, ability/mob/dungeon/key-level/school/aura
  decomposition of the existing wedge tool's "clean" population; imports `full_chain_wedge.py` as a
  sibling module rather than duplicating its parsing.
- This doc.

## Caveats

- The school/dungeon/key-level decomposition is solid (independently re-reproduced exactly, to 4
  decimal places, in the adversarial verification pass). The KYFOTG magnitude match (2-3pp) and the
  raw-log occurrence counts were independently re-verified in this session directly (not just by
  sub-agents). The Wowhead/Wiki tooltip text was independently re-fetched in this session too. The
  SimC source line citation (`sc_warrior.cpp:7491`) was NOT independently re-fetched from GitHub in
  this session — recommended before this becomes a shipped fix, not before documenting the lead.
- Only Brutoh's own build is represented (12 unique logs, one character) — this is a single-player
  corpus finding, same standing caveat as every other Prot Warrior calibration doc.
- The Elemental Resistance frost-split (2 runs / 164 hits) is a lead, not proof, per its own
  section above.
