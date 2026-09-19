"""Analytical eHP marginals — ∂eHP/∂stat per school (physical, magic).

Pure function of a Character. No simulation, no random draws. Used by the
v0.9 vault ranking and gear-list helpers to compute per-dungeon ΔeHP for an
item swap in microseconds.

Returns a dict keyed by stat name, each value a {"p": dehp_per_unit_phys,
"m": dehp_per_unit_magic} mapping. Stats with zero direct mitigation
contribution (haste, crit, mastery, strength) get zero marginals — they
matter for damage output / Shield Block frequency but not eHP per se.

Moved from ui/app.py during the v0.9 redesign so non-UI callers (CLI,
tests, future API) can use it without pulling Streamlit.
"""

from __future__ import annotations

from dataclasses import replace

from .character import Character
from .constants import load_constants


def ehp_marginals(char: Character) -> dict:
    """Instantaneous ∂eHP / ∂stat — no simulation needed."""
    c = load_constants()
    hp_per_stam = c["stat_conversion"]["hp_per_stamina"]
    armor_dr = char.armor_dr()
    # ∂max_hp/∂stamina including ALL max_hp multipliers (racial, Indomitable, and
    # Guardian Bear Form ×1.40 when stamina_in_caster_form) — derive it from
    # max_hp()/stamina exactly as the sim anchor does
    # (survivability_weights._closed_form_dehp_per_stam) so the closed-form
    # fallback agrees with the sim path instead of under-counting by those mults.
    vers_dr = char.versatility_dr()
    max_hp = char.max_hp()
    dmaxhp_dstam = max_hp / char.stamina if char.stamina > 0 else hp_per_stam
    armor = char.total_armor()
    K = c["armor"]["k_constant"]
    vr_per_pct = c["stat_conversion"]["versatility_rating_per_pct"]
    # Always-on passives (DS + Indomitable) apply uniformly to all schools;
    # they multiply the eHP denominator and so factor into every marginal.
    # See character.py:_always_on_dr for the components.
    always_on = char._always_on_dr()

    dehp_base_p = 1.0 / ((1 - armor_dr) * (1 - vers_dr) * always_on)
    dehp_base_m = 1.0 / ((1 - vers_dr) * always_on)
    d_adr_per_armor = K / (armor + K) ** 2 if armor_dr < 0.85 else 0.0
    dehp_p_per_armor = max_hp * d_adr_per_armor / ((1 - armor_dr) ** 2 * (1 - vers_dr) * always_on)
    d_vdr_per_vr = 1.0 / (vr_per_pct * 100 * 2)
    dehp_p_per_vers = max_hp * d_vdr_per_vr / ((1 - armor_dr) * (1 - vers_dr) ** 2 * always_on)
    dehp_m_per_vers = max_hp * d_vdr_per_vr / ((1 - vers_dr) ** 2 * always_on)

    # Guardian haste survival marginal (Elune's Chosen only): haste → rage →
    # Ironfur stacks → armor → eHP. total_armor() is haste-dependent ONLY when the
    # Ironfur haste model is active (Character._guardian_ironfur_avg_stacks), so the
    # finite difference is exactly 0 — and this block a no-op — for every other
    # spec / build (haste_rating stays {p:0,m:0}, bit-identical). This is the
    # CLOSED-FORM FALLBACK; the live gear UI uses the sim path
    # (optimizer.survivability_weights.compute_survivability_marginals), which
    # captures the same chain automatically once total_armor() depends on haste.
    # dehp_p_per_armor already self-guards armor_dr ≥ 0.85 (→ 0).
    dehp_p_per_haste = 0.0
    if char.class_spec == "guardian_druid":
        armor_hi = replace(char, haste_rating=char.haste_rating + 100).total_armor()
        if armor_hi != armor:
            dehp_p_per_haste = dehp_p_per_armor * (armor_hi - armor) / 100.0

    # Agility survival marginal (Guardian): agility → Ironfur bonus armor
    # (total_armor += agi × coeff × stacks) → armor DR → eHP. Same finite-diff
    # through total_armor() as haste; 0 for any spec whose armor doesn't scale
    # with agility (a1 == a0). NOTE: this CLOSED-FORM captures only the armor
    # path — agility ALSO raises Guardian dodge (base_dodge), which the eHP
    # denominator omits, so it UNDER-states agility. The live gear UI uses the
    # sim path (survivability_weights), which perturbs agility and captures
    # armor + dodge together; this entry is the closed-form fallback.
    dehp_p_per_agility = 0.0
    if char.class_spec == "guardian_druid":
        armor_agi = replace(char, agility=char.agility + 100).total_armor()
        if armor_agi != armor:
            dehp_p_per_agility = dehp_p_per_armor * (armor_agi - armor) / 100.0

    # Guardian mastery (Nature's Guardian) has a real survival value via its
    # healing/absorb-received aura (Character.incoming_healing_multiplier), but
    # that is a HEALING-THROUGHPUT effect, not an eHP-denominator one, so it has
    # no closed form here — mastery_rating stays {p:0, m:0} below. The live gear
    # surface + gem suggester use the SIM path (compute_survivability_marginals),
    # which perturbs mastery_rating and captures it; this closed form is only the
    # fallback (same situation as the agility-dodge component noted above).
    return {
        "stamina": {"p": dmaxhp_dstam * dehp_base_p, "m": dmaxhp_dstam * dehp_base_m},
        "armor_from_gear": {"p": dehp_p_per_armor, "m": 0.0},
        "versatility_rating": {"p": dehp_p_per_vers, "m": dehp_m_per_vers},
        "haste_rating": {"p": dehp_p_per_haste, "m": 0.0},
        "crit_rating": {"p": 0.0, "m": 0.0},
        "mastery_rating": {"p": 0.0, "m": 0.0},
        "strength": {"p": 0.0, "m": 0.0},
        "agility": {"p": dehp_p_per_agility, "m": 0.0},
    }
