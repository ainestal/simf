# Coaching-lever registry audit — the 4 non-Warrior/Guardian specs (2026-07-02)

Per the user's 2026-06-30 strategy ([[strategy_two_calibrated_specs_all_tank_features]]
in memory terms — finish Warrior + Guardian, then build all-tank features
before fine-tuning the logless specs), this is the "cleanest all-tank feature,
no logs, no calibration" item that memory called out: the
`coaching.defensives` registry (`data/constants.yaml`) that drives the
model-independent hit-vs-coverage coaching surface
(`docs/validation/coaching_coverage_mvp_2026_06_29.md`) was known-incomplete
for the 4 specs without a validation log — the file's own comment says as
much ("best-known signature DR cooldowns ... pending a per-spec log to verify
spell_ids"). Two specific gaps were flagged by name: Vengeance DH missing
Fiery Brand entirely, and Protection Paladin's Divine Shield looking
mis-listed as a coverage lever.

This pass re-researched all four registries (Protection Paladin, Blood Death
Knight, Vengeance Demon Hunter, Brewmaster Monk) against current WoW Midnight
12.0.5/12.0.7 live data (Wowhead spell pages, Icy Veins, Method.gg, Maxroll,
Peak of Serenity — not training-data assumptions, since ability tuning shifts
across expansions and this project has been burned by stale-patch guesses
before). It found the two known gaps plus three additional correctness bugs.
`constants_version` 34 → 35 (this block's own comment requires a bump on any
edit, since the cached coverage windows are keyed on it).

## Real bugs fixed

- **Vengeance DH `Metamorphosis` was `school_scope: all`, should be
  `physical`.** Current Vengeance Meta grants +40% max HP and +200% armor —
  no flat all-school damage-reduction line exists on the current tooltip
  (confirmed via Wowhead raw aura data + Icy Veins + Wowpedia, no
  contradicting source found). The old `all` scope meant every magic hit a
  Meta'd Vengeance DH took was wrongly credited as "covered." This is the
  highest-traffic row in the VDH registry (Meta is used on essentially every
  pull), so it was the highest-leverage fix in this pass.
- **Brewmaster `Celestial Brew` was `role: continuous`, should be
  `coverage`.** It's a reactive absorb-shield cooldown (base ~45s, talent-cut
  by Tiger Palm/Keg Smash casts) that a player presses into a specific spike —
  the same shape as Shield Wall or Fortifying Brew, not a keep-it-up-100%
  tool like Ironfur. Under the old `continuous` tag it was invisible to the
  hit-vs-coverage join entirely (`build_coverage_report` only joins
  `role == "coverage"` levers); a Brewmaster who tanked a hit *with Celestial
  Brew's shield actually up* would have had that hit reported as
  "uncovered." Fixed to `coverage` + `cooldown_s: 45`.
- **Blood DK `Dancing Rune Weapon` was `tier: major`, should be `minor`.**
  Its DR mechanism is +20% parry chance — a probabilistic avoidance boost,
  not a flat cut — putting it in the same class as Demoralizing Shout's −20%
  debuff (the registry's one pre-existing `minor` row) rather than alongside
  a 40-50%+ personal wall. Icy Veins explicitly warns against treating it as
  a primary defensive ("primarily a resource/throughput cooldown"). Also
  fixed its stale cooldown (was pinned as if unaffected — base is still
  120s, unchanged) — see below for the other stale-cooldown fixes.
- **Stale cooldowns from a pre-Midnight tuning pass:** `Ardent Defender`
  120s → 90s, `Guardian of Ancient Kings` 300s → 180s (Protection Paladin);
  `Icebound Fortitude` 180s → 120s (Blood DK). These feed the "was a major
  cooldown available?" honesty split (`_lever_available_at` in
  `core/coaching.py`) — an overstated cooldown makes that split too
  conservative, hiding real "you had it up and didn't press it" moments
  behind a false "kit was spent."

## The Divine Shield question, resolved

The registry keeps Divine Shield (spell 642) as a `coverage`/`major` lever.
The classic tanking caveat is real and current — an immune tank drops off the
threat table and mobs can re-aggro onto a squishier party member — **but**
Protection Paladin has a capstone talent, **Final Stand** (spell 204077),
that makes Divine Shield taunt everything within 15 yards for its full
duration, neutralizing exactly that problem. Three independent current
guides (Method, Icy Veins, Maxroll-adjacent aggregate search) converge on
Final Stand being the default Midnight M+ capstone pick, not a fringe
choice. So: not mis-listed, just conditionally correct, which the schema
can't express directly. Left listed with a comment; the existing talent-gate
(a lever only counts once the log shows ≥1 real cast) is the practical guard
against a Final-Stand-less build ever getting coached to press it — the
residual risk is narrow (a player who cast it once anyway, without the
talent, could see a later "you had Divine Shield ready" callout that isn't
great advice for their build). Not worth a schema change for one ability's
edge case.

## New levers added

- **Vengeance DH `Fiery Brand`** (204021) — the known gap. Confirmed as a
  **self-buff** (not an enemy debuff, despite the ability applying its own
  separate offensive DoT DEBUFF to the target under a different aura — don't
  confuse the two), 40% flat all-school damage reduction, 60s base cooldown.
  Three independent sources rank it the #2 priority defensive after
  Metamorphosis.
- **Blood DK `Anti-Magic Zone`** (51052, minor, magic-only, ~240s) and
  **`Rune Tap`** (194679, minor, all-school, no `cooldown_s` — it's
  rune/RP-gated, not a fixed timer, so it's left out of the availability
  split rather than modeled with a fabricated number).

## Deliberately excluded (don't re-add without new evidence)

- **Blood DK `Purgatory` / `Death Pact`** — Purgatory is an always-armed
  passive that converts a killing blow into a temporary healing-absorb
  debuff; it has no "player pressed this, it's now active" window the
  buff-detect model can represent without producing a false "always covered"
  or "never detected" artifact. Death Pact's primary effect is a self-heal
  (with a healing-received penalty afterward), not a reduction in the damage
  of any specific hit — it doesn't fit the hit-vs-coverage join's question at
  all. **Blood DK `Lichborne`** — redesigned away from any damage-reduction
  component; it's CC-immunity + leech only now.
- **VDH `Netherwalk`** — confirmed cut from the Vengeance kit in the Midnight
  redesign. Removed from the registry (mirrors the Guardian
  `Rage of the Sleeper`-removed precedent) rather than left as a phantom row
  that silently never detects.
- **Brewmaster `Zen Meditation`** — confirmed removed from the game entirely
  in patch 11.2.0 (Warcraft Wiki + an independent Midnight ability-pruning
  article), predating this registry. Correctly never added.
- **Protection Paladin `Eye of Tyr`** — confirmed cut from the current
  Midnight Protection kit (replaced by a second Guardian of Ancient Kings
  charge via the `Empyrean Authority` talent). Correctly never added.
- **Lower-confidence single-source finds** — Protection Paladin's
  `Gift of the Golden Val'kyr` / `Adjudication` / `Strength in Adversity`;
  Brewmaster's `Celestial Infusion` (Celestial Brew's mutually-exclusive
  talent-node alternative); VDH's `Soul Barrier` / `Revel in Pain` /
  `Incorruptible Spirit` and any Aldrachi-Reaver/Annihilator hero-talent
  aura. Each needs its own dedicated spell-ID lookup before it can be
  trusted in the registry; per the fail-safe property this file's header
  comment already documents ("an incomplete list under-shows but never
  lies"), omitting them is the honest choice over guessing. Flagged here so
  the next pass doesn't have to re-derive the list from scratch — most
  notably, a Brewmaster who picked Celestial Infusion over Celestial Brew
  currently gets zero absorb-shield coverage credit.

## Verification

`tests/test_coaching.py` gained one test per touched spec
(`test_levers_for_spec_protection_paladin` /
`_blood_death_knight` / `_vengeance_demon_hunter` / `_brewmaster_monk`)
pinning the specific fixes above, on top of the pre-existing registry-wide
tripwires (`test_registry_rows_use_only_valid_enum_values`,
`test_rage_of_the_sleeper_is_not_in_any_registry`) that already auto-cover
any new row's field validity. Full suite: 1866 passed, 7 pre-existing skips.
`make lint` / `make typecheck` clean.

## Sources

Per-row citations (Wowhead spell pages, Icy Veins rotation/spell-summary
guides, Method.gg talent guides, Maxroll's M+ guide, Peak of Serenity's
Midnight S1 guide, Warcraft Wiki) were captured during the research pass;
the highest-value ones are inlined above. Re-verify against an actual parsed
log's `COMBATANT_INFO` + cast events before raising `calibrated: true` on any
of these four specs — this audit improves the coaching registry's accuracy,
it does not substitute for the per-spec log calibration that stays
data-blocked per the standing strategy.
