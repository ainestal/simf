"""simf UI — L0 string/HTML formatters.

Pure presentation helpers: they take plain values (and at most read
``constants.yaml`` via ``load_constants``) and return strings/HTML fragments.
No Streamlit, no session state. Extracted from ``app.py`` so the formatting
layer has its own import-light home and can be unit-tested in isolation.
"""

from __future__ import annotations

import urllib.parse

from simf.core.character import Character
from simf.core.constants import load_constants
from simf.io.upgrade_track import TrackBadge, classify_upgrade_track


def _item_quality_class(item: object) -> str:
    """CSS class fragment for an item's quality border on the paperdoll.

    ItemSpec carries no quality field (neither the /simc export nor the
    Wowhead-fallback lookup surfaces one), so quality is inferred from the
    configured epic floor — every endgame M+ piece is at least epic — with a
    promotion allowlist for known legendaries. Returns ``""`` for an item
    without an id (e.g. an empty slot) so its icon keeps the neutral frame.

    Data: ``constants.yaml`` → ``gear.quality_border`` (``default`` +
    ``legendary_item_ids``). Keeping the classification in YAML lets a
    legendary be added without a code change.
    """
    item_id = int(getattr(item, "item_id", 0) or 0)
    if not item_id:
        return ""
    cfg = (load_constants().get("gear", {}) or {}).get("quality_border", {}) or {}
    if item_id in set(cfg.get("legendary_item_ids", []) or []):
        return "q-legendary"
    return f"q-{cfg.get('default', 'epic')}"


def _track_badge_html(item: object, display_ilvl: int | None) -> str:
    """Right-aligned upgrade-track badge for a paperdoll cell.

    Renders ``Myth 6/6`` (track-tinted) followed by the ilvl when the item's
    rank bonus_id is recognised, degrading to a bare ``i289`` for crafted /
    unrecognised items and to ``""`` when there's no ilvl either. ``item``
    supplies the track; ``display_ilvl`` is the row's authoritative ilvl
    (the equipped piece's), preferred over the table's reference ilvl.
    """
    badge = classify_upgrade_track(item) if item is not None else TrackBadge(None, None, None, None)
    ilvl = display_ilvl or badge.ilvl
    ilvl_html = f'<span class="slot-row-ilvl">i{ilvl}</span>' if ilvl else ""
    if not badge.has_track:
        return ilvl_html
    head = (
        f"{badge.track} {badge.rank}/{badge.max_rank}"
        if badge.rank and badge.max_rank
        else badge.track
    )
    track_cls = f"track-{badge.track.lower()}"
    return f'<span class="track-badge {track_cls}">{head}</span>{ilvl_html}'


def _wowhead_dungeon_url(name: str) -> str:
    return f"https://www.wowhead.com/search?q={urllib.parse.quote_plus(name)}"


def _school_mix_caption(school_mix: dict[str, float]) -> str:
    parts = sorted(school_mix.items(), key=lambda kv: kv[1], reverse=True)
    return " · ".join(f"{round(p * 100)}% {school}" for school, p in parts if p > 0)


def _filter_qualifier(winner) -> str:
    """' · Best across 2 of 7 prog dungeons' — empty when no filter is active."""
    sel = winner.selected_dungeon_count
    total = winner.total_dungeon_count
    if total <= 0 or sel >= total:
        return ""
    return f" · Best across {sel} of {total} prog dungeons."


def _dps_to_ehp_ratio() -> float:
    """Calibration constant — see constants.yaml `survivability_recommender`."""
    return float(
        (load_constants().get("survivability_recommender") or {}).get("dps_to_ehp_ratio", 1000.0)
    )


def _composite_score(delta_ehp: float, delta_dps: float, surv_pct: float) -> float:
    """Linear blend of ΔeHP and ΔDPS under a single 0-100% survivability dial.

    DPS units (`Σ Δrating × weight / 1000`) are an order of magnitude smaller
    than ΔeHP. The yaml-tunable `dps_to_ehp_ratio` brings them into the same
    band so the slider's extremes feel symmetric."""
    w = max(0.0, min(1.0, surv_pct / 100.0))
    return w * delta_ehp + (1.0 - w) * delta_dps * _dps_to_ehp_ratio()


def _surv_axis_label(pct: int) -> str:
    """Named tier for the slider position so users don't have to translate
    '70%' into a feel. Brutoh's review (2026-05-16): '_DPS_TO_EHP=1000 is
    engineer-brain — give me Pure survival / Balanced / Max DPS instead.'"""
    if pct >= 85:
        return "Pure survival"
    if pct >= 60:
        return "Survival-leaning"
    if pct >= 41:
        return "Balanced"
    if pct >= 16:
        return "DPS-leaning"
    return "Max DPS"


def _humanize_loadout_key(key: str) -> str:
    """`archon-meta` → `Archon meta`. Display label for a share-supplied
    (`?build=`/`?compare=`) loadout key, which has no curated label of its own."""
    return key.replace("-", " ").replace("_", " ").strip().capitalize() or key


_SKILL_ASSUMPTION_CDS_BY_SPEC: dict[str, str] = {
    "protection_warrior": (
        "Shield Block, Demoralizing Shout, Ignore Pain, and the Shield Wall / Last Stand chain"
    ),
    "protection_paladin": ("Shield of the Righteous, Word of Glory, and Ardent Defender"),
    "blood_death_knight": ("Death Strike, Vampiric Blood, and Icebound Fortitude"),
    "vengeance_demon_hunter": ("Demon Spikes, Soul Cleave, and Metamorphosis"),
    # Celestial Brew and Celestial Infusion are mutually-exclusive choice-node
    # talents for the same absorb-shield slot — named generically since simf
    # can't reliably tell which one a given character took (see
    # BrewmasterPolicy.decide()'s comment for the detection blocker).
    "brewmaster_monk": ("Purifying Brew, your absorb-shield cooldown, and Fortifying Brew"),
    "guardian_druid": ("Ironfur, Frenzied Regeneration, Survival Instincts, and Incarnation"),
}


def _skill_assumption_caption_for_spec(class_spec: str) -> str:
    """Build the player-skill caveat that sits beneath the key-level
    verdict headline.

    Spec-aware so a Blood DK doesn't read "Shield Block" and bounce —
    the caveat names the cooldowns *that spec* needs to press for the
    sweep's numbers to hold. Falls back to a spec-agnostic phrasing
    when the spec isn't in the map (mostly a defensive default;
    every modelled tank spec has an entry above).

    Distinct in intent from the "avoidable-mechanic damage isn't
    modeled" caption further down in the panel: that caption is about
    *modelling scope* (what the engine simulates), this one is about
    *player execution* (whether you actually pressed the cooldowns
    the policy assumes you would).
    """
    cd_clause = _SKILL_ASSUMPTION_CDS_BY_SPEC.get(class_spec)
    if not cd_clause:
        cd_clause = "your defensive cooldowns"
    return (
        f"**Assumes proper defensive use.** This sweep presses "
        f"{cd_clause} on cooldown. Real-world death rates are higher "
        "if you miss those presses."
    )


def _verdict_claimed_key(verdict) -> int | None:
    """The key level the verdict is *telling the player they can do*.

    The headline cites `displayable_prog_ceiling()` when present
    ("Holds under stress to +N") and falls back to `comfortable_max`
    when every key in the sweep is safe. That ceiling — what the user
    actually reads — is what the world-ceiling caption gates on, not
    the raw `prog_ceiling` (which can exceed the displayable cap when
    the chain is trimmed).
    """
    shown_prog = verdict.displayable_prog_ceiling()
    if shown_prog is not None:
        return shown_prog
    return verdict.comfortable_max


def _pick_ladder_key(verdict) -> int | None:
    """Choose the key level to render the skill ladder at.

    The naive choice (push_key = `prog_ceiling or comfortable_max`)
    produces a tiny spread for over-geared characters: a player whose
    gear can clear +18 comfortably will see 100/98/96/95 across the
    ladder because at +18 even bad play is survivable. That reads as
    "skill doesn't matter" — exactly the opposite of what the ladder
    is trying to say.

    The fix is to render at the highest key in the sweep where the
    headline death rate is non-trivial — the first key past
    `comfortable_max`. That's where missed presses actually start
    costing the player something. If every key in the sweep is
    comfortable (user is over-geared for the table ceiling), we
    fall back to the top of the sweep so the ladder still renders;
    Brutoh's feedback (2026-05-20): "tiny spread is the actual
    problem."
    """
    if not verdict.points:
        return None
    # First non-comfortable key — where skill bites.
    non_comfortable = [p.key_level for p in verdict.points if p.band != "comfortable"]
    if non_comfortable:
        return min(non_comfortable)
    # Everything comfortable → show ladder at top of sweep; caption
    # will note the spread is tight on purpose.
    return max(p.key_level for p in verdict.points)


def _trinket_scoring_caveat() -> str:
    """Why a trinket's ΔeHP in this dialog won't match the paperdoll chip that
    opened it (user-flagged 2026-06-10). They use different scoring frames, on
    purpose: the paperdoll ranks known trinkets with their proc/use model at
    *as-dropped* item level; this dialog is stats-only at *match-my-gear* ilvl.
    Say so, so the gap reads as a frame difference, not a bug."""
    return (
        "⚠️ Trinkets are compared on **stats only** here — simf doesn't model "
        "on-use abilities or procs yet, so check the candidate's special effect "
        "before you commit. The paperdoll's trinket pick scores known trinkets "
        "with their proc/use model at as-dropped item level, so its ΔeHP won't "
        "match these stats-only, match-my-gear numbers — that's the frame "
        "difference, not a bug."
    )


def _base_secondary_pool(char: Character) -> float:
    """Sum of haste/crit/mastery/versatility ratings on the character.

    Denominator for the ΔDPS fraction in the upgrade panel — same
    approximation the vault scorer uses. Returns 1.0 instead of 0.0 when
    the character has zero secondaries so the caller can divide safely.
    """
    pool = (
        float(getattr(char, "haste_rating", 0) or 0)
        + float(getattr(char, "crit_rating", 0) or 0)
        + float(getattr(char, "mastery_rating", 0) or 0)
        + float(getattr(char, "versatility_rating", 0) or 0)
    )
    return pool if pool > 0 else 1.0


_SPEC_PLURAL_LABEL = {
    "protection_warrior": "Prot Warriors",
    "protection_paladin": "Prot Paladins",
    "blood_death_knight": "Blood Death Knights",
    "vengeance_demon_hunter": "Vengeance Demon Hunters",
    "brewmaster_monk": "Brewmasters",
    "guardian_druid": "Guardians",
}
