# Patch 12.1.0 "Curse of Ula'tek" — readiness tracker

> ⚠️ **PTR-DATAMINED, NOT LIVE. Do not hardcode any number below into
> `constants.yaml` until live patch notes ship and the numbers are confirmed.**
> Status as of **2026-06-28**: patch is on the PTR, no confirmed launch date.

This is the in-repo companion to the verified-facts memo
(`memory/patch_1210_curse_of_ulatek.md`). It tracks what simf will need to absorb
when 12.1.0 / Mythic+ Season 2 go live, and explicitly gates the recalibration on
live data so we don't burn effort on volatile PTR numbers.

## Provenance + confidence

Facts below were adversarially verified 2026-06-28 (multi-source incl. primary
Blizzard PTR channels; single-origin + fabrication risk both assessed LOW).
Everything is **PTR maturity** → subject to change before live.

**Re-verified 2026-07-06** against Blizzard's official news post ("Watch the
Latest WoWCast and Learn About The Curse of Ula'tek", news.blizzard.com) plus
Wowhead/Icy Veins/Warcraft Wiki corroboration — a stronger source than the PTR
dungeon-testing thread this doc originally cited. Zero discrepancy on the
dungeon list/origins. New finding from the re-verify: Blizzard's separate
"Midnight Season 2 Mythic+ Dungeon Philosophy and Design Goals" post confirms
Murder Row, King's Rest, Ruby Life Pools, and Temple of Sethraliss (plus
"and more") are getting **mechanically reworked** for Season 2 — see the note
in `data/dungeons.yaml`'s `season_2_catalog:` block. This means the 3
old-expansion returning dungeons' historical WCL data should not be reused as
a school_mix estimate even post-launch; each needs a fresh read on the
reworked encounter.

## Timeline (the load-bearing correction)

- **No confirmed launch date.** Blizzard lists only "Summer 2026". Independent
  projection has narrowed to **~August 11, 2026** specifically
  (blizzardwatch.com's dedicated date-tracking post, "very good odds point to
  August 11"), re-confirmed 2026-07-06.
- **July 7 is an in-game lead-in QUEST**, NOT the patch launch. A widely-repeated
  "patch July 7 / Season 2 July 14" claim is a search-summarizer artifact
  (lead-in date + 1 week) and is wrong.
- **Season 2 starts ~1 week after** the patch goes live (~mid-to-late August).
- **Implication:** there is no patch-day fire drill. ~6–8 weeks of runway, and the
  numbers will move. Gate all constant changes on live notes.

## What simf must absorb on launch

### Systemic (survivability-relevant)

- **+25% max-level player HP and +25% creature damage** — *approximately*
  net-neutral on raw burst-TTL (both up by the same factor; intent = "less
  spiky" damage), but NOT strictly neutral: absorbs that don't scale and
  %-max-HP self-heals (Frenzied Regen; the new Ardent Defender +20% max HP)
  shift the balance. Max level only, not a literal universal multiplier.
  Interacts with simf's `hp_per_stamina=22` and the per-dungeon damage profiles
  — the real answer is gated on the live re-fit below.

### Per-spec tank changes (PTR values — confirm on live)

| Spec | Change | simf modeling note |
|---|---|---|
| Prot Warrior | Ignore Pain **+25%** | absorb magnitude (calibrated spec — re-validate) |
| Guardian | Brambles **+25%** (+ After the Wildfire / Lunar Beam +25%, Ursoc's Fury 30→35%) | re-opens the Guardian calibration just finished |
| Brewmaster | Celestial Brew / Awakening Spirit / Staggering Strikes **+25%** | absorb + Stagger levers |
| Blood DK | broad defensive sweep (Permafrost 40→50, Voracious 12→15, Relish in Blood +25, Rapid Decomposition 50→85, …) | not a single 25% — multi-row |
| Vengeance DH | Frailty 8→10% (+25% rel) + Soul Cleave/Fel Dev/Feast healing +25% | self-heal levers |
| **Prot Paladin** | Improved Ardent Defender redesign = **+20% max HP while active** | **NOT an absorb buff — model as a max-HP / EH lever** |

> **Conflation hazard:** a *separate* **May 12 2026 LIVE hotfix** used DIFFERENT
> numbers (Ignore Pain +8%, AD 8→12s / 20→30% DR, Demonic Wards 8→12%) and
> excluded Guardian + Brewmaster. Do not mix those with the 12.1.0 PTR sweep.

### Mythic+ Season 2 — complete dungeon swap

All 8 differ from Season 1 (zero overlap). Verbatim from Blizzard's official PTR
dungeon-testing thread; staged as `season_2_catalog:` in `data/dungeons.yaml`
(names + origins only, NOT consumed by the live picker):

Altar of Fangs (new), Murder Row, Den of Nalorakk, The Blinding Vale, Voidscar
Arena (all Midnight) · Ruby Life Pools (DF) · Temple of Sethraliss, King's Rest
(BfA).

simf's `dungeons.yaml` S1 catalog + the 16-log S1 calibration corpus go
content-stale at launch.

## Recalibration plan (GATE ON LIVE)

When 12.1.0 ships and the numbers are confirmed (Brutoh verifies against live
patch notes / in-game tooltips):

1. Apply the confirmed +25% HP / +25% creature-damage rescale + per-spec absorb
   levers to `constants.yaml`; bump `last_verified_patch` + `constants_version`.
2. Re-fit K / `hp_per_stamina` against the systemic rescale using fresh Season-2
   **Warrior + Guardian** logs (the two specs the owner can produce).
3. Re-validate Guardian (the Brambles +25% re-opens it — see
   `phase4_guardian_finish_2026_06_28.md`).
4. Promote the `season_2_catalog:` dungeons into `dungeons:` once replayed
   (school_mix / par_time from real logs), and archive the S1 list.

## Strategic note (deferred, not gated)

Season 2's fresh-log flood is the ideal moment to stand up the **WCL
community-log ingestion pipeline** — the only lever that breaks the permanent
Warrior+Guardian-only data ceiling and could unblock the other four specs'
calibration. Director-flagged 2026-06-28; not started.
