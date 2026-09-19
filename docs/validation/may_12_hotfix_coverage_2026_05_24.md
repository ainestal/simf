# May 12, 2026 tank-tuning hotfix — simf coverage audit (2026-05-24)

Per Icy Veins' tank-tuning hotfix coverage (May 12, 2026) and the matching
Blizzard hotfix note, the May 12 patch was the largest survivability-tuning
pass since Midnight 12.0.5 launched. Every spec simf supports got moved.
This doc enumerates per-spec what's modeled at HEAD, what's been bumped to
post-hotfix values, and what's deferred behind per-spec log calibration.

The motivating finding: when this audit started, two of the Prot Warrior
changes (Phalanx 5→8%, Ignore Pain effectiveness +8%) had already been
applied to `constants.yaml` with `# May 12 2026 hotfix` comments — landed
in the initial commit. The audit trail of "what's modeled vs. what's
stubbed" was undocumented; we were rediscovering our own state. This doc
closes that gap permanently.

## Status legend

- ✅ **Modeled + at post-hotfix value**
- ⚠️ **Modeled but at pre-hotfix value** — bump pending
- ❌ **Not modeled** — ability/talent absent from `constants.yaml`; needs new
  modeling code path before a value can mean anything
- 🪦 **Out of scope** — DPS-side change, won't affect survivability outputs

## Protection Warrior (`calibrated: true`, K=3430, RMSE 0.065 / 16 logs)

| Change | Status | Notes |
| --- | --- | --- |
| Phalanx 5% → 8% damage taken debuff | ✅ | `talents.phalanx.damage_taken_debuff_on_target: 0.08` (line 350) |
| Ignore Pain effectiveness +8% | ✅ | `active_mitigation.ignore_pain.absorb_pct_of_max_hp: 0.216` = 0.20 × 1.08 (line 228) |
| Fight Through Flames 4% → 6% | ✅ | Added 2026-05-24 (this PR). New `talents.fight_through_flames.magic_dr: 0.06` + gate in `mitigation.py` 5c-bis. Not in any current loadout — bit-identity preserved on existing seeds |

**Net impact on Brutoh's calibration:** zero. The two pre-existing patches
were already in the K=3430 fit; FtF is not in `brutoh-actual`. Calibration
RMSE on the 16-log set is unchanged by this PR.

**Marginal for users who DO take FtF:** ~6% × (1 − existing_magic_DR) ≈
~4.5pp on magic events for a tank already in Defensive Stance (−15%
all-schools) + party-aura `all: 0.05`. Computed per
[[feedback_dr_stacking_arithmetic]] before promising.

## Protection Paladin (`calibrated: false`)

| Change | Status | Notes |
| --- | --- | --- |
| Blessing of Dusk 10% → 20% DR (scales with health) | ❌ | Not modeled. New talent path needed — health-scaling DR is a tier the current Paladin module doesn't have |
| Sanctified Plates stamina 5/10% → 7/15% | ❌ | Not modeled. Stamina-multiplier talents are absent from `constants.yaml` |
| Ardent Defender duration 8s → 12s | ⚠️ | `ardent_defender_duration_s: 8.0` (line 775) — bump pending |
| Ardent Defender DR 20% → 30% | ⚠️ | `ardent_defender_dr: 0.20` (line 774) — bump pending |
| Sentinel duration 16s → 20s | ❌ | Current model uses `sentinel_absorb_pct_per_10s` + cap; no explicit duration knob |

**Why deferred:** Three of five items require **new modeling code** (BoD,
Sanctified Plates, Sentinel duration), not constant bumps. Bumping AD
20→30%/8→12s in isolation while BoD and Sanctified Plates remain unmodeled
would shift the Paladin verdict by a partial set of the hotfix — worse
than leaving the entire spec at its current `calibrated: false` warning,
because users would see a number that looks more authoritative without
actually being so.

**Promotion gate:** Bundle the full hotfix patch + the new ability paths
with the per-spec log-calibration phase. Need ≥ 2 Prot Pal M+ logs to
validate, per `specs.protection_paladin.calibrated` block.

## Blood Death Knight (`calibrated: false`)

| Change | Status | Notes |
| --- | --- | --- |
| Dancing Rune Weapon parry 20% → 25% | ❌ | DRW itself is not modeled in `blood_death_knight.py` |
| Blood Fortification stamina 35% → 40% | ❌ | Talent not present in `constants.yaml` |
| Improved Death Strike heal 5% → 15% | ❌ | Talent multiplier on top of base DS heal — not wired in |
| Dance of Midnight duration 6s → 8s | ❌ | Talent not modeled |
| Dance of Midnight proc 10% → 12.5% | ❌ | Talent not modeled |

**Why deferred:** All five items are **new ability modeling**, not value
bumps. DRW is a meaningful cooldown (~25-30% effective damage taken
reduction during uptime via parry stack) — adding it correctly is a
multi-line policy.tick path. Same gate as Prot Pal: bundle with per-spec
log-calibration phase.

## Vengeance Demon Hunter (`calibrated: false`)

| Change | Status | Notes |
| --- | --- | --- |
| Demonic Wards 8% → 12% DR | ❌ | Class-passive DR not separated as a constant |
| Thick Skin 50% → 55% stamina | ❌ | Stamina-multiplier talent not modeled |
| Mastery: Fel Blood +20% effectiveness | ❌ | Mastery scaling for VDH not modeled |
| Void Reaver Frailty 4% → 5% DR | ❌ | Talent not modeled |

**Why deferred:** Same as above — new ability paths. Plus VDH mastery
mechanics differ enough from Prot Warrior's block-based mastery that the
existing `mastery_dr_scaling` knob isn't a drop-in fit.

## Guardian Druid (`calibrated: false`)

May 12 hotfix included **no survivability buffs for Guardian** — the only
listed changes were DPS-side. ✅ Nothing to patch.

(Older Guardian changes the director mentioned — Thrash damage −25%,
Elune's Favored 25→15% — are pre-Midnight 12.0.5 or damage/healing
adjustments, not survivability tuning. Out of scope for this audit.)

## Brewmaster Monk (`calibrated: false`)

🪦 Not included in the May 12 hotfix batch. A separate May 5 hotfix
adjusted High Tolerance (3/6s → 2/4s purify CDR) and Zen State (20% → 15%
stagger) — both stagger-pool mechanics. The simf Brewmaster model uses
`purifying_brew_avg_per_minute` and `stagger_pct_physical/_magic`
abstractions that don't expose CDR or per-talent stagger overrides
directly. Bumping either knob would be a value migration of a placeholder
calibration that's never been log-validated.

**Why deferred:** Same gate as the other uncalibrated specs.

## What this audit unblocks

Per the strategic note from director (this session, 2026-05-24): the real
win is converting "five specs at `calibrated: false` with vague TODOs"
into an auditable per-spec ability-modeling backlog. The Prot Pal /
Blood DK / VDH / Brewmaster sections above are now that backlog.

The promotion gate to flip any spec to `calibrated: true` is:
1. Implement the listed `❌` items (new code paths)
2. Bump the listed `⚠️` items (value migrations)
3. Acquire ≥ 2 real M+ M+ logs for the spec
4. Validate within ±15% RMSE per the standing `specs.<spec>.calibrated`
   comment block
5. Bump `constants_version` and update this doc

## Sources

- Icy Veins: [Tank Tuning Hotfixes May 12, 2026](https://www.icy-veins.com/wow/news/tank-tuning-class-fixes-prey-improvements-midnight-12-0-5-hotfixes-may-12th/)
- Icy Veins: [Class Tuning Pass May 5, 2026](https://www.icy-veins.com/wow/news/the-huge-class-tuning-pass-is-here-midnight-12-0-5-hotfixes-may-5th/)
- SimC source for FtF: `docs/simc-reference/AUDIT.md` F4 + spell 452494 effect 3 of buff 386208 (Defensive Stance), school mask 126
- Constants file: `src/simf/data/constants.yaml` lines 228, 350, 349-356, 763-784
- Memory references: [[feedback_engine_batch_ratification]] (ratification cap), [[feedback_dr_stacking_arithmetic]] (marginal computation discipline)
