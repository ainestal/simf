# Gem suggester — engine validation (2026-06-24)

The gem suggester recommends, per socket, the gem that maximises survival ΔeHP:

    ΔeHP(gem) = Σ_stat (∂eHP/∂stat × gem_stat)   weighted by the dungeon school mix

reusing `optimizer/per_dungeon._score_for_school_mix` so a gem's "+N eHP" is
bit-for-bit comparable to an item swap's. It is fully spec-general — the only
gem-specific logic is `primary → agility/strength` resolution and the
Unique-Equipped meta constraint. Everything else (which stats matter, by how
much) comes from the marginals dict the caller passes in.

## Gem catalog (`data/gems.yaml`)

Every value CONFIRMED via direct Wowhead `&xml` fetch (Midnight 12.0.5):

| family | gem | stats |
|---|---|---|
| Eversong (unique) | Indecipherable | +32 primary |
| Eversong (unique) | Stoic | +23 primary, +13 armor |
| Peridot (haste) | Quick / Versatile / Deadly / Masterful | 17 haste · 16h+7v · 16h+7c · 16h+7m |
| Lapis (vers) | Versatile / Quick / Deadly / Masterful | 17 vers · 16v+7h · 16v+7c · 16v+7m |
| Garnet / Amethyst | Versatile | 16c+7v · 16m+7v |

**There is no stamina gem in Midnight** — the only non-secondary survival stats
obtainable are primary (Eversong) and +13 armor (Stoic meta only). The Eversong
metas are Unique-Equipped (one socketable), so the suggester reserves exactly
one socket for the best meta and fills the rest with the best non-unique gem.

## Grounding against the live sim-derived marginals

Realistic AnonGuardian1-shaped Guardian (caster armor 919, agi 1900, in-form bear armor
≈ 9.7k–12.2k, armor_dr 0.74–0.78 — below the 0.85 cap, matching the local-log
characterization's stable ≈11k). Marginals from `compute_survivability_marginals`
(the path the live gear UI uses), 150 iter:

**Non-Elune's-Chosen** (`agility 1128/pt`, **haste 0** — EC gate holds):
- agi-32 Eversong = **23.5k eHP** ≫ 17-vers Lapis = 5.7k ≫ haste = 0.
- Suggester: keep Eversong on neck (Δ 0, already there), rings → Versatile Lapis.

**Elune's-Chosen** (`agility 1502/pt`, `haste 1482/pt`, vers 553):
- agi-32 = 31.2k, 16h+7v Peridot = 18.1k, 17-haste = 16.4k, 17-vers = 6.5k.
- Suggester: keep Eversong on neck; finger1 Masterful Peridot (haste + **wasted
  mastery**) → Versatile Peridot (haste + vers) for **+2.7k eHP**; finger2 already
  optimal (Δ 0).

Both match the model the Guardian work established: agility is the top Guardian
survival stat (so the +agi Eversong is the meta pick), and haste only carries
survival value for an Elune's-Chosen Guardian — for whom the best *secondary*
gem flips from vers to haste. The "+agi gem wins" acceptance criterion holds.

### Note on the armor DR cap

When a Guardian is pushed past the 0.85 armor DR cap (e.g. an unrealistically
high haste→Ironfur stack count), the armor-path marginals for armor/agility/haste
collapse and versatility becomes the dominant survival stat. Real AnonGuardian1 sits at
~0.76 DR (uncapped), so this isn't hit in practice — but it is the correct
behaviour: at the cap, more armor genuinely does nothing, and the suggester
follows the marginals wherever they lead. No gem-specific assumption is baked in.

## Tests

`tests/test_gem_suggester.py` (19): catalog integrity (incl. no-stamina-gem),
primary-stat resolution, school-mix scoring, EC-gating (haste valued iff EC),
unique-meta-in-exactly-one-socket, no-churn on an already-metaed socket, the
defensive-Stoic pick for a plate tank whose strength is weak, the AnonGuardian1
acceptance criterion, and unknown-current-gem / empty-socket edge cases.

`calibrated` for `guardian_druid` stays **false** (unchanged) — the suggester
is a consumer of the existing marginals, not a calibration change.
