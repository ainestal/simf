# Phase 4 — Guardian haste→rage→Ironfur uptime-elasticity (2026-06-24)

The model-finish step the user asked for: **give haste a non-zero Guardian survival
value.** simf zeroed it (`marginals.ehp_marginals` returned `haste_rating: {p:0,m:0}`
and `total_armor()` used a static `ironfur_avg_stacks_m_plus`). Mechanically that is
wrong — haste → more rage → more Ironfur casts → higher Ironfur uptime/stacks → more
armor → survival. This pass measures that loop from AnonGuardian1's 16 local runs and models it.

Built on the merged P0 (`#199`, out-of-form armor snapshot) + P1 (`#200`, Ironfur
124%-base coeff 1.46 / 2.45 stacks). `specs.guardian_druid.calibrated` **stays false.**

## 1. Rage economy (16 timed +10..+16 runs, `scripts/rage_economy_anonguardian1.py`)

Aggregate SPELL_ENERGIZE (rage) source breakdown — **remarkably stable** across all 6
dungeons and key levels:

| source | share | haste elasticity | why |
|---|---|---|---|
| Blood Frenzy (203961, Thrash-bleed DoT ticks) | 29.5% | ~1.0 | hasted DoT → more ticks/s |
| Mangle (33917) | 24.8% | ~0.9 | CD **is** haste-reduced (Wowhead 137009 "Modifies Cooldown by Haste") |
| Moonfire (8921, Elune's Chosen DoT) | 14.0% | ~1.0 | hasted DoT |
| Rage/auto (195707) | 12.2% | ~1.0 | 4 rage/swing × hasted swing rate |
| Thrash (77758) | 10.8% | ~0.9 | haste-reduced CD |
| Moon Guardian (430581, EC Moonfire-proc) | 6.0% | ~1.0 | rides the hasted Moonfire engine |
| Bear Form (17057, on-shift) | 1.5% | 0 | one-time per shapeshift |
| Boundless Moonlight | 1.2% | ~1.0 | hasted Moonfire engine |

Two facts drive everything:
- **There is NO rage-from-damage-taken in Midnight 12.0.x Guardian** (verified vs SimC
  `sc_druid.cpp` — no `assess_damage` rage code — and confirmed by the breakdown: every
  source is offensive). The live game's "3 rage/unavoided-hit" mechanic is absent from
  SimC and from these logs. So the inelastic-rage bucket is tiny (just Bear Form, 1.5%).
- **Ironfur is HARD rage-capped, not GCD-capped**: 2–9× more `"Not enough rage"` Ironfur
  fails than successful casts (~1:5). So Ironfur uptime is rage-bound — every marginal
  rage point converts ~directly to more Ironfur.

→ **haste-scaled rage fraction f = Σ(share × elasticity) ≈ 0.945** (only Bear Form's 1.5%
is inelastic). Nearly all Guardian rage scales with haste.

## 2. The model (ratified by the user 2026-06-24)

Ironfur is rage-capped sub-cap, so `d(stacks)/stacks ≈ d(rage)/rage` (elasticity ~1.0
locally), and `d(rage)/rage = f · d(haste%)`. The raw mechanistic `k = f ≈ 0.945` would
multiply two ~1.0 elasticities losslessly; an **adversarial review** (3 lenses) flagged
that as over-credit and applied a **concave rage→stacks transmission haircut (~0.8)** — as
haste raises the rage budget the fail:cast ratio falls, so each rage buys less Ironfur.

> **k = 0.945 × 0.8 ≈ 0.75** (ratified conservative central value; band 0.85–0.97 for f).

```
avg_stacks(haste%) = ironfur_avg_stacks_m_plus(2.45) × (1 + k·(haste% − ref)/ref)
                     true-clamped to [0, ironfur_stacks_max(8)]
ref = 0.121   (AnonGuardian1's MEASURED 16-run mean haste_pct; <0.30 → pre/post-DR identical)
```

**Adversarial review caught real bugs in the first design:** (a) the stated saturation
clamp `base × min(1, raw/8)` is a quadratic that would collapse AnonGuardian1 to ~0.75 stacks —
fixed to a true `min(raw, 8)`; (b) a round `ref=0.12` vs the measured 0.121 would shift
the P1-calibrated baseline — fixed so the change is a **no-op at ref**; (c) the live gear
UI uses the **sim path** (`compute_survivability_marginals`, perturbs haste by 2000), which
captures haste→armor→survival automatically once `total_armor()` is haste-dependent — so
the `ehp_marginals` entry is only a closed-form **fallback**, not a second source of truth.

### Gating (ratified: Elune's Chosen only)

Gated on the **Fury of Elune buff (202770)** in `active_buff_spell_ids` — an Elune's Chosen
capstone a Druid of the Claw build lacks (mirrors the Brewmaster ledger buff-gating).
~22% of AnonGuardian1's rage (Moonfire 14% + Moon Guardian 6% + Boundless Moonlight 1.3%) is
EC Moonfire-engine that DotC doesn't generate, so its `k` differs and is unmeasured.
A **non-Elune's-Chosen or unknown** Guardian falls back to the **static 2.45 stacks with
haste marginal 0** (haste-independent, bit-identical to pre-model). The user did **not**
take the optional vers-ceiling guardrail — haste ranks where the math puts it.

## 3. Magnitude (real-engine, AnonGuardian1 profile)

| stat | +100 rating → physical eHP |
|---|---|
| **haste** (k=0.75) | **+3.28%** |
| haste (k=0.945) | +4.13% |
| versatility | +0.52% |

So the model ranks **haste as the top *physical* survival secondary** for a rage-starved
Guardian (~6× vers/point). Directionally this matches community guidance ("more haste →
more rage → more Ironfur uptime → survival"). Caveats: haste does **nothing for magic**
eHP (vers covers both schools), so on a blended basis it's strong-but-less-extreme; and
the slope is the honest weak point (below).

## 4. Honesty / why calibrated stays false

- **Un-regressable.** AnonGuardian1's haste is gear-locked (1183–1235 ≈ 0.118–0.124) across all 16
  runs — there is zero held-out haste variation to confirm even the *sign* of the slope at
  this magnitude, let alone k=0.75 vs 0.6. This is a MODELED value, not a fit.
- **Single-build.** Derived on one Elune's Chosen profile; DotC is unmeasured (and the two
  code-unverified proc elasticities, Moon Guardian + Boundless Moonlight = 7.5%, are
  exactly the DotC-absent buckets). DotC row is shipped null.
- **Compounding overfit.** k rides on top of the already-AnonGuardian1-shaped `ironfur_avg_stacks_m_plus
  = 2.45` baseline.

→ `calibrated` stays false; the UI must caption the haste survival value
**"modeled / un-regressed / Elune's Chosen only."** Promote toward calibrated only after a
2nd Guardian profile at a materially different haste% (or a DotC log) confirms the slope.

## 5. Validation

At `ref` haste the elasticity factor is exactly 1.0, so an EC Guardian's armor equals the
static-2.45 path — the **P1 calibration corpus (13/16 within ±15%) is preserved by
construction**. Re-running the 16-run characterizer with EC detection on confirms the
baseline deltas are unchanged (haste varies only ±27 rating around ref → stacks shift
≈ ±0.06 → negligible). The new content is the *marginal* (haste's survival slope), which is
un-regressable and therefore characterization-grade, not calibrated.

Unit tests (`tests/test_mitigation.py`): no-op at ref; monotonic + true-clamp at 8;
static (haste-independent) without the EC buff; `ehp_marginals` haste > 0 only for EC
Guardian, 0 for non-EC Guardian and 0 for warrior (bit-identical guard).

## 6. Named follow-ups (NOT in this pass)

- **Live-path hero-talent detection.** ✅ **Hydrate (log-load) path DONE** —
  `hydrate_character` now detects the Fury of Elune buff (202770) from the log and sets
  `active_buff_spell_ids`, so loading a Guardian from a log lights up the haste model on the
  gear panel (verified via the live sim path: haste survival ≈ 964 ≈ 2× versatility; 0 for
  non-Elune's-Chosen). The same wiring surfaces the Brewmaster Predictive Training ledger on
  hydrate too. **Still pending:** the SimC-*paste* path (no log) carries no EC signal — it
  discards the talent hash — so a paste-loaded Guardian gets the static fallback until EC is
  decoded from the talent string (no decoder exists yet; deferred).
- **Nature's Guardian mastery → max HP** (still unmodeled survival).
- **Agility survivability marginal** (Guardian armor rides on agility via Ironfur, but
  `PERTURBED_STATS` / `ehp_marginals` have no agility key — the haste marginal is correct
  only conditional on fixed agility).
- **Gem suggester** (the original product goal — now that haste/agi/mastery are valued).
- **DotC log + 2nd-profile regression** → the falsification gate for calibrated.
