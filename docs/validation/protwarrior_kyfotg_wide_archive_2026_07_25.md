# KYFOTG + resid_out at scale — 37-file wide-archive extension (2026-07-25)

**Update (same day, later): KYFOTG merged.** Following this doc's own
"reconfirmed at scale, not a small-sample artifact" finding below plus a
live re-confirmation on the ratified corpus (RMSE 0.0797, rounds to 0.080,
not 0.079 as first written here — same measurement, tighter precision),
the user ratified option (ii) from this doc's own recommendation: merge on
correctness grounds and accept the worse-but-more-honest RMSE/bias. See
`docs/validation/protwarrior_cross_player_validation_gate_2026_07_25.md`
for the merge decision and `constants.yaml`'s `calibration:` block for the
final numbers.

**Original status (measurement only, investigation-only, superseded by the
update above). No engine/constants change at the time this was written.
Extends
`docs/validation/protwarrior_magic_wedge_kyfotg_lead_2026_07_22.md` after that doc's
implementation regressed the ratified corpus's calibrate-k numbers (RMSE 0.073→0.079,
bias +1.8%→−4.4%, 16/16→14/16 within ±15% — see the branch's own validator reports).**

## Why this run

Two questions were open after the regression: (1) was KYFOTG's measured magnitude/uptime
(from the 16-log ratified corpus alone) a small-sample artifact, and (2) what's actually
left in `resid_out` (the ~7-10% cut still unexplained even when KYFOTG is confirmed off) —
is it a second findable mechanic (the Vanguard/KYFOTG pattern) or diffuse noise? The
2026-07-21 wide-corroboration pass (25 of the 37 non-ratified Brutoh logs, systematic
1-in-3 sample) answered neither — it predates KYFOTG's discovery and only reports the
aggregate wedge, not per-ability/aura decomposition. This run uses the FULL 37 remaining
files (not a sample) and extends `scripts/clean_wedge_decomposition.py` (new `--wide` mode,
mirroring `full_chain_wedge.py`'s existing `--wide` design) to get the same per-hit
decomposition the original investigation ran on the ratified corpus, at ~3.5x the scale.

Corpus: 37 files, 70 successful CHALLENGE_MODE runs, 29,575 clean hits (vs the ratified
corpus's 16 runs / 8,920 clean hits), spanning 2026-05-18 to 2026-07-11.

## Finding 1 — KYFOTG reconfirms, and the non-frost-magic gap is BIGGER at scale, not smaller

| | ratified 16-log corpus | wide 37-file / 70-run set |
|---|---:|---:|
| non-frost-magic wmean | 0.8598 (n=7,453) | **0.8049** (n=23,622) |
| implied gap | +16.3% | **+24.1%** |
| KYFOTG resid_in/resid_out (non-frost-magic) | 0.8233/0.9035 (ratio 0.911) | 0.7730/0.8437 (ratio **0.916**) |
| KYFOTG resid_in/resid_out (all clean) | 0.8385/0.9318 (ratio 0.900) | 0.8003/0.9014 (ratio 0.888) |

KYFOTG's own in/out ratio holds up well (0.916 non-frost-magic — even tighter against the
tooltip's -8%/0.92 than the small corpus) — **not a small-sample artifact.** But the gap
KYFOTG is credited against is measurably larger in the bigger, more representative sample
(+24.1% vs +16.3%). The ratified 16-log corpus, by chance of its own realized per-run
variance, sat on the smaller-gap side of the real distribution. This is the direct
explanation for the regression: K=3430 was tuned against a corpus that was, this whole
time, quietly under-counting close to zero of this gap and still landing at +1.8% bias —
meaning something else in that specific 16-log sample's realized noise was compensating.
Crediting even KYFOTG's real, correctly-sized share breaks that coincidental balance.

## Finding 2 — resid_out has no findable single-mechanism candidate; it looks diffuse

Aggregate resid_out (hits with KYFOTG confirmed NOT active): **wmean 0.9014** (n=13,774),
consistent with the ratified corpus's own reading (0.90-0.93) — this part of the residual
is stable across sample sizes, unlike the raw non-frost-magic gap above.

Aura-correlation pass on resid_out (non-frost-magic only, n=10,393) — top correlate power
**0.050** ("Flask of the Blood Knights", a raid consumable — almost certainly a party-comp
confound, not causal), next few (Anguish, Beacon of the Savior/the Sun, Spell Reflection,
Avatar, Mark of the Wild) all in the 0.02-0.05 power range. **Nothing remotely close to
KYFOTG's own 0.098 (all-clean) / 0.064 (resid_out-only in the smaller test) power** — no
single buff/consumable/CD explains a meaningful share of what's left.

By dungeon (8 dungeons, all with real sample size): resid_out ranges 0.839 (Magisters'
Terrace) to 0.952 (Pit of Saron) — a real spread, but every dungeon shows SOME gap, none
near 1.0 (fully explained) and none a wild outlier. Consistent with a diffuse, cross-content
effect, not dungeon-specific mob tuning.

**Interpretation:** resid_out reads exactly like the "universal ~5-7% run-scoped wedge"
already named in `docs/validation/phase4_brewmaster_physical_gap_decomposition_2026_07_04.md`
("Finding B" — run-scoped, mob-side damage multipliers applied after `base_amount` is
computed, the signature of server-side tuning) — present everywhere, correlated with
nothing, no single named ability explains it. If that's right, it is very likely NOT a
second findable Vanguard/KYFOTG-style passive. It may not be closable by mechanism-hunting
at all.

## What this means for the KYFOTG decision

Both regression symptoms are now explained, not just measured:

1. The regression is NOT evidence KYFOTG is wrong — its magnitude is real and reconfirmed
   at nearly 3.5x the original sample size.
2. The regression IS evidence that the ratified 16-log corpus's near-zero bias was
   partly coincidental — it was never crediting this gap at all, and adding back even a
   correctly-measured slice of it will mechanically push bias negative until either (a) the
   rest of the gap is also found and credited, or (b) it's accepted as a residual and
   priced into how `calibrated` is interpreted.
3. Finding 2 suggests (b) may be the honest ceiling — there may be no clean "(a)" available
   here, unlike the Vanguard case where the whole gap resolved to one real passive.

Recommendation (for the user, not decided here): do not keep chasing a second KYFOTG-style
discovery for resid_out — the data argues against one existing. The real decision is whether
to (i) hold KYFOTG unmerged and keep the current ratified 16-log numbers as the practical
ceiling, naming this gap as a known, currently-irreducible residual, or (ii) merge KYFOTG
on correctness grounds (it's real) and accept/re-baseline a worse-but-more-honest
RMSE/bias, the same trade this project accepted for the Demo Shout and Shield Block fixes
before Vanguard happened to close the rest. There is no guaranteed "Vanguard" here still to
find — that's the actual news from this run.

## Caveats

- This corpus overlaps in time with the ratified one (both are Brutoh, same 2-month
  window) — it is NOT an independent player/build check, only a larger sample of the same
  one. A genuinely different Prot Warrior's logs would be a stronger test.
- `full_chain_wedge.py`'s "as modeled" and "SB-excluded" aggregate numbers are now
  identical on current `master`/this branch (0.9033 both) — mechanical consequence of the
  Shield Block double-count fix already landing in the ratified calibration; not a new
  finding, noted so it isn't mistaken for one.
- Per-run non-frost-magic wmean spans 0.72-0.90 across the 58 wide runs — real spread,
  consistent with per-run F-factor noise already characterized elsewhere in this project.

## Files produced

- `scripts/clean_wedge_decomposition.py` — extended with a `--wide FILE...` mode (mirrors
  `full_chain_wedge.py`'s existing `--wide`) and a new resid_out decomposition section.
  Nothing existing changed behaviorally; ratified-corpus mode (no `--wide` flag) is
  byte-for-byte the same code path as before.
- This doc.
