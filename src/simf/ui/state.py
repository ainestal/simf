"""simf UI — L1 shared session-state contract.

The single home for the Streamlit session-state accessors, the equipped/trial
gear views, the item-DB lookups, the eHP baseline cache, the public/read-only
mode gates, and the trial-swap mutators. Every gear-aware surface in ``app.py``
consumes these so the session-state shape is defined in exactly one place.

Layer L1: may import from ``models`` (L0) but never from ``app``. ``st.*`` calls
live inside function bodies only — importing this module is side-effect-free.
"""

from __future__ import annotations

import contextlib
import html
import re
import subprocess
import threading
from datetime import UTC, date, datetime
from pathlib import Path

import streamlit as st
import yaml

from simf.core.character import Character
from simf.core.constants import DATA_DIR, load_constants, spec_is_calibrated
from simf.ui.helpers.trial_swap import TrialState, apply_trials
from simf.ui.models import _SlotPick

try:
    from simf.io import item_db as _ITEM_DB
except ImportError:
    _ITEM_DB = None  # requests not installed in this env


# ─── session-state accessors ──────────────────────────────────────────────────


def _ss():
    return st.session_state


def _has_character() -> bool:
    return bool(_ss().get("char_data"))


def _baseline_equipped() -> dict:
    """The gear set parsed from SimC — without any trial swaps overlaid."""
    return _ss().get("simc_equipped") or {}


def _trial_state() -> TrialState:
    return TrialState(
        swaps=_ss().get("_trial_swaps") or {},
        enchant_overrides=_ss().get("_trial_enchants") or {},
        gem_overrides=_ss().get("_trial_gems") or {},
    )


def _equipped() -> dict:
    """The view every gear-aware surface should consume: baseline + trials."""
    return apply_trials(_baseline_equipped(), _trial_state())


# Gear-driven stat fields copied from resolved item stats onto `char_data` at
# load time (see `ui/helpers/simc_load.py`'s `load_from_simc` and
# `ui/load.py`'s `_do_raider_io_load`) — the same fields `Character` consumes
# to derive max_hp/armor/versatility. Kept in sync with that copy-list.
_GEAR_DELTA_FIELDS = (
    "strength",
    "agility",
    "stamina",
    "armor_from_gear",
    "haste_rating",
    "crit_rating",
    "mastery_rating",
    "versatility_rating",
    "shield_armor",
    "parry_rating",
)


def _char_data_effective() -> dict:
    """`char_data` with any active trial swap's stat impact folded in — the
    view every gear-aware surface (verdict, skill ladder, reproduction hash,
    trust-strip) should build its `Character` from, not the raw session key.

    A trial swap only ever mutates `_trial_swaps`, never `char_data` itself
    (so "Reset" can restore the exact original with no snapshot bookkeeping).
    That means a plain `_ss()["char_data"]` read goes stale the moment a trial
    is active: the verdict panel, skill ladder, and reproduction hash all
    derive from `Character.from_dict(char_data)`, so they silently kept
    scoring the PRE-trial gear.

    Fixed here as a DELTA, not an absolute recompute: re-resolving the whole
    equipped set via the item-stat resolver would drift away from an exact
    SimC-export baseline (`gear_haste_rating=`-style lines, resolver-free) by
    however much the resolver's own estimate differs from the export's exact
    total — a regression for the common case. Instead this resolves ONLY the
    baseline vs. trial-merged equipped sets and adds the DIFFERENCE on top of
    whatever `char_data` already holds (exact or estimated), so an inactive
    trial is byte-identical to today's behaviour and an active one is exact
    to the extent the resolver can size the swapped item(s).

    Known gap, not silently claimed as exact: this does not re-run the gem/
    enchant correction pass (`io/resolver_estimated_stats.py`) for a trial's
    swapped item, so a swap onto/off of a socketed or enchanted item under-
    counts that gem/enchant's contribution. Narrower than the pre-existing
    "gem/enchant this app doesn't recognize" caveat, not a new failure mode.
    """
    base = _ss().get("char_data") or {}
    if not base or _ITEM_DB is None:
        return base
    trial = _trial_state()
    if not trial.is_active:
        return base
    class_spec = base.get("class_spec", "")
    baseline_stats = _resolve_equipped_stats(_baseline_equipped(), class_spec=class_spec)
    trial_stats = _resolve_equipped_stats(_equipped(), class_spec=class_spec)
    out = dict(base)
    for field_name in _GEAR_DELTA_FIELDS:
        delta = trial_stats.get(field_name, 0) - baseline_stats.get(field_name, 0)
        if delta:
            out[field_name] = int(base.get(field_name, 0)) + delta
    return out


def _vault() -> list:
    """Flatten the per-slot vault dict into a single list[ItemSpec]."""
    vault_map = _ss().get("simc_vault_items") or {}
    flat = []
    for _slot, items in vault_map.items():
        flat.extend(items)
    return flat


def _selected_dungeons() -> list[dict]:
    """User's prog-dungeon selection. Defaults to all of them with equal weight."""
    selected_ids = _ss().get("selected_dungeon_ids")
    catalog = _dungeon_catalog()
    if not selected_ids:
        return catalog
    by_id = {d["id"]: d for d in catalog}
    return [by_id[i] for i in selected_ids if i in by_id]


# ─── cached pure data loaders ─────────────────────────────────────────────────


@st.cache_data
def _dungeon_catalog() -> list[dict]:
    with open(DATA_DIR / "dungeons.yaml") as f:
        return yaml.safe_load(f)["dungeons"]


# Season 2's M+ rotation goes live 2026-08-18 — a separate, later date than
# patch 12.1.0's own ~2026-08-11 ship (see CONTRIBUTING.md's Season 2 section).
# `season_2_catalog:` entries in dungeons.yaml are pre-staged inert (no
# school_mix/recommended_profile — scripts/promote_season2_catalog.py
# refuses to promote them as-is) until real Season 2 logs let someone run
# the promotion for real. Gated on BOTH the date AND the file's own state
# (not just the date) so this self-clears the moment promotion actually
# happens — no second manual step to remember, mirroring
# `run_config.py`'s `_patch_currency_caveat_active`. Not cached (unlike
# `_dungeon_catalog` above): caching would freeze whichever answer this
# gave on its first call, across the very date boundary it exists to catch.
_SEASON_2_START_DATE = date(2026, 8, 18)


def _season_pool_is_stale() -> bool:
    if datetime.now(UTC).date() < _SEASON_2_START_DATE:
        return False
    text = (DATA_DIR / "dungeons.yaml").read_text()
    # A bare substring check would false-positive on the file's own header
    # prose, which already NAMES `season_1_catalog:` in a sentence describing
    # the future promotion step — anchor to it as an actual top-level key
    # (start of line, nothing else on the line), same convention
    # `promote_season2_catalog.py`'s own `_TOP_LEVEL_KEY_RE` uses.
    return re.search(r"^season_1_catalog:\s*$", text, re.MULTILINE) is None


@st.cache_data
def _loadouts_for_spec(class_spec: str) -> list[str]:
    loadouts = load_constants().get("talent_loadouts", {})
    return [k for k, v in loadouts.items() if v.get("spec") == class_spec] or list(loadouts)


@st.cache_data
def _simf_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607 — git via PATH, read-only local-repo query for build footer
            cwd=Path(__file__).resolve().parent.parent.parent.parent,
            text=True,
        ).strip()
    except Exception:
        return "unknown"


@st.cache_data
def _simf_commit_time() -> str:
    """ISO-8601 commit timestamp of HEAD (``%cI`` — committer date, with
    offset). Sibling to ``_simf_sha()``, same cache/fallback shape. Feeds
    the always-visible build-footer caption (``format_build_footer``)."""
    try:
        return subprocess.check_output(
            ["git", "log", "-1", "--format=%cI", "HEAD"],  # noqa: S607 — git via PATH, read-only local-repo query for build footer
            cwd=Path(__file__).resolve().parent.parent.parent.parent,
            text=True,
        ).strip()
    except Exception:
        return "unknown"


@st.cache_data
def _item_db_date() -> str:
    """Best-effort: stat-snapshot timestamp of the item DB. Falls back to 'unknown'."""
    try:
        path = Path.home() / ".simf" / "item_db_snapshot.yaml"
        if path.exists():
            import datetime as dt

            return dt.date.fromtimestamp(path.stat().st_mtime).isoformat()
    except Exception:  # noqa: S110 — best-effort fallback
        pass
    return "unknown"


# ─── item-DB lookups ──────────────────────────────────────────────────────────


def _stats_for_item(spec) -> dict[str, int] | None:
    if _ITEM_DB is None or not getattr(spec, "item_id", 0):
        return None
    # CRITICAL: cache key MUST include bonus_ids — two ItemSpecs with the
    # same item_id but different bonus_ids resolve to different ilvls and
    # therefore different stat magnitudes on Wowhead. Before this key
    # widened (2026-05-24), the Brutoh-284 profile rendered the bag's
    # Bifurcation Band (ilvl 266) using cached stats from the equipped
    # Bifurcation Band (ilvl 289) — the bag entry looked ~33% inflated
    # AND its ΔeHP came out implausibly positive against a higher-ilvl
    # equipped piece. Same bug bit the slot-dialog ΔDPS path. The
    # underlying ``item_db.fetch_item_stats_wowhead`` disk cache already
    # keys on bonus_ids; this session-state cache just has to follow suit.
    # crafted_stats must ALSO be in the key (2026-07-11): two crafted
    # variants of the same base item+bonus (different chosen secondary
    # pair) relabel to different stats — sharing one cache entry would
    # silently serve one variant's stats to the other.
    #
    # class_spec must ALSO be in the key (2026-07-11): fetch_item_stats_for_spec
    # returns the hybrid agi/str primary label already RESOLVED per class_spec
    # (unlike the disk cache, which stores the unresolved placeholder and
    # re-resolves on every read). Without class_spec here, loading a Guardian
    # then switching to Brutoh (warrior) in the same session would silently
    # keep serving a tri-stat item's agility-resolved stats to the warrior.
    bonus = tuple(sorted(getattr(spec, "bonus_ids", None) or []))
    crafted = tuple(sorted(getattr(spec, "crafted_stats", None) or []))
    class_spec = (_ss().get("char_data") or {}).get("class_spec", "")
    cache_key = (int(spec.item_id), bonus, crafted, class_spec)
    cache = _ss().setdefault("_item_stats_cache", {})
    if cache_key in cache:
        return cache[cache_key]
    try:
        stats = _ITEM_DB.fetch_item_stats_for_spec(spec, class_spec=class_spec)
    except Exception:
        stats = None
    # Cache only successes — a transient Wowhead blip or freshly-configured
    # Blizzard key should resolve on the next render.
    if stats:
        cache[cache_key] = stats
    return stats


def _is_two_handed_item(spec) -> bool | None:
    """Whether ``spec`` is a two-handed weapon, per Wowhead's equip-slot data.
    ``None`` means unknown (fetch failed / offline) — callers should treat
    that as "don't filter," same fail-open convention as ``_stats_for_item``."""
    if _ITEM_DB is None or not getattr(spec, "item_id", 0):
        return None
    bonus = tuple(sorted(getattr(spec, "bonus_ids", None) or []))
    cache_key = (int(spec.item_id), bonus)
    cache = _ss().setdefault("_item_two_hand_cache", {})
    if cache_key in cache:
        return cache[cache_key]
    try:
        is_two_hand = _ITEM_DB.fetch_item_is_two_handed_wowhead(spec.item_id, spec.bonus_ids)
    except Exception:
        is_two_hand = None
    if is_two_hand is not None:
        cache[cache_key] = is_two_hand
    return is_two_hand


def _icon_for_item(spec) -> str | None:
    if _ITEM_DB is None or not getattr(spec, "item_id", 0):
        return None
    cache = _ss().setdefault("_item_icon_cache", {})
    if spec.item_id in cache:
        return cache[spec.item_id]
    try:
        icon = _ITEM_DB.fetch_item_icon_wowhead(spec.item_id)
    except Exception:
        icon = None
    if icon:
        cache[spec.item_id] = icon
    return icon


def _resolve_equipped_stats(items: dict, class_spec: str = "") -> dict[str, int]:
    if _ITEM_DB is None:
        return {}
    return _ITEM_DB.resolve_equipped_stats(items, class_spec=class_spec)


# ─── status announcements ─────────────────────────────────────────────────────


def _announce_status(message: str, *, level: str = "error") -> None:
    """Show a load status message that assistive tech actually announces.

    Streamlit's `st.error`/`st.warning` render a bare `<div
    data-testid="stAlert">` with no `role`/`aria-live` (verified against the
    bundled frontend), so a failed load painted into the top `status_slot` —
    while keyboard focus stays on the Fetch/Load button lower in the form —
    leaves a screen-reader user with no audible *and* no positional cue. Pair
    the visual alert with a visually-hidden live region carrying the same text
    so it's spoken regardless of where focus sits (WCAG 4.1.3 Status
    Messages). Errors are assertive; non-fatal warnings are polite.
    """
    role, live = ("status", "polite") if level == "warning" else ("alert", "assertive")
    (st.warning if level == "warning" else st.error)(message)
    st.markdown(
        f'<div class="visually-hidden" role="{role}" aria-live="{live}">'
        f"{html.escape(message)}</div>",
        unsafe_allow_html=True,
    )


# ─── single-sim lock ──────────────────────────────────────────────────────────

_SIM_LOCK = threading.Lock()


@contextlib.contextmanager
def _sim_slot():
    """Yield True if this caller got the single sim slot, False if another sim
    is already running (non-blocking)."""
    acquired = _SIM_LOCK.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if acquired:
            _SIM_LOCK.release()


# ─── slider + realm state ─────────────────────────────────────────────────────


def _surv_weight() -> int:
    return int(_ss().get("surv_weight", 70))


def _reset_rio_realm() -> None:
    """Clear the realm selection when the region changes.

    A realm from the old region (e.g. EU "Uldum") isn't in the new region's
    options, and a Streamlit selectbox raises if its session value isn't an
    option. Fires as the region selectbox's `on_change` — before the realm
    widget re-renders — so the stale value never reaches it. Also the correct
    UX: your realm changes when your region does.
    """
    st.session_state.pop("_rio_realm", None)


# ─── public / read-only mode ──────────────────────────────────────────────────


def _is_public_mode() -> bool:
    """`SIMF_PUBLIC=1` runs simf as a locked-down PUBLIC read-only instance
    (the Cloudflare-Tunnel-to-the-Pi deploy, 2026-06-13). In this mode:

      * everyone is read-only (no `?ro=0` escape — mutations stay off);
      * the "Why did I die?" log-upload surface is hidden (the dominant
        OOM/abuse vector — a stranger can't upload a 100 MB log);
      * online gear lookup IS exposed, but forced to the zero-auth Raider.IO
        source and rate-limited (see `_do_raider_io_load` + `gear_lookup_guard`)
        so the owner's Blizzard creds are never spent and the SSRF-shaped armory
        host is never reached. Blizzard Armory stays owner-only.

    A visitor starts with a name lookup, a `/simc` paste (cheap, client-
    provided), the demo, or a `?build=`/`?compare=` cold-load. Read once from
    the env (fixed at process start)."""
    import os

    return os.environ.get("SIMF_PUBLIC", "").strip().lower() in {"1", "true", "yes", "on"}


def _is_read_only() -> bool:
    """`?ro=1` cold-share flag (and always-on in `SIMF_PUBLIC` mode). When set,
    every mutation surface (Try buttons, Trial-all, slot-dialog Try) is disabled
    — a stranger viewing a shared URL shouldn't accidentally trial-swap the
    host's gear and corrupt the conversation."""
    return _is_public_mode() or bool(_ss().get("read_only", False))


def _set_view(view: str) -> None:
    """Used as `on_click=` on the nav buttons. Streamlit fires the callback
    *before* the script reruns, so the next render sees the new view and
    computes ``type=primary`` for the correct button on the same pass. A
    bare ``st.rerun()`` here would trigger Streamlit's widget-state
    cleanup and nuke `advanced_mode` and other keyed-widget bindings.
    """
    st.session_state["view"] = view


# ─── eHP baseline ─────────────────────────────────────────────────────────────


def _baseline_ehp() -> float:
    """Mean of physical & magic eHP at the equipped baseline. Used as the
    denominator when framing ΔeHP as a percentage so users have a feel for
    magnitude — engaged-tank reviewer flagged 'bare +820 eHP is meaningless
    on a 1.8M-HP tank'. Falls back to 1.0 to avoid divide-by-zero on the
    paint before `_refresh_baseline_ehp` runs."""
    return float(_ss().get("_baseline_ehp") or 1.0)


def _char_baseline_ehp(char: Character) -> float:
    """Mean of physical & magic eHP — the canonical baseline-eHP definition,
    pure in ``char`` (no session state). ``_refresh_baseline_ehp`` caches this for
    the ΔeHP % framing; ``_per_slot_picks`` uses it for the meaningful-upgrade
    gate, so both speak the same denominator."""
    return (char.effective_hp_physical() + char.effective_hp_magic()) / 2


def _refresh_baseline_ehp(char: Character) -> None:
    _ss()["_baseline_ehp"] = _char_baseline_ehp(char)


def _meaningful_upgrade_ehp_threshold(char: Character) -> float:
    """Absolute ΔeHP a candidate must clear to be surfaced as an actual
    recommendation anywhere on the Gear tab — items, gems, and enchants
    alike. `meaningful_upgrade_ehp_pct` (constants.yaml) is a FRACTION of
    baseline eHP, not an absolute number, because a fixed eHP floor means
    nothing across the range of gear a tank can carry.

    Before 2026-07-05 the item-swap paperdoll used this percentage gate
    (0.5% of baseline) but gems and enchants each had their own hardcoded
    ``_OPTIMAL_EPSILON_EHP = 1.0`` — effectively zero, so a gem/enchant
    "upgrade" could be recommended on a delta orders of magnitude below the
    tool's own stated model error (a usability review caught this: cards
    recommending +0.13%/+0.22% swaps under a "±6.8% model error" banner).
    calibration-scientist's read: keep the gate flat rather than
    dungeon-aware (per-dungeon residual is common-mode across a paired
    comparison and mostly cancels; it's the wrong error term for a delta
    gate) and route every recommendation channel through the same number
    so "is this worth doing" means the same thing everywhere on the tab."""
    meaningful_pct = float(
        (load_constants().get("survivability_recommender") or {}).get(
            "meaningful_upgrade_ehp_pct", 0.0
        )
    )
    return meaningful_pct * _char_baseline_ehp(char)


# No geared level-90 tank has an effective-HP pool below this — even a fresh
# 90 in quest greens clears it comfortably (~hundreds of k eHP). A baseline
# UNDER it means the character's gear stats never resolved (stamina backfilled
# to 0 because the item DB was unreachable), so any eHP %, recommendation, or
# verdict computed from it is garbage. Used both to gate the gear/verdict
# surfaces (`_stats_unresolved`) and to defensively suppress a nonsense ΔeHP %.
_MIN_SANE_BASELINE_EHP = 100_000


def _stats_unresolved(char: Character, equipped: dict) -> bool:
    """True when a character has equipped gear but its survivability stats
    didn't resolve — the gear's per-item stats came back empty (stamina
    backfilled to 0) because the item DB couldn't be reached. This is the
    online name-lookup path on the public instance, where item-stat fetches are
    forced offline (`item_db._is_offline()` under `SIMF_PUBLIC`). The resulting
    near-zero eHP makes every recommendation / verdict / ΔeHP% meaningless, so
    the surface must show an honest banner instead of fake numbers."""
    if not equipped:
        return False
    # The structural "did the gear stats resolve?" test is defined once, on
    # Character (is_degraded) — don't re-derive it per surface, so a future
    # import path that drops a required field is caught everywhere at once. The
    # eHP-floor check stays here: it's baseline-relative and only meaningful
    # once we know gear is present.
    if char.is_degraded():
        return True
    return _char_baseline_ehp(char) < _MIN_SANE_BASELINE_EHP


def _gear_stats_estimated() -> bool:
    """True when the currently-loaded character's stats came from the
    ``/simc``-paste item-lookup RESOLVER fallback (``SimcLoadOk.stats_estimated``
    — see ``ui/helpers/simc_load.py``) rather than the export's own exact
    ``gear_*_rating=`` lines, a demo YAML, or a log/WCL hydrate.

    Distinct from ``_stats_unresolved`` above: that one catches a hard
    failure (stats came back all-zero, numbers are garbage). This is the
    softer, much more common case — every stat resolved to a real,
    plausible-looking number, but it's a per-item Wowhead-lookup ESTIMATE
    (base character stats approximated, gems/enchants/tertiaries not
    resolved, and any one equipped item that under-resolves — e.g. a
    proc-based trinket whose tooltip doesn't expose secondary stats the
    same way a stat-stick does — silently understates the character's true
    total). That estimation error was previously surfaced only as a
    one-time ``st.toast`` at load (``_surface_load_summary_toast``), which
    is long gone by the time a reader is looking at a swap card's ΔeHP —
    exactly the surface this flag now gates a persistent caption on.

    Set by the loader in ``ui/load.py`` (paste, demo, and online-lookup
    paths each set this explicitly — the demo YAML and online lookup are
    NOT estimated in this sense) and read by the Gear surface. Defaults
    ``False`` so a session with no character loaded yet, or a load path
    that hasn't been updated, never claims a false caveat."""
    return bool(_ss().get("_gear_stats_estimated", False))


def _format_ehp_delta(delta: float, *, threshold_pct: float = 0.10) -> str:
    """Render ΔeHP, percentage-first. Three independent usability reviews
    converged on the same complaint (2026-07-04): the raw eHP number (tens
    of thousands against a multi-million pool) reads as unparseable noise
    next to a verdict, while the percentage is the number a player actually
    judges significance by. The old version also DROPPED the parenthetical
    below `threshold_pct` — correct as noise-suppression in isolation, but
    when it fired on one card in a 3-up grid (e.g. a trinket's -0.06%) next
    to two cards that kept theirs, the missing parenthetical read as a bug,
    not a feature. Now every delta always carries a percentage (rounding to
    "≈0%" instead of vanishing), and the raw eHP moves to a muted
    parenthetical for players who want the absolute number."""
    baseline = _baseline_ehp()
    sign = "+" if delta >= 0 else ""
    ehp_str = f"{sign}{delta:,.0f} eHP"
    # An implausible baseline (gear stats didn't resolve → near-zero eHP) would
    # print a nonsense % like "+195%" off a ~21k denominator. Never show the %
    # in that regime — the surface should already be suppressed upstream, but
    # this guard keeps the number honest if a degraded ΔeHP ever reaches here.
    if baseline < _MIN_SANE_BASELINE_EHP:
        return ehp_str
    pct = delta / baseline * 100 if baseline > 0 else 0.0
    pct_sign = "+" if pct >= 0 else ""
    pct_str = f"{pct_sign}{pct:.2f}%" if abs(pct) >= threshold_pct else "≈0%"
    return f"{pct_str} ({ehp_str})"


# ─── residual / calibration warnings ──────────────────────────────────────────

_HIGH_RESIDUAL_THRESHOLD_PCT = 10.0  # |per-dungeon residual| above this surfaces a warn


def _high_residual_warning() -> str:
    """`⚠️ Algaz -6.0% — verdict may understate it.` — empty string when no
    selected prog dungeon exceeds the residual threshold and none are
    unverified.

    engaged_tank Round 2 (2026-05-16): "the verdict claims 'wins all 7
    prog dungeons' but Algeth'ar has -16.8% RMSE buried in the popover.
    If Algeth'ar is in my key tonight I'd want that asterisk on the
    verdict, not three clicks away."

    Reads `constants.yaml calibration.per_dungeon` — the SAME source the
    Calibration-details popover uses (`load.py:_build_run_config`) — not
    `dungeons.yaml`'s `calibration_gap_pct`. Round-1 multi-agent review
    (2026-07-05) caught those two disagreeing on every dungeon: dungeons.yaml
    held the abandoned K=2700 self-fit table (e.g. Algeth'ar -12.2%, Skyreach
    -11.3%) while constants.yaml holds the post-recalibration K-anchored
    numbers (Algeth'ar -6.0%, Skyreach unverified) the popover already
    trusted. Two surfaces stating the same fact two different ways is the
    exact class of bug that produced the earlier item-eHP vault/gear
    contradiction — `calibration_gap_pct` is now dead in dungeons.yaml.
    """
    dungeons = _selected_dungeons()
    per_dungeon = (load_constants().get("calibration") or {}).get("per_dungeon") or {}
    bad: list[str] = []
    unverified: list[str] = []
    for d in dungeons:
        label = d.get("abbrev", d["id"])
        gap = per_dungeon.get(d["id"])
        if gap is None:
            unverified.append(label)
            continue
        gap_pct = gap * 100
        if abs(gap_pct) > _HIGH_RESIDUAL_THRESHOLD_PCT:
            sign = "+" if gap_pct > 0 else ""
            bad.append(f"{label} {sign}{gap_pct:.1f}%")

    clauses: list[str] = []
    if bad:
        noun = "this dungeon" if len(bad) == 1 else "these dungeons"
        clauses.append(
            f"⚠️ {', '.join(bad)} — known model error above 10%; the verdict may miscall {noun}."
        )
    if unverified:
        pronoun = "its" if len(unverified) == 1 else "their"
        clauses.append(
            f"ⓘ {', '.join(unverified)} — no calibration replay yet; treat "
            f"{pronoun} verdict as model-only, unverified."
        )
    if not clauses:
        return ""
    return (
        " ".join(clauses)
        + " Open the calibration chip at the top of the page for the full per-dungeon table."
    )


def _uncalibrated_spec_warning() -> str:
    """Return the warning message when the active character's spec isn't
    at the `calibrated` tier in constants.yaml. Empty string when the
    spec IS calibrated or no character is loaded.

    engaged_tank Phase 4 review (2026-05-16): docstrings promised this
    surface but it didn't exist. Phase 4 added 4 new specs all marked
    `calibrated: false` — users could load a Blood DK / VDH / Brewmaster
    / Guardian and get a confident verdict with zero indication that
    the math was un-anchored.

    Tiered 2026-07-06 (Top-5 #4 retrospective): a spec with real
    characterization work (multiple logs replayed, deltas published in
    docs/validation/, just short of the calibrated parity bar) reads very
    differently from a spec nobody has ever run a real log through — the
    old `calibrated: true/false` boolean collapsed both to the same
    "isn't calibrated yet" sentence. `characterized` gets its own,
    less alarming copy; `placeholder` keeps the original wording.
    """
    cd = _ss().get("char_data")
    if not cd:
        return ""
    spec = cd.get("class_spec", "")
    if not spec:
        return ""
    c = load_constants()
    spec_cfg = c.get("specs", {}).get(spec, {})
    # `{rmse_pct}` lets a caveat entry (e.g. protection_warrior's) quote the
    # live global RMSE instead of a number typed into this dict — the exact
    # way that string went stale (2026-07-22 "+1.8%, RMSE 0.073, 16/16"
    # survived three later corpus re-measurements). `.format()` is a no-op
    # for any entry without the placeholder (e.g. guardian_druid's).
    rmse = (c.get("calibration") or {}).get("global_rmse")
    rmse_pct = f"{rmse * 100:.1f}" if rmse is not None else "?"
    caveat = _SPEC_MODELING_CAVEAT.get(spec, "").format(rmse_pct=rmse_pct)
    if spec_is_calibrated(spec_cfg):
        # Calibrated: no "uncalibrated" warning, but STILL surface any per-spec
        # modeling caveat (decoupled from the flag, 2026-06-29). The flag means
        # "trust the absolute numbers"; the caveat names which levers are tighter
        # than others, so "calibrated" never reads as "every lever equal".
        return caveat
    label = spec.replace("_", " ").title()
    tier = spec_cfg.get("calibration_tier", "placeholder")
    if tier == "characterized":
        base = (
            f"**{label}** is *characterized*, not *calibrated* — real M+ logs "
            f"have been replayed and the gap is measured and written up (see "
            f"`docs/validation/`), but it doesn't yet clear this project's "
            f"`calibrated` bar. Gear A vs gear B comparisons within this spec "
            f"are still useful; absolute death rates and cross-spec numbers "
            f"will shift as the remaining gap closes."
        )
    else:
        base = (
            f"**{label}** isn't calibrated against real logs yet. "
            f"Gear A vs gear B comparisons within this spec are still useful — but "
            f"absolute death rates and cross-spec numbers will shift once we land "
            f"two or more M+ logs for the spec."
        )
    return f"{base} {caveat}" if caveat else base


# Per-spec modeling caveats, rendered REGARDLESS of the calibrated flag (decoupled
# 2026-06-29). For a calibrated spec they surface alone; for an uncalibrated spec
# they append to the "not calibrated yet" warning. They name the levers that are
# tighter/looser than others so "calibrated" never implies every term is equal.
_SPEC_MODELING_CAVEAT: dict[str, str] = {
    # Guardian flipped to calibrated:true 2026-06-29 (unbiased across AnonGuardian1's 16
    # logs, mean +0.2%, 12/16 within ±15%) — real, named caveats even then.
    # A leave-one-out cross-validation run (scripts/calibrate_spec_from_logs.py)
    # first landed just under the promotion bar on 2026-07-07, then again on the
    # F-consistent subset on 2026-07-08 (9/13 = 69%, need ≥75%, not explained by
    # known outlier runs — see docs/validation/phase4_guardian_loo_cv_f_consistent_2026_07_08.md).
    # Downgraded to `characterized` 2026-07-17 (human-ratified) — the underlying
    # model is unchanged, it simply doesn't clear the cross-validation bar this
    # project holds itself to. This entry now supplements the `characterized`
    # base text from `_uncalibrated_spec_warning` rather than a `calibrated` one.
    "guardian_druid": (
        "The **haste→Ironfur** scaling is from one Elune's-Chosen build "
        "(un-regressed — a Druid-of-the-Claw or different-haste build will "
        "refine it), the **mastery→healing** conversion is medium-confidence, and "
        "a small **magic residual** remains (it slightly over-predicts "
        "pulls where most incoming damage is magic rather than physical). "
        "A held-out cross-validation "
        "run confirmed the shortfall isn't explained by known outlier runs — "
        "downgraded from *calibrated* to *characterized* 2026-07-17 pending a "
        "fresh cross-validation pass that clears the bar."
    ),
    # Prot Warrior's tier history (see CONTRIBUTING.md's Calibration section for the
    # full chain): calibrated -> characterized 2026-07-18 (Demo Shout/Phalanx
    # double-count fix) -> RE-PROMOTED calibrated 2026-07-22 once the Shield
    # Block fixes + Vanguard's strength->armor passive + a passing LOO-CV run
    # closed the same-player gap -> DOWNGRADED to characterized again
    # 2026-07-25, the FIRST time the `calibrated` promotion bar was ever
    # checked against anyone other than Brutoh: 15 independent Prot Warrior
    # players (via WCL) miss the identical bar by ~+11% mean bias / 67%
    # within ±15% — this is a real per-player generalization gap, cause
    # still unknown, not a regression in Brutoh's own fit (which stayed
    # excellent throughout). `global_rmse` itself is UNCHANGED by the
    # downgrade — this entry must never hardcode it (that's exactly how the
    # 2026-07-22 wording went stale within 3 days); see `{rmse_pct}` handling
    # in `_uncalibrated_spec_warning`. See
    # docs/validation/protwarrior_calibrated_downgrade_2026_07_25.md and
    # protwarrior_cross_player_validation_gate_2026_07_25.md.
    "protection_warrior": (
        "Fits **Brutoh's own 16-log corpus** closely — ±{rmse_pct}% typical "
        "run-to-run error, and it passes a leave-one-out cross-validation "
        "check on that corpus. It does **not** yet generalise: 15 "
        "independent Prot Warriors miss the same bar by about 11%, so simf "
        "holds this spec at *characterized* until a wider corpus clears "
        "that cross-player check. Practically: trust gear A vs gear B "
        "comparisons; treat absolute death rates as indicative, not exact."
    ),
}


# ─── trial-swap mutators ──────────────────────────────────────────────────────


def _apply_trial_swap(slot: str, item: object) -> None:
    _ss()["_trial_swaps"] = dict(_trial_state().with_swap(slot, item).swaps)
    st.rerun()


def _apply_trial_enchant(slot: str, enchant_id: int) -> None:
    next_state = _trial_state().with_enchant_override(slot, enchant_id)
    _ss()["_trial_enchants"] = dict(next_state.enchant_overrides)
    st.rerun()


def _apply_trial_gem(slot: str, index: int, gem_id: int) -> None:
    """Trial one socket's gem. `TrialState.gem_overrides[slot]` holds the
    FULL gem_ids list for the item (apply_trials replaces the whole list on
    the item), so this reads the item's current (baseline + already-trialed)
    gem_ids from `_equipped()` and only overwrites the one socket index —
    a second socket's existing pick or prior trial survives untouched."""
    item = _equipped().get(slot)
    current_ids = list(getattr(item, "gem_ids", None) or [])
    while len(current_ids) <= index:
        current_ids.append(0)
    current_ids[index] = gem_id
    next_state = _trial_state().with_gem_override(slot, current_ids)
    _ss()["_trial_gems"] = dict(next_state.gem_overrides)
    st.rerun()


def _reset_trial_swaps() -> None:
    _ss().pop("_trial_swaps", None)
    _ss().pop("_trial_enchants", None)
    _ss().pop("_trial_gems", None)
    st.rerun()


def _revert_trial_slot(slot: str) -> None:
    next_state = _trial_state().without_slot(slot)
    if next_state.is_active:
        _ss()["_trial_swaps"] = dict(next_state.swaps)
        _ss()["_trial_enchants"] = dict(next_state.enchant_overrides)
        _ss()["_trial_gems"] = dict(next_state.gem_overrides)
    else:
        _ss().pop("_trial_swaps", None)
        _ss().pop("_trial_enchants", None)
        _ss().pop("_trial_gems", None)
    st.rerun()


def _apply_trial_all(picks: dict[str, _SlotPick]) -> None:
    """Trial every recommended swap as a single transaction. Pre-existing
    trial swaps are preserved (a user might have stacked manual picks before
    hitting Trial-all).

    Runs as an `on_click` callback — Streamlit reruns the script
    automatically when the callback completes, so calling `st.rerun()`
    here yields a benign "no-op" warning visible above the H1.
    Mutating session_state is enough.
    """
    state = _trial_state()
    for pick in picks.values():
        if pick.is_swap and pick.item is not None:
            state = state.with_swap(pick.slot, pick.item)
    _ss()["_trial_swaps"] = dict(state.swaps)
