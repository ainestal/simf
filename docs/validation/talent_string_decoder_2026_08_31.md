# Talent-string decoder — fixed (2026-08-31)

Reopens and closes `docs/validation/talent_string_decoder_2026_07_13.md`,
whose per-node content-section decode never worked. Found while chasing a
narrower, unrelated question: whether Guardian Druid's haste survivability
model (Elune's Chosen only, see `docs/validation/
phase4_guardian_haste_model_2026_06_24.md`) could be detected from a
pasted SimC `talents=` string instead of only a WCL log hydrate.

## Root cause

Blizzard's own shipped addon source
(`Blizzard_PlayerSpells/ClassTalents/Blizzard_ClassTalentImportExport.lua`,
fetched from `github.com/tomrus88/BlizzardInterfaceCode`) shows the real
per-node bit layout is:

```
Is Node Selected, 1 bit
  Is Node Purchased, 1 bit
    Is Partially Ranked, 1 bit
      Ranks Purchased, 6 bits
    Is Choice Node, 1 bit
      Choice Entry Index, 2 bits
```

The 2026-07-13 attempt's documented format skipped **"Is Node Purchased"**
entirely, going straight from "is selected" to "is partially ranked" /
"is choice node". A node that's selected-but-granted-for-free (writes
`selected=1, purchased=0` and *nothing else* — every real tree has a
handful of these starter nodes) desyncs every bit after it under the old
(never-shipped) format, because the next node's "is selected" bit gets
misread as the current node's "is partially ranked" bit. This exactly
matches that investigation's observed symptom ("corrupts almost
immediately, regardless of node list") — it was never about the node list
or the SubTreeSelection encoding width, both of which that investigation
also (independently, and as it turns out correctly) suspected.

Also confirmed via the same source: hero-talent picker nodes
(`Enum.TraitNodeType.SubTreeSelection`) use the **identical** 1-bit-choice
+ 2-bit-index encoding as a normal `Selection` choice node — same width,
not distinguished at the bit level, only by which real node_id they land
on. That resolves the 2026-07-13 doc's "leading hypothesis, unconfirmed."

## Node data

The per-node walk needs the full ordered node structure Blizzard's client
walks (`C_Traits.GetTreeNodes`), not just entry_id → name. This comes from
SimulationCraft's `trait_data.inc` (`midnight` branch), whose
`trait_data_t` struct (`engine/dbc/trait_data.hpp`) carries every field
needed — `id_node` (for ordering), `node_type` (0=normal, 1=tiered,
2=choice, 3=sub-tree-selection), `max_ranks`, `selection_index` (choice
entries' on-screen order), `id_spec`/`id_sub_tree` (tree membership). This
is a strictly richer extraction than the 2026-07-15-era
`scripts/fetch_talent_tree.py`, which only pulled `entry_id → name` for
the (order-independent) COMBATANT_INFO join and was never meant to
support a string decode.

Tree membership is **per-CLASS, not per-spec** — confirmed empirically,
not assumed: a real string only decodes with zero leftover bits when
every spec's spec-tree rows (`tree_index=2`) and every hero tree's body
nodes (`tree_index=3`) are included unconditionally, not filtered to the
target spec. Blizzard's client evidently uses one shared, class-wide node
ordering; only the reachable subset for a given character ever reads as
selected. See `scripts/fetch_talent_string_nodes.py`'s module docstring
for the full derivation, including the empirical discovery that a
first, spec-filtered attempt (133 nodes instead of 209 for Warrior)
undercounted and immediately desynced — the same failure shape as
2026-07-13, for a different reason.

## Verification against real ground truth

**Protection Warrior — Brutoh, `examples/brutoh.simc` (2026-05-06):**
decodes with `ok=True`, **zero leftover bits**. Cross-checked against his
REAL, same-day COMBATANT_INFO from `examples/WoWCombatLog-050626_153703.txt`
(79 real `(node_id, entry_id)` pairs): **78/79 exact match.** The sole
miss is node 110411 (Phalanx), a tiered "Apex" 4-point cluster split
across 3 DBC rows — its selected/purchased/rank bits decode correctly,
it just doesn't resolve to one single `entry_id` (by design — see
`SelectedNode`'s docstring). His hero-talent pick (node 99851) resolves
to entry 123388 → id_sub_tree 61 → **Mountain Thane**, matching CONTRIBUTING.md's
documented KYFOTG (Mountain Thane debuff) context for his real build. A
second, independent real string (`examples/Brutoh Protection
2026-07-08-vault.txt`, two months later) also decodes cleanly (3 leftover
bits — harmless base64 padding) and agrees: Mountain Thane, 15/16
hero-tree reads consistent.

**Guardian Druid — AnonGuardian1, the Vault export behind this investigation's
original question:** decodes with `ok=True`, 3 leftover bits (harmless).
The hero-tree picker node (99807) resolves to entry 123295 → id_sub_tree
24 → **Elune's Chosen** — confirming what `docs/validation/
phase4_guardian_haste_model_2026_06_24.md` asserted about AnonGuardian1's profile,
this time derived directly from their talent string rather than assumed.
14 of 15 hero-tree body-node reads agree (Elune's Chosen); one single
phantom "Ravage" (Druid of the Claw) read is the known noise (see below).
Two more independent AnonGuardian1 exports and AnonGuardian3's real Guardian Druid
export (`examples/AnonGuardian3.simc`) all independently confirm Elune's
Chosen too.

## What shipped

- `io/talent_string_codec.py`: `decode_full_loadout` — the real per-node
  decoder, plus `hero_tree_choice` — the character's hero-talent tree name,
  read specifically from the `SUB_TREE_SELECTION` node (not the noisier
  body-node majority).
- `scripts/fetch_talent_string_nodes.py` + committed
  `data/talent_trees/{protection_warrior,guardian_druid}_string_nodes.json`.
- `io/talent_decoder.py`: `decode_simc_talent_string` now actually decodes
  (previously always `ok=False`) — Protection Warrior only, matching its
  existing scope (the modeled-talent catalog join). New
  `decode_hero_tree_name` — spec-agnostic hero-tree query, for specs like
  Guardian Druid with no modeled-talent catalog at all.
- `io/simc_import.py`: a Guardian Druid SimC paste that decodes to Elune's
  Chosen now sets `active_buff_spell_ids` to the Ironfur haste model's
  gate buff (Fury of Elune, 202770) — read from `constants.yaml`'s
  `ironfur_haste_model.detect_buff_spell_id`, not a second hardcoded
  literal. This is the exact signal the WCL log-hydrate path already sets
  from a real buff observation (`character_from_combatant_info.py`); the
  gap this closes was named as "still pending" in the original 2026-06-24
  haste-model doc ("the SimC-*paste* path... discards the talent hash").

## Known remaining limitations (by design, not deferred)

- **Non-chosen-hero-tree noise.** The per-class walk includes body nodes
  for hero trees a character did NOT pick, and a handful spuriously read
  as "granted" (root cause not pinned down — possibly a duplicate/shared
  node-id interaction between a class's hero trees; Warrior showed 14 such
  phantom reads out of ~93 selected nodes, all cleanly attributable to the
  non-chosen hero tree by name). `decode_simc_talent_string` and
  `hero_tree_choice` both filter hero-tree entries to the character's own
  decoded hero-tree choice before trusting them — a correct invariant
  regardless of this raw-read noise (a character cannot really have points
  in a hero tree they didn't pick), not a fix at the bit level.
- **Tiered/"Apex" nodes** (Warrior's Phalanx) decode their bits correctly
  but don't resolve to one `entry_id` — excluded from `modeled`/`all_talents`
  by the existing "no claim, not a guess" convention, same as before.
- **Older strings can't decode under the current tree.** A string exported
  before a talent-tree change (nodes added/removed/reordered across
  patches) runs genuinely short of bits partway through the walk, since
  the committed node data is current-patch-only. `decode_full_loadout`
  fails closed (`ok=False`) rather than raising — caught live during this
  fix's own validation sweep against every real `.simc`/vault-export
  fixture in `examples/` (19 files, 0 crashes): a 2026-06-10 Brutoh export
  predates this exact node set and correctly falls back instead of
  crashing `simc_to_character_yaml`.
- Only Protection Warrior and Guardian Druid have committed node data
  (`_TREE_FILE_STEM` in `talent_string_codec.py`) — the two specs this
  investigation actually needed. Extending to another spec is a
  `scripts/fetch_talent_string_nodes.py` target-list addition plus
  re-running it, not new decoder work.

## Follow-up validation (2026-09-01) — AnonGuardian1's fresh logs + independent WCL players

The user dropped 8 fresh AnonGuardian1 combat logs (Aug 24-31) into
`examples/anonguardian1-guardian/` and asked for broader validation, including
independent online logs. Initial investigation found what looked like a
GUID collision — 4 of the 8 logs carry a *different* GUID under the name
"AnonGuardian1" (`Player-9001-AAAA0001`, realm AnonRealm4-EU) than the other 4
(`Player-9002-AAAA0002`, realm AnonRealm1-EU, matching the SimC vault
export this whole investigation started from) — and were initially
(incorrectly) reported here as an unrelated same-named character. **The
user corrected this**: it's the same AnonGuardian1, realm-transferred from
AnonRealm1 to AnonRealm4 (a more populated realm) between the Aug 28 and Aug 29
logs — Blizzard GUIDs are realm-scoped, so a transfer changes the GUID
without changing the character. All 8 logs are the real subject.

**Across the 7 of 8 logs with a COMBATANT_INFO match for them** (the 8th,
`WoWCombatLog-083126_101933.txt`, genuinely has none for their GUID —
absent from that particular pull, not an error), every method agrees on
every log, both pre- and post-transfer:
- `hydrate_character` (existing, already-reliable buff-detection path):
  `active_buff_spell_ids = frozenset({202770})` (Fury of Elune) in all 5
  logs it could resolve a fight window for (2 of the 7 return `None` from
  `hydrate_character` specifically — ambiguous fight-window selection,
  not a talent-detection failure — but still show clean entries via the
  direct join below).
- This PR's SimC-string decode of their Aug 28 vault export (pre-transfer):
  Elune's Chosen.
- Direct entry-id join of each log's real COMBATANT_INFO talent block
  against `guardian_druid_string_nodes.json`: 15/15 hero-tree entries
  Elune's Chosen in all 7, zero Druid-of-the-Claw noise this time (the one
  phantom "Ravage" read noted above was specific to that one earlier
  `talents=` string decode, not a WCL-CombatantInfo artifact) — including
  after the realm transfer, confirming the pick survived it.

**Independent online players**: reused the 27 real, ACL-confirmed WCL
fights already discovered across three prior cross-player calibration
rounds (`guardian_druid_cross_player_wcl_2026_08_14.yaml`,
`guardian_druid_s2_keylevel_2026_08_30.yaml`,
`guardian_druid_s2_keylevel_2026_08_31.yaml`) — different players, servers,
and regions, spanning Aug 14-31. Queried `fetch_combatant_info_events`
directly (note for future one-off scripts: WCL's `talents` field is empty
for hero-talent-era characters — the real data is `talentTree[].id`,
already correctly used by `io/wcl_combatant_info.py` and named in
`fetch_combatant_info_events`'s own docstring; this ad hoc validation
script just didn't check that first) and joined each Guardian Druid's
real talent entries against the same node data. **All 27: 100%
Elune's Chosen, 0% Druid of the Claw** (225 total hero-tree entry hits).

Sanity-checked this isn't a mapping gap: `guardian_druid_string_nodes.json`
carries 19 real, named Druid of the Claw entries (Ravage, Wildshape
Mastery, Bestial Strength, ...) — a Druid of the Claw player would be
correctly detected, there just wasn't one in this ~34-fight combined
sample (7 AnonGuardian1 + 27 independent). Real finding about the current
playerbase (Elune's Chosen reads as heavily dominant in Season 2 M+ right
now, at least in this WCL sample), not a code bug — worth keeping in mind
for any future Guardian Druid hero-talent characterization work.

## Calibration impact

None on the ratified corpus — that's built from WCL log hydrate
(`character_from_combatant_info.py`), which already had a working,
independent `decoded_talents` path (`decode_combatant_info_entry_ids`)
before and after this fix. This fix only changes the SimC-*paste* path
(`simc_import.py`), used by the Gear/Vault UI's demo-character and
user-uploaded-.simc flows, not the calibration RMSE pipeline.

It does change live behavior for a Protection Warrior user pasting a
current (non-stale) `talents=` string: `decoded_talents` now populates
with their REAL selected talents instead of staying `None` (falling back
to nearest-preset-name matching) — a real accuracy improvement, and
directly relevant to the still-open `protwarrior_decoded_talents_ignored_by_runner`
memory note (Brutoh's real build includes Unyielding Stance, which his
ratified `brutoh-actual` preset loadout does not) — that memory's
`runner.py`-ignores-`decoded_talents` gap is unchanged by this fix and
remains a separate, deliberately-deferred calibration decision.
