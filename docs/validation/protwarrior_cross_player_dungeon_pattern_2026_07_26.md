# Prot Warrior cross-player gap — per-dungeon pattern investigated, no single mechanism found (2026-07-26)

**Status: investigation only. No engine/constants change, no doc claims retracted.**
Follows up the 2026-07-25 cross-player validation work
(`docs/validation/protwarrior_cross_player_validation_gate_2026_07_25.md`)
on one specific lead that session surfaced but didn't chase: grouping the
15 independent players' deltas by dungeon showed Magisters' Terrace and
Nexus-Point Xenas sitting at ~+25-27% bias while three other dungeons sat
near zero. This doc reports what a real investigation of that pattern
found — and mostly didn't find.

## The observation that prompted this

| Dungeon | Independent-player deltas | Mean |
|---|---|---:|
| Magisters' Terrace | +23.7%, +26.0% | **+24.9%** |
| Nexus-Point Xenas | +31.4%, +21.9% | **+26.7%** |
| Pit of Saron | +15.4%, +6.0% | +10.7% |
| Seat of the Triumvirate | +10.0%, +11.4% | +10.7% |
| Algeth'ar Academy | +7.3%, +8.4% | +7.9% |
| Maisara Caverns | +5.3%, −0.5% | +2.4% |
| Skyreach | +1.9% (n=1) | +1.9% |
| Windrunner Spire | −1.9%, −0.9% | **−1.4%** |

Both independent players agreed tightly within Magisters' Terrace and
Nexus-Point Xenas, which read as a real signal rather than noise at the
time — worth chasing before assuming the cross-player gap is diffuse
per-player variance.

## What was checked

**Party composition + full buff inventory**, fetched live via WCL for the
4 flagged (high-bias) fights and 4 comparison (low-bias) fights (Maisara
Caverns / Windrunner Spire, the two dungeons reading closest to zero):

- No single healer spec, DPS spec, or fight duration distinguishes the two
  groups — party comps are heterogeneous in both (Discipline Priest,
  Preservation Evoker, and Mistweaver Monk healers all appear in the
  high-bias group; Preservation Evoker and Mistweaver Monk also appear in
  the low-bias group). Fight durations overlap (1506-1898s high, 1746-1786s
  low) — no clean split there either.
- A set-difference over each fight's full tank-sourced buff inventory (WCL
  `events(dataType: Buffs, sourceID: tank)`, unfiltered — the same query
  this project's KYFOTG/Painbringer credit uses, just with `ability_ids=None`)
  found exactly 2 candidates present in ALL 4 high-bias fights and NONE of
  the 4 low-bias fights: spell 97463 ("Rallying Cry") and 202164 ("Bounding
  Stride"). 7 candidates ran the other way (present in all 4 low, none of
  the 4 high) — Anti-Magic Zone (145629), Into the Fray (202602), and 5
  spell IDs in the `12xxxxx`/`126xxxx` range that resolve to what look like
  Midnight trinket-proc names ("Light's Potential", "Might of the Void",
  "Heart of Ancient Hunger", "Aln'sight", "Aln'scorned Essence").

**Rallying Cry, the strongest candidate, tested against Brutoh's own
corpus — and refuted.** Rallying Cry (spell 97463) is a real, currently
unmodeled Warrior class ability (party-wide damage-taken reduction). If its
absence from the model explained the high-bias group's over-prediction,
Brutoh's own ratified 16-log corpus should show the same pattern: runs
where he used Rallying Cry should skew more positive than runs where he
didn't. Checked directly (exact spell-ID field match within each run's
real CHALLENGE_MODE time window, not a naive text search — a first naive
`grep -c 97463` falsely matched on 1-166 occurrences per file by matching
the substring inside unrelated large numbers; the real per-run count using
the proper CSV field position is much smaller and only sometimes nonzero):

| Group | n | Mean signed delta |
|---|---:|---:|
| Rallying Cry present in the run | 11 | **−3.4%** |
| Rallying Cry absent | 5 | **−6.5%** |

Rallying Cry is present in 11 of Brutoh's 16 runs already (a common,
unremarkable occurrence, not something unique to the flagged independent
players) and the presence/absence split runs in the **wrong direction**
for the hypothesis (present-runs are less negative, not more positive) —
if anything a mild effect opposite to what "unmodeled Rallying Cry inflates
over-prediction" would predict. **This rules out Rallying Cry as the
explanation.**

**Brutoh's own experience in the two flagged dungeons directly contradicts
a "these dungeons are hard for the model" theory.** Grouping his own
16-log corpus's per-run deltas (measured at canonical K=3430 with KYFOTG
live, same session) by dungeon:

| Dungeon | n | Mean delta |
|---|---:|---:|
| Algeth'ar Academy | 2 | −15.7% |
| Skyreach | 1 | −16.1% |
| Pit of Saron | 4 | −3.8% |
| Maisara Caverns | 2 | −2.8% |
| Nexus-Point Xenas | 1 | −2.4% |
| Windrunner Spire | 3 | −0.5% |
| **Magisters' Terrace** | 3 | **+0.7%** |

Magisters' Terrace and Nexus-Point Xenas — the two dungeons showing the
**largest** independent-player bias — are among the **best-fit** dungeons
in Brutoh's own corpus. If either dungeon carried a real, unmodeled
mob-kit or mechanic gap, it should show up for Brutoh too. It doesn't.
Algeth'ar Academy and Skyreach, not flagged by the independent-player data
at all, are Brutoh's own worst-fit dungeons — the opposite pattern
entirely.

## Conclusion

No single missing buff or dungeon-side mechanic survives cross-checking.
The 7 "low-only" buff candidates weren't pursued further given how cleanly
Rallying Cry — the cleanest-looking candidate of the two "high-only" ones —
fell apart under the same test; chasing weaker, less-clean candidates
without the Rallying Cry precedent's decisive refutation-test available
(none of the 7 have anywhere near Brutoh's own within-dungeon sample size
to check against) would just be speculation. The original per-dungeon
table's tight agreement between the 2 independent players in Magisters'
Terrace and 2 in Nexus-Point Xenas is more likely a coincidence of a very
small sample (n=2 per dungeon bucket) than a real per-dungeon signal —
it looked compelling with 2 points per bucket, but doesn't survive being
checked against a dungeon-matched larger sample (Brutoh's own 3+3 runs in
the same two dungeons).

This **does not overturn** the 2026-07-25 cross-player downgrade's own
conclusion — it reinforces it. That doc already ruled out a hydrate-quality
artifact and a key-level effect, landing on "the ratified calibration is
real and correct for Brutoh specifically, and does not transfer to other
players' gear/builds/parties" as the load-bearing explanation. This
investigation adds: it's not a per-dungeon effect either, at least not one
findable via party comp, buff inventory, or the single most-plausible
missing-mechanic candidate. The gap remains a genuine, currently
unexplained per-player generalization gap — named honestly rather than
forced into a tidier-sounding but unsupported per-dungeon story.

## What would move this further

- A genuinely larger per-dungeon independent sample (more than 2 players
  per dungeon) would settle whether the original table's agreement was
  coincidence or signal — WCL's rankings index was already found (2026-07-25)
  not to reach below ~+17-18 no matter how deep it's paged, so more data
  means more players at the SAME key range, not lower ones.
- The 7 "low-only" buff/trinket candidates are unverified, not refuted —
  worth a look if a future investigation has a larger matched sample to
  test them against (the same technique used here for Rallying Cry).
- Per-hit forensics (the `clean_wedge_decomposition.py` method) on the 4
  flagged fights themselves, rather than aggregate buff presence/absence,
  would show whether their damage residual is concentrated in specific
  abilities/mobs or diffuse — not attempted here.

## Files

- This doc.
- Raw fetched data (party comp + buff inventory JSON per fight): session
  scratchpad, not committed (diagnostic only, reproducible from the WCL
  report codes/fight IDs already in
  `src/simf/data/calibration_corpora/prot_warrior_independent_wcl_2026_07_25.yaml`).
- `docs/validation/protwarrior_calibrated_downgrade_2026_07_25.md` — the
  downgrade this investigation follows up, conclusion unchanged/reinforced.
