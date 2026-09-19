"""Sim-derived per-stat survivability marginals (Phase 2.7).

Replacement for `core/marginals.py:ehp_marginals` — which is a closed-form
derivative of `effective_hp = max_hp / ((1 − armor_dr)(1 − vers_dr))` and
therefore returns *zero* for haste / crit / mastery / strength and ignores
self-sustain (Death Strike, Soul Cleave, Brutal Vitality, Frenzied Regen,
Word of Glory, …). Every gear surface uses these marginals as the source
of truth for "+X eHP" — the structural bias toward versatility came from
right here.

This module measures each stat's value by perturbation against the live
Monte Carlo engine. Output shape matches `ehp_marginals` exactly:

    {
        "stamina":            {"p": ..., "m": ...},
        "armor_from_gear":    {"p": ..., "m": ...},
        "versatility_rating": {"p": ..., "m": ...},
        "haste_rating":       {"p": ..., "m": ...},
        "crit_rating":        {"p": ..., "m": ...},
        "mastery_rating":     {"p": ..., "m": ...},
        "strength":           {"p": ..., "m": ...},
    }

so `optimizer/per_dungeon.py:_score_for_school_mix` keeps working bit-identically.

## Defense of the eHP units

The sim returns ETMI-12 (TMI with externals, 12s window) — a log-scale
measure of how much damage the tank takes net of self-heal and absorbs,
sliding-windowed and exp-weighted per the SRD. Lower ETMI = more survivable.

To translate ΔETMI back into raw-damage-absorbable-before-death ("eHP")
units, the function uses a **stamina-anchor calibration**:

  - Stamina has an exact closed-form derivative:
      dEHP/dstam = hp_per_stam / ((1 - armor_dr)(1 - vers_dr))    (physical)
                 = hp_per_stam / (1 - vers_dr)                    (magic)
    The math is rock-solid: stamina linearly grows max_hp, max_hp
    linearly scales eHP, no other interaction.
  - We measure the sim's ΔETMI per unit of stamina via perturbation.
  - The conversion constant is K = closed_form_dEHP_per_stam / sim_dETMI_per_stam.
  - For every other stat x:
        eHP_per_unit_x = (sim_weight_x / sim_weight_stam) × closed_form_dEHP_per_stam

By construction, the stamina marginal returned here matches the closed-form
exactly. Armor and versatility have known closed-form derivatives too —
they serve as *independent sanity checks*: if sim-derived armor / vers
marginals disagree with their closed-form values by more than ~20%, the
engine has a bug (iteration count too low, heal_timeline not populated for
this spec, metric not behaving as advertised). The anchor tests assert this.

For mastery / haste / crit there is no closed-form. The number shown is
the engine's own answer, expressed in stamina-equivalent eHP units.

## Cost

Default settings: 1 baseline + 6 perturbations = 7 sims per profile,
× 2 profiles (physical / magic), × ~300 iter each ≈ 4 200 iter total.
On Pi (≈10 ms/iter) ≈ 40 s sequentially, ≈ 10–15 s with 4-worker
ProcessPoolExecutor. Cache callers so character load triggers this once.
"""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from dataclasses import replace as dc_replace

import numpy as np

from .character import Character
from .constants import load_constants
from .events import AttackType, DamageSchool
from .metrics import SRD_M_PLUS_WINDOW_S, tmi_iteration_expsums
from .profiles import (
    DamageProfile,
    HealingProfile,
    MobSpec,
    TankBuster,
    load_damage_profile,
    load_healing_profile,
)
from .runner import run_simulation

# Stats we perturb. Order matters only for parallel dispatch; the dict
# returned is keyed by stat name. Strength included so the {p, m} shape
# matches `ehp_marginals` exactly — Prot Warr / Pal / DK / DH get parry
# from strength, so it has real survivability value the closed-form
# also misses. Agility likewise: Guardian armor scales with agility via
# Ironfur (total_armor += agi × coeff × stacks) AND agility raises Guardian
# dodge — both flow through the perturbed re-sim automatically; ~0 for specs
# whose armor/dodge don't scale with agility (plate tanks).
PERTURBED_STATS = (
    "stamina",
    "armor_from_gear",
    "haste_rating",
    "crit_rating",
    "mastery_rating",
    "versatility_rating",
    "strength",
    "agility",
)

# Perturbation deltas per stat. Tuned so each perturbation produces signal
# above sim noise without distorting the marginal too much (we treat it as
# a finite-difference estimate of a local derivative).
PERTURB_DELTA = {
    "stamina": 2000,  # ~5–10% of typical M+ tank stamina (~25k)
    "armor_from_gear": 5000,  # ~5% of typical M+ tank armor (~100k)
    "haste_rating": 2000,  # 1000 ~= 5% haste with current rating_per_pct
    "crit_rating": 2000,
    "mastery_rating": 2000,
    "versatility_rating": 2000,
    "strength": 2000,
    "agility": 2000,  # Guardian: agi → Ironfur armor + dodge; ~0 for plate tanks
}

# Sentinel: the *cumulative* ETMI shift between baseline and perturbed
# sim must clear this magnitude for us to trust the marginal. Below this
# the result is noise (or genuine zero, e.g. crit for Prot Warr). We
# return zero for that stat so the picker doesn't chase wobble.
# Comparison is against |ΔETMI| at the configured PERTURB_DELTA — typical
# observed range across stats is 300–5000 for survivability-relevant stats
# at 200–300 iter, so 5.0 is well below signal but above iteration noise.
_NOISE_FLOOR_DELTA_ETMI = 5.0


@dataclass(frozen=True)
class SurvivabilityWeights:
    """Per-stat ΔeHP per unit of stat, split by school. Same shape as
    `ehp_marginals()` returns. `meta` carries the raw sim outputs for
    debugging + the closed-form sanity-check residuals.

    ``ci`` is an optional 95% confidence interval on each marginal (same
    shape as ``marginals``, values are ``(lo, hi)`` tuples or ``None`` where
    no CI applies — a physics-pinned cell, a noise-floor-zeroed cell, an
    unstable anchor school, or ``ci_resamples=0``). See
    ``_bootstrap_marginal_ci`` for the construction."""

    marginals: dict[str, dict[str, float]]
    meta: dict
    ci: dict[str, dict[str, tuple[float, float] | None]] | None = None


def _run_one(payload: tuple) -> tuple[float, list[float], int]:
    """Module-level worker: run one sim, return (ETMI-12, per-iteration
    exp-sums, bins-per-iteration).

    Must be module-level so ProcessPoolExecutor can pickle it. Exp-sums are
    the additive per-iteration terms inside the SRD log-sum-exp (see
    ``core/metrics.py::tmi_iteration_expsums``) — the right unit for a
    paired-bootstrap CI on a CRN run. Computed HERE, inside the worker, so
    only ~300 floats round-trip the process pool instead of the full
    per-iteration damage/heal timelines.
    """
    char, dmg, heal, iters, seed = payload
    result = run_simulation(
        char,
        dmg,
        heal,
        iterations=iters,
        seed=seed,
        compute_metrics=True,
        compute_tmi_metrics=True,
        keep_iteration_results=True,
    )
    assert result.iteration_results is not None  # noqa: S101 — keep_iteration_results=True above
    expsums, bins_per_iter = tmi_iteration_expsums(
        result.iteration_results,
        result.sample_max_hp,
        result.sample_duration_s,
        window_s=SRD_M_PLUS_WINDOW_S,
        include_externals=True,
    )
    return float(result.etmi_12), expsums.tolist(), bins_per_iter


def _bootstrap_marginal_ci(
    raw: dict[tuple[str, str], tuple[float, list[float], int]],
    cf_stam_p: float,
    cf_stam_m: float,
    sim_stam_p: float,
    sim_stam_m: float,
    n_resamples: int,
    seed: int,
) -> tuple[dict[str, dict[str, tuple[float, float] | None]], dict]:
    """Joint paired-bootstrap 95% CI on every stat's eHP marginal.

    Resamples iteration INDICES (zero extra simulations) from the
    per-iteration exp-sums ``_run_one`` already computed, extending the
    plain two-arm paired-bootstrap technique to the ratio this module's
    anchor conversion needs: each stat's eHP
    marginal is ``(sim_weight_stat / sim_weight_stam) × closed_form_stam``,
    so a valid CI requires resampling the SAME iteration indices across the
    baseline, the target stat's arm, AND the stamina arm within one profile
    (they share a baseline and the CRN pairing only holds jointly). One
    index matrix per school is applied to every arm.

    Design validated 2026-07-08 (calibration-scientist pass, empirical
    check against a real character/profile pair): CRN resolves marginals as
    small as ~1 eHP/pt with a non-degenerate CI, and every bootstrap
    replicate's log-sum reconstruction is bit-identical to the point
    estimate's own ``result.etmi_12`` (the ``1e4`` scale factor and
    ``ln(N0/N)`` offset are identical across every arm of a school at fixed
    iteration count, so both cancel in every delta below — this mirrors the
    point-estimate derivation exactly, just resampled).

    Guard: if the stamina-anchor denominator sign-flips in ANY replicate for
    a school (the ratio would blow up / change sign nonsensically), every
    stat's CI on that school is ``None`` with the flip count recorded in
    ``meta`` — the CI-layer sibling of the point estimate's own
    ``anchor_failed`` guard. Not observed at any tested operating point, but
    a near-death character or a very easy profile could concentrate the
    stamina ETMI shift near zero.
    """
    rng = np.random.default_rng(seed)
    ci: dict[str, dict[str, tuple[float, float] | None]] = {
        stat: {"p": None, "m": None} for stat in PERTURBED_STATS
    }
    sign_flips: dict[str, int] = {"p": 0, "m": 0}

    schools = (("p", cf_stam_p, sim_stam_p), ("m", cf_stam_m, sim_stam_m))
    for profile_key, cf_stam, point_sim_stam in schools:
        base_expsums = np.array(raw[(profile_key, "baseline")][1])
        stam_expsums = np.array(raw[(profile_key, "stamina")][1])
        n = len(base_expsums)
        idx = rng.integers(0, n, size=(n_resamples, n))

        ln_base = np.log(base_expsums[idx].sum(axis=1))
        ln_stam = np.log(stam_expsums[idx].sum(axis=1))
        delta_etmi_stam = 1e4 * (ln_stam - ln_base)
        sim_weight_stam = -delta_etmi_stam / PERTURB_DELTA["stamina"]

        point_sign = 1.0 if point_sim_stam >= 0 else -1.0
        flips = int(np.sum(np.sign(sim_weight_stam) != point_sign))
        sign_flips[profile_key] = flips
        if flips > 0:
            continue  # every stat's CI on this school stays None

        for stat in PERTURBED_STATS:
            if stat == "stamina":
                continue  # closed-form anchor — zero-width by construction
            stat_expsums = np.array(raw[(profile_key, stat)][1])
            ln_stat = np.log(stat_expsums[idx].sum(axis=1))
            delta_etmi_stat = 1e4 * (ln_stat - ln_base)
            sim_weight_stat = -delta_etmi_stat / PERTURB_DELTA[stat]
            ehp_dist = (sim_weight_stat / sim_weight_stam) * cf_stam
            lo, hi = np.percentile(ehp_dist, [2.5, 97.5])
            ci[stat][profile_key] = (float(lo), float(hi))

    # Physics-exact pin — armor doesn't mitigate magic. Nothing measured,
    # nothing to bound; mirrors the point estimate's own hardcoded 0.0.
    ci["armor_from_gear"]["m"] = None

    return ci, {
        "resamples": n_resamples,
        "seed": seed,
        "denominator_sign_flips": sign_flips,
    }


def _all_physical(profile: DamageProfile) -> DamageProfile:
    """Project every event in a profile onto the 'physical' school.

    The marginal weight for school 'p' should measure stat value in a
    pure-physical pressure environment. Mutating an existing calibrated
    profile (rather than synthesizing from scratch) keeps mob counts /
    swing timers / cadences realistic.
    """
    return _project_profile(profile, "physical", "melee")


def _all_magic(profile: DamageProfile) -> DamageProfile:
    """Project every event onto the 'shadow' school as a magic stand-in.

    Shadow is mitigated by versatility only (no armor) and is one of the
    most common high-key magic schools (Stonevault, Ara-Kara, MGT). Pick
    any single magic school — the closed-form marginal makes no school
    distinction within 'magic' so the choice is arbitrary as long as we
    stay magic and aren't accidentally a school the spec has a special
    interaction with (e.g. Defensive Stance is 20% non-physical DR — it
    applies equally to shadow / fire / nature, so no bias introduced).
    """
    return _project_profile(profile, "shadow", "spell")


def _project_profile(
    profile: DamageProfile, school: DamageSchool, attack_type: AttackType
) -> DamageProfile:
    new_mobs = []
    for m in profile.mobs:
        new_casts = [{**cast, "school": school} for cast in (m.casts or [])]
        new_mobs.append(
            MobSpec(
                count=m.count,
                swing_timer_s=m.swing_timer_s,
                swing_damage_mean=m.swing_damage_mean,
                swing_damage_variance=m.swing_damage_variance,
                school=school,
                attack_type=attack_type,
                casts=new_casts,
            )
        )
    new_busters = [
        TankBuster(
            time_s=tb.time_s,
            damage=tb.damage,
            school=school,
            attack_type=attack_type,
            is_avoidable_by_spell_reflect=False,
        )
        for tb in profile.tank_busters
    ]
    return dc_replace(profile, mobs=new_mobs, tank_busters=new_busters)


def _closed_form_dehp_per_stam(char: Character) -> tuple[float, float]:
    """Exact closed-form ∂eHP/∂stamina for physical and magic schools.

    Stamina linearly grows max_hp (modulo flat racial/talent multipliers
    which factor out of the derivative), and eHP is linear in max_hp:
        eHP_phys = max_hp / ((1 − armor_dr)(1 − vers_dr))
        eHP_magic = max_hp / (1 − vers_dr)
    so d eHP / d stam = (d max_hp / d stam) / mitigation_denominator.

    `Character.max_hp()` already applies the multipliers — we approximate
    d max_hp / d stam by running max_hp once and dividing by stamina,
    which is exact for the linear `base = stamina × hp_per_stam` term
    that dominates and for the multiplicative racial/talent corrections
    (they don't depend on stamina, only scale it).
    """
    if char.stamina <= 0:
        c = load_constants()
        dmaxhp_dstam = c["stat_conversion"]["hp_per_stamina"]
    else:
        dmaxhp_dstam = char.max_hp() / char.stamina
    armor_dr = char.armor_dr()
    vers_dr = char.versatility_dr()
    # Always-on passives (DS 15% all-schools + Indomitable 4%) factor into
    # the eHP denominator — matches Character.effective_hp_*() and the
    # mitigation chain in apply_mitigation. F1+F4 introduced this denominator
    # term; this anchor must move with it to stay calibrated.
    always_on = char._always_on_dr()
    dehp_p = dmaxhp_dstam / ((1 - armor_dr) * (1 - vers_dr) * always_on)
    dehp_m = dmaxhp_dstam / ((1 - vers_dr) * always_on)
    return dehp_p, dehp_m


def _closed_form_dehp_per_armor(char: Character) -> float:
    """Exact closed-form ∂eHP_physical/∂armor (magic side is zero)."""
    c = load_constants()
    armor = char.total_armor()
    K = c["armor"]["k_constant"]
    if char.armor_dr() >= c["armor"]["max_armor_dr"]:
        return 0.0
    d_adr_per_armor = K / (armor + K) ** 2
    max_hp = char.max_hp()
    always_on = char._always_on_dr()
    return (
        max_hp
        * d_adr_per_armor
        / ((1 - char.armor_dr()) ** 2 * (1 - char.versatility_dr()) * always_on)
    )


def _closed_form_dehp_per_vers(char: Character) -> tuple[float, float]:
    """Exact closed-form ∂eHP/∂versatility_rating for physical, magic."""
    c = load_constants()
    vr_per_pct = c["stat_conversion"]["versatility_rating_per_pct"]
    d_vdr_per_vr = 1.0 / (vr_per_pct * 100 * 2)  # vers_dr = vers_pct × 0.5
    max_hp = char.max_hp()
    armor_dr = char.armor_dr()
    vers_dr = char.versatility_dr()
    always_on = char._always_on_dr()
    dehp_p = max_hp * d_vdr_per_vr / ((1 - armor_dr) * (1 - vers_dr) ** 2 * always_on)
    dehp_m = max_hp * d_vdr_per_vr / ((1 - vers_dr) ** 2 * always_on)
    return dehp_p, dehp_m


def compute_survivability_marginals(
    character: Character,
    physical_profile: DamageProfile | None = None,
    magic_profile: DamageProfile | None = None,
    healing_profile: HealingProfile | None = None,
    iterations: int = 300,
    seed: int = 42,
    n_workers: int = 0,
    ci_resamples: int = 1000,
    ci_seed: int = 42,
) -> SurvivabilityWeights:
    """Compute per-stat ΔeHP marginals via ETMI-12 perturbation, anchored
    on the closed-form stamina derivative for unit conversion.

    Args:
        character: the character to compute marginals for.
        physical_profile: a DamageProfile projected onto an all-physical
            school. If None, derives one from `m+_pull_melee`.
        magic_profile: a DamageProfile projected onto an all-magic school.
            If None, derives one from `m+_pull_caster`.
        healing_profile: a HealingProfile. If None, loads `m+_high_key_healer`.
        iterations: per-sim iteration count. 300 is the sweet spot on Pi
            between sim noise and one-time character-load cost.
        seed: base seed (each sim uses the same seed for variance reduction
            — same RNG draws across baseline/perturbed runs).
        n_workers: ProcessPoolExecutor worker count. 0 → auto-detect (up
            to 8). 1 → run sequentially.
        ci_resamples: bootstrap resample count for the 95% CI on each
            marginal (see `_bootstrap_marginal_ci`). Costs zero extra sims —
            it resamples the iterations already run — so this only adds a
            fraction of a second even at the default 1000. ``0`` disables
            the CI entirely (``SurvivabilityWeights.ci`` stays ``None``).
        ci_seed: bootstrap RNG seed, independent of ``seed`` (the sim seed)
            so a caller can hold the point estimate fixed while varying the
            bootstrap, or vice versa — mainly for deterministic tests.

    Returns:
        SurvivabilityWeights with .marginals matching `ehp_marginals()`
        shape exactly, .ci carrying the 95% bootstrap CI per marginal (or
        None if disabled/guarded-out), and .meta carrying raw ETMI values +
        closed-form sanity residuals for debugging.
    """
    if physical_profile is None:
        physical_profile = _all_physical(load_damage_profile("m+_pull_melee"))
    else:
        physical_profile = _all_physical(physical_profile)
    if magic_profile is None:
        magic_profile = _all_magic(load_damage_profile("m+_pull_caster"))
    else:
        magic_profile = _all_magic(magic_profile)
    if healing_profile is None:
        healing_profile = load_healing_profile("m+_high_key_healer")

    if n_workers == 0:
        n_workers = min(os.cpu_count() or 1, 8)

    # Build the sim task matrix: (profile_key, "baseline" or stat name).
    # Same seed across baseline/perturbed pairs for common-random-numbers
    # variance reduction (Theck-style — the noise mostly cancels).
    # Drop max_hp_override on the perturbation baseline. Some demo
    # characters carry an in-game-measured `max_hp_override` so the
    # paperdoll matches what the player sees on the character sheet —
    # but it freezes max_hp regardless of stamina, which silently turns
    # the stamina perturbation into a no-op. The anchor depends on
    # stamina moving ETMI, so we recompute max_hp from stamina for the
    # whole stat-weight pass.
    base_char = dc_replace(character, max_hp_override=None)

    task_keys: list[tuple[str, str]] = []
    payloads: list[tuple] = []
    for profile_key, profile in (("p", physical_profile), ("m", magic_profile)):
        task_keys.append((profile_key, "baseline"))
        payloads.append((base_char, profile, healing_profile, iterations, seed))
        for stat in PERTURBED_STATS:
            current = getattr(base_char, stat)
            delta = PERTURB_DELTA[stat]
            perturbed = dc_replace(base_char, **{stat: current + delta})
            task_keys.append((profile_key, stat))
            payloads.append((perturbed, profile, healing_profile, iterations, seed))

    raw: dict[tuple[str, str], tuple[float, list[float], int]] = {}
    if n_workers > 1 and len(payloads) >= 4:
        try:
            with ProcessPoolExecutor(max_workers=n_workers) as pool:
                futures = {
                    pool.submit(_run_one, p): k for p, k in zip(payloads, task_keys, strict=False)
                }
                for fut, key in futures.items():
                    raw[key] = fut.result()
        except Exception:
            raw = {}  # fall through to sequential
    if not raw:
        for key, payload in zip(task_keys, payloads, strict=False):
            raw[key] = _run_one(payload)

    # Closed-form anchors (stamina is THE anchor; armor + vers are sanity
    # checks). Use the override-cleared character so the closed-form
    # measures the same max_hp the sim's stamina perturbation sees —
    # otherwise the K_anchor is computed against an unrelated max_hp and
    # over- or under-scales every other stat by the override ratio.
    cf_stam_p, cf_stam_m = _closed_form_dehp_per_stam(base_char)
    cf_armor_p = _closed_form_dehp_per_armor(base_char)
    cf_vers_p, cf_vers_m = _closed_form_dehp_per_vers(base_char)

    # Raw ETMI deltas (perturbed − baseline). Negative means the stat helped.
    sim_delta_etmi: dict[str, dict[str, float]] = {}
    for profile_key in ("p", "m"):
        baseline_etmi = raw[(profile_key, "baseline")][0]
        for stat in PERTURBED_STATS:
            perturbed_etmi = raw[(profile_key, stat)][0]
            sim_delta_etmi.setdefault(stat, {})[profile_key] = perturbed_etmi - baseline_etmi

    # Per-unit sim weight: -ΔETMI / delta_rating. Positive = stat improves
    # survivability (consistent with eHP-units downstream where bigger is
    # better).
    sim_weights: dict[str, dict[str, float]] = {}
    for stat in PERTURBED_STATS:
        delta = PERTURB_DELTA[stat]
        sim_weights[stat] = {
            "p": -sim_delta_etmi[stat]["p"] / delta,
            "m": -sim_delta_etmi[stat]["m"] / delta,
        }

    # Anchor conversion: K = closed_form_dEHP_per_stam / sim_dETMI_per_stam.
    # Then any stat's eHP-equivalent marginal is sim_weight × K, which equals
    # (sim_weight / sim_stam) × closed_form_dEHP_per_stam.
    sim_stam_p = sim_weights["stamina"]["p"]
    sim_stam_m = sim_weights["stamina"]["m"]
    abs_delta_stam_p = abs(sim_delta_etmi["stamina"]["p"])
    abs_delta_stam_m = abs(sim_delta_etmi["stamina"]["m"])

    # Guard: if the stamina ETMI shift is below the noise floor (extremely
    # unlikely — stam is the most reliable stat — but possible if the
    # damage profile is so easy nobody dies and ETMI is near zero), fall
    # back to the closed-form for everything. Output is still honest, just
    # no better than today.
    if abs_delta_stam_p < _NOISE_FLOOR_DELTA_ETMI or abs_delta_stam_m < _NOISE_FLOOR_DELTA_ETMI:
        from .marginals import ehp_marginals

        return SurvivabilityWeights(
            marginals=ehp_marginals(character),
            meta={
                "anchor_failed": True,
                "reason": "stamina ETMI shift below noise floor",
                "abs_delta_stam_p": abs_delta_stam_p,
                "abs_delta_stam_m": abs_delta_stam_m,
                "noise_floor_delta_etmi": _NOISE_FLOOR_DELTA_ETMI,
            },
        )

    k_phys = cf_stam_p / sim_stam_p
    k_mag = cf_stam_m / sim_stam_m

    marginals: dict[str, dict[str, float]] = {}
    for stat in PERTURBED_STATS:
        sp = sim_weights[stat]["p"]
        sm = sim_weights[stat]["m"]
        # Per-stat noise floor: a stat whose cumulative ETMI delta is
        # below the floor gets zero credit. Distinguishes "engine
        # iteration noise" from "genuinely small contribution."
        p_ehp = sp * k_phys if abs(sim_delta_etmi[stat]["p"]) >= _NOISE_FLOOR_DELTA_ETMI else 0.0
        m_ehp = sm * k_mag if abs(sim_delta_etmi[stat]["m"]) >= _NOISE_FLOOR_DELTA_ETMI else 0.0
        marginals[stat] = {"p": p_ehp, "m": m_ehp}

    # Armor magic-side is always 0 by physics (armor doesn't mitigate magic).
    # Independent of what the noisy sim says. Pin it.
    marginals["armor_from_gear"]["m"] = 0.0

    ci: dict[str, dict[str, tuple[float, float] | None]] | None = None
    ci_meta: dict = {}
    if ci_resamples > 0:
        ci, ci_meta = _bootstrap_marginal_ci(
            raw, cf_stam_p, cf_stam_m, sim_stam_p, sim_stam_m, ci_resamples, ci_seed
        )
        # A marginal the point-estimate noise floor zeroed out shouldn't
        # carry a nonzero-centered CI next to a displayed 0 — same "no
        # claim" honesty as the point estimate itself.
        for stat in PERTURBED_STATS:
            for school in ("p", "m"):
                if marginals[stat][school] == 0.0:
                    ci[stat][school] = None

    # Sanity-check residuals — surfaced in meta, asserted in tests.
    armor_residual_p = (
        (marginals["armor_from_gear"]["p"] - cf_armor_p) / cf_armor_p if cf_armor_p > 1e-6 else 0.0
    )
    vers_residual_p = (
        (marginals["versatility_rating"]["p"] - cf_vers_p) / cf_vers_p if cf_vers_p > 1e-6 else 0.0
    )
    vers_residual_m = (
        (marginals["versatility_rating"]["m"] - cf_vers_m) / cf_vers_m if cf_vers_m > 1e-6 else 0.0
    )

    return SurvivabilityWeights(
        marginals=marginals,
        ci=ci,
        meta={
            "anchor_failed": False,
            "iterations": iterations,
            "seed": seed,
            "k_phys": k_phys,
            "k_mag": k_mag,
            "closed_form": {
                "stam_p": cf_stam_p,
                "stam_m": cf_stam_m,
                "armor_p": cf_armor_p,
                "vers_p": cf_vers_p,
                "vers_m": cf_vers_m,
            },
            "sim_etmi": {key: val[0] for key, val in raw.items()},
            "sim_delta_etmi": sim_delta_etmi,
            "sanity_residuals": {
                "armor_p": armor_residual_p,
                "vers_p": vers_residual_p,
                "vers_m": vers_residual_m,
            },
            "ci_meta": ci_meta,
        },
    )
