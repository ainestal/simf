# Patch 12.1.0 live-patch-notes verification (2026-08-13)

## Summary

12.1.0 "Curse of Ula'tek" shipped 2026-08-11 (NA) / 2026-08-12 (EU) — no
longer PTR. This session re-checked every tank-relevant scalar simf's
`patch/12.1.0-scalars` branch (merged as PR #472, commit `a90bc577`,
2026-08-11) and ROADMAP.md's coverage-gap section assumed, against Blizzard's
own shipped patch notes and independent secondary sources, now that a live
tooltip/patch-note check is actually possible. Six items were checked:
Blood-Soaked Ground (Blood DK), Soul Cleave (Vengeance DH), Celestial Brew
(Brewmaster Monk), Ignore Pain and Brutal Vitality (Prot Warrior), and Prot
Paladin's three previously-unmodeled levers (Improved Ardent Defender,
Bulwark of Order, Solace, Avenging Wrath).

**Outcome:** four items **CONFIRMED** matching what simf already assumed or
shipped; one item (**Celestial Brew**) **CORRECTED** — the shipped scalar
bump doesn't match the real patch note; two items (**Ignore Pain**, **Brutal
Vitality**) **RESOLVED** a prior open dispute from the 2026-08-07 scalar-prep
research (see the `patch_1210_ptr_scalar_prep_2026_08_07` memory). This
document records the verbatim findings and sourcing; it does not itself
change any constant — the Celestial Brew correction and the Ignore
Pain/Brutal Vitality constant update are being made separately, and this
doc's job is to give whoever makes those changes (and whoever reviews them)
a citable primary-source record rather than a re-derivation.

**Separately, and independent of the above:** WCL's "Mythic+ Season 2" zone
(55) contains only pre-ship PTR test data as of this writing — see its own
subsection below. Do not ingest it into any calibration corpus yet.

## Method

Two tiers of sourcing were used:

1. **WebSearch/WebFetch convergent secondary sourcing** for most items —
   fetching multiple independent community sources (icy-veins, ConquestCapped,
   Warcraft Wiki, Wowhead news, Method.gg) and Blizzard's own official notes
   page, and treating an item as settled when 2+ independent sources agreed
   verbatim.
2. **A real Playwright browser session** directly against Blizzard's
   official patch notes page and Wowhead's spell pages, for the items that
   resisted plain WebFetch. Two distinct failure modes showed up:
   - **Length truncation on Blizzard's own notes page.** `news.blizzard.com`'s
     Curse of Ula'tek article renders every class's change list into the page
     (even the ones collapsed behind a "▶ CLASS NAME" disclosure widget) as
     real DOM text, not lazy-loaded — but the full article, across all 13
     classes, is long enough that WebFetch's markdown conversion reliably cut
     off before reaching the alphabetically-last class, **Warrior**, on every
     attempt. Death Knight, Demon Hunter, Monk, and Paladin content came
     through fine via plain WebFetch; Warrior never did. Driving an actual
     Playwright session, clicking the "▶ WARRIOR" disclosure widget, and
     reading `document.querySelector('article').innerText` directly got the
     verbatim Protection Warrior section with no truncation.
   - **Wowhead's "Live" tab lags real patches for server-side hotfix-style
     tuning.** Wowhead's default spell-page tooltip is scraped from
     client-side datamined build data. Some Blizzard tuning ships as a
     server-only value change invisible to client datamining until a later
     client build catches up — the same category of change as the
     already-known **Ignore Pain +8% hotfix from 2026-05-12**, which never
     showed up as a client-side tooltip diff either. Wowhead's Ignore Pain
     page (`spell=190456`) was checked directly via Playwright and shows a
     raw AP-coefficient value (`AP mod: 16.124`) with no before/after framing —
     useful for the ability's existing shape, but not a reliable primary
     source for "how much did this patch change it," which is why Blizzard's
     own verbatim patch-note wording (obtained above) is the citation used
     for the actual percentages below, not Wowhead.

## Findings

### CONFIRMED — Blood-Soaked Ground (Blood DK)

Blizzard's official notes: *"Blood-Soaked Ground now reduces physical damage
taken by 8% (was 5%)."* Matches simf's shipped constant (`0.05` → `0.08`,
PR #472) and the existing `Blood-Soaked Ground` write-up in ROADMAP.md/PR
#453 — flat 5%→8%, gated on standing in the DK's own Death and Decay.

Sources: Blizzard official patch notes; [Warcraft Wiki — Blood-Soaked
Ground](https://warcraft.wiki.gg/wiki/Blood-Soaked_Ground) (independently
confirms 8%, was 5%, and the Death-and-Decay gating).

### CONFIRMED — Soul Cleave (Vengeance DH)

Blizzard's official notes: *"Soul Cleave healing increased by 25%."* Matches
simf's shipped `soul_cleave_heal_pct_missing_hp` ×1.25 bump (PR #472).

Sources: Blizzard official patch notes; icy-veins; ConquestCapped — all
three agree on the flat +25%.

### CORRECTED — Celestial Brew / Celestial Infusion (Brewmaster Monk)

Blizzard's official notes, verbatim (Brewmaster subsection, confirmed twice
independently via WebFetch's full-text markdown conversion of the
notes page and a third time verbatim via the Playwright accordion-click):

> "Celestial Brew and Celestial Infusion absorb increased by 150% and
> cooldown increased by 100%."

This is **not** the change simf's scalar-bump branch assumed. PR #472
shipped `celestial_brew_absorb_pct_of_max_hp` ×1.25 (0.20 → 0.25) on the
premise that Celestial Brew got the "same shape, same +25%" bump as
Blood-Soaked Ground and Soul Cleave, citing the same Warcraft Wiki-style
source. The real live patch note describes a much larger absorb increase
(+150%, not +25%) paired with a **cooldown increase** (+100% — i.e., the
ability recharges roughly twice as slowly) that the shipped fix doesn't
touch at all. This is two separate scalar changes on two separate fields
(`celestial_brew_absorb_pct_of_max_hp` and
`celestial_brew_cooldown_s`), not the single-field +25% the branch assumed.

One secondary source (ConquestCapped) reported this ability as "+25%,"
matching the wrong assumption rather than the shipped patch — most likely a
stale reading from an earlier PTR build, which is exactly the kind of
pre-ship number movement the project's "don't encode PTR numbers" policy
exists to guard against. Blizzard's own notes page (checked twice via
WebFetch, once via Playwright) is the more authoritative and internally
consistent source here.

**This document does not fix the constant.** A follow-up correction to
`celestial_brew_absorb_pct_of_max_hp` and `celestial_brew_cooldown_s` (and a
check of whether the forward-sim `hp_pct < 0.70` press gate — see
[[brewmaster_celestial_brew_never_presses_geared_forward_sim]] in memory —
changes this analysis at all) is tracked separately from this verification
pass.

Sources: Blizzard official patch notes (primary, verbatim); icy-veins (agrees
with Blizzard); ConquestCapped (disagrees, reads "+25%" — treated as the
less reliable of the two given verbatim primary-source disagreement).

### RESOLVED — Ignore Pain absorb (Prot Warrior)

Blizzard's official notes, verbatim (Protection Warrior subsection, obtained
via Playwright after WebFetch truncated before reaching it):

> "Ignore Pain absorb amount increased by 25%."

This resolves the open discrepancy flagged in the 2026-08-07 scalar-prep
research (`patch_1210_ptr_scalar_prep_2026_08_07` memory): Blizzard's dev
notes said +25% to the base absorb, but a raw PTR spell-tooltip scrape had
read +35%. The dev-notes reading was correct; the +35% PTR-era scrape was
not (plausibly an intermediate, pre-ship PTR build value, or a scrape error —
not chased further, since the live number is now directly confirmed from
Blizzard's own shipped notes rather than a scrape of either kind).

Independently corroborated by icy-veins and ConquestCapped, both of which
also read "+25%" for Ignore Pain's absorb.

**This does not, by itself, feed a `calibrate-k` recalibration** — see the
next section.

### RESOLVED — Brutal Vitality conversion rate (Prot Warrior)

Same Blizzard notes section, immediately following the Ignore Pain line:

> "Brutal Vitality adds 10% of damage dealt to Ignore Pain (was 8%)."

This resolves the other open item from the same 2026-08-07 research: the
live conversion rate moved from 8% to **10%** of damage dealt converted into
Ignore Pain absorb (simf's `brutal_vitality` constant is currently `0.05`, a
separately-fit conversion rate that was never anchored to either the 8% or
10% tooltip value in the first place — see below on why re-fitting it isn't
as simple as scaling by 10/8).

Corroborated by icy-veins and ConquestCapped (both read "8% → 10%"). A
WebSearch of Wowhead/Wowpedia/Warcraft Wiki also confirms Brutal Vitality's
existing (pre-12.1.0) design already includes the separate self-cap language
*"up to 15% of your total health"* — this is the same self-cap ROADMAP.md
item 6 already flags as entirely unenforced in `policy.py` today (only the
shared 30% Ignore Pain cap is checked, not Brutal Vitality's own 15%
sub-cap). That correctness gap is independent of the 8%→10% scalar bump and
is tracked separately.

### Why neither Ignore Pain nor Brutal Vitality feeds a recalibration (structural finding)

The original plan for these two constants — written into both ROADMAP.md's
scalar-bump subsection and the `patch_1210_ptr_scalar_prep_2026_08_07`
memory — was: "once 12.1.0 ships, get the real live-tooltip value, apply it,
then re-run `scripts/calibrate_spec_from_logs.py` against the 16-log Prot
Warrior corpus and report the honest before/after RMSE." A calibration-
scientist pass empirically proved this plan cannot work **as written** —
not because it's premature, but because it's a structural no-op. By
temporarily editing both constants to several different values and
re-running `calibrate-k`, the printed RMSE came back byte-identical every
time. The reason: `mitigation.py`'s log-replay path substitutes the log's
own `event.log_absorbed` field for the modeled absorb and never reaches the
Ignore Pain/Brutal Vitality chain those two constants feed — the same shape
of finding as the already-documented Celestial Brew replay bypass (see
`docs/validation/phase4_brewmaster_celestial_brew_cooldown_fix_2026_08_07.md`,
"Replay-based calibration: exactly zero, by construction").

**Consequence:** whatever value these two constants get updated to (25%
absorb / 10% conversion, matching the confirmed live tooltip above), that
update is correctly framed as a **correctness/tooltip fix**, not a
recalibration — `calibration_tier` does not move in either direction as a
result, and no before/after RMSE claim should be attached to it. ROADMAP.md
has been updated to reflect this; the original "re-run calibrate-k and
report RMSE" framing in that section and in the 2026-08-07 memory is
superseded by this finding.

### CONFIRMED — Prot Paladin's three levers (Bulwark of Order, Solace, Avenging Wrath, Improved Ardent Defender)

Blizzard's official notes, verbatim (Protection Paladin subsection, via
Playwright):

> "Improved Ardent Defender has been redesigned – Now increases maximum HP
> by 20% while active and no longer cancels remaining duration if fatal
> damage is sustained."
>
> "Bulwark of Order absorb increased to 75% of Avenger's Shield damage (was
> 60%)."
>
> "Solace causes Consecration to heal you for 375% of damage it deals (was
> 300%)."
>
> "Avenging Wrath increases damage and healing done by 10%, and critical
> strike by 10%."

All four numbers match exactly what ROADMAP.md's coverage-gap section
(items 3 and 7) already recorded from the PTR-era research pass — nothing
moved between PTR and ship for any of these four. All four remain entirely
unmodeled in `protection_paladin.py` today; this check only confirms the
target numbers are correct and stable, not that the modeling work is done.

## WCL zone 55 ("Mythic+ Season 2") is still PTR test data, not live — durable finding, don't ingest yet

Checked directly against WCL's authenticated API as of 2026-08-12/13: zone
55 ("Mythic+ Season 2") has only **5 reports ever**, all dated **before
ship** (2026-07-07 through 2026-08-01) — every one of them PTR-era test
data, not a real live run. One report has 140+ actors spanning 7+ different
home realms in a single report, a signature that only makes sense for a
shared PTR test realm, not an organic guild/pug run. Real live Season 2
runs (once keystones open 2026-08-18) will land here; **as of this writing,
zone 55 has none, and zone 47 ("Mythic+ Season 1" — never 45, which is
TWW, not Midnight) is still where real live uploads are landing.**

**Action for anyone building or expanding a WCL-sourced calibration corpus:**
do not pull reports from zone 55 today. Re-check after 2026-08-18 (Season 2
keystones live) and expect a lag of some days-to-weeks after that before a
meaningful number of real reports accumulate. This finding is independent
of anything else in this document and doesn't expire when the Celestial
Brew / Ignore Pain / Brutal Vitality items above are resolved in code.

## Sources

- <https://news.blizzard.com/en-us/article/24293281/curse-of-ula-tek-content-update-notes> — primary; Death Knight/Demon Hunter/Monk/Paladin sections read via WebFetch, Warrior section read verbatim via a live Playwright session (WebFetch truncated before reaching it every time).
- <https://www.icy-veins.com/wow/news/patch-12-1-curse-of-ulatek-is-now-live-patch-notes-survival-guide/>
- <https://www.wowhead.com/news/midnight-season-2-launches-august-18th-382287> — confirms 12.1.0 launched Aug 11, Season 2 keystones Aug 18.
- <https://www.method.gg/guides/protection-warrior>
- <https://conquestcapped.com/guides/wow/wow-patch-12-1-class-changes/> — the one source that disagreed on Celestial Brew (read "+25%," treated as the less reliable reading given verbatim primary-source disagreement).
- <https://warcraft.wiki.gg/wiki/Blood-Soaked_Ground>
- Wowhead spell page `spell=190456` (Ignore Pain), checked via Playwright — client-datamined "Live" tab, not used as the primary source for the patch-note percentages (see Method above).
