"""Always-visible run-config trust banner for Surface 1.

Pure string builder — Streamlit just renders the output. The strip must be
visible by default (NOT behind a toggle) per elite-tank's hard requirement.
The output is Discord-pasteable so a reader can verify the build that
produced any shared simf result.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from simf.core.character_fields import SIM_AFFECTING_CHARACTER_FIELDS, normalize_sim_field
from simf.core.constants import load_constants

if TYPE_CHECKING:
    from simf.core.character import Character


# The single corpus behind `RunConfig.global_rmse` is Prot Warrior's 16 M+
# logs (see `format_run_config_details`'s docstring) — there is no per-spec
# RMSE variant yet. Any surface that welds the ±X% error onto a per-spec
# confidence signal MUST gate on this: another spec (e.g. Guardian Druid,
# real RMSE ~0.119, a different corpus entirely) would otherwise display
# Warrior's number as if it were its own — a false per-spec precision claim,
# the exact trust bug the calibration surface exists to prevent (advisor
# catch, review round R2, 2026-07-17). Threading each spec's own RMSE is the
# fuller fix; until then, show the number only for this spec. Note this is
# independent of `calibration_tier` — CALIBRATION_CORPUS_SPEC identifies
# WHICH corpus `global_rmse` measures, not whether that spec currently holds
# the `calibrated` tier (protection_warrior itself was downgraded to
# `characterized` 2026-07-18 without this constant changing).
CALIBRATION_CORPUS_SPEC = "protection_warrior"

# WoW patch 12.1.0 "Curse of Ula'tek" ships live 2026-08-11. Every constant in
# constants.yaml was fit/verified against 12.0.5 — the moment 12.1.0 is live,
# every ability-tuning-sensitive number (absorb %, healing %, talent-driven
# damage reduction) is a *pre-patch* number until someone re-verifies it, and
# nothing on the trust strip said so. Gated on BOTH a date AND the label
# itself (not just the date) so this self-clears the moment `last_verified_patch`
# is bumped past 12.0.5 — no second manual step to remember, no risk of the
# caveat surviving its own re-verification. Tied to THIS patch cycle: once it
# stops firing for real, delete both this and `_patch_currency_caveat_active`
# rather than pushing the date forward for the next patch.
_PATCH_1210_SHIP_DATE = date(2026, 8, 11)
_PATCH_1210_PRE_SHIP_LABEL = "12.0.5"


def _patch_currency_caveat_active(constants_version_label: str) -> bool:
    return (
        constants_version_label == _PATCH_1210_PRE_SHIP_LABEL
        and datetime.now(UTC).date() >= _PATCH_1210_SHIP_DATE
    )


@dataclass(frozen=True)
class RunConfig:
    k_active: int
    global_rmse: float | None
    log_count: int
    worst_residual_dungeon: str | None
    worst_residual_pct: float | None  # signed, e.g. -0.125 means -12.5%
    iterations: int
    seed: int
    constants_version: str
    simf_sha: str
    item_db_date: str
    policy_version: str
    dungeons: list[str]
    healer_profile: str
    talent_hash: str
    per_dungeon_residuals: dict[str, float] | None = None  # all log-vs-sim residuals
    repro_hash: str | None = None  # see compute_reproduction_hash; None = no character loaded
    marginal_noise_summary: str | None = None  # see ui.marginals.summarize_marginal_noise
    # Provenance clause for `marginal_noise_summary` — see
    # ui.marginals.marginal_noise_basis_label. Kept as data threaded in from
    # `load.py`'s `_build_run_config` (which already imports `ui.marginals`)
    # rather than an import here, so this module doesn't reach up into the
    # sim-cache layer just to describe a number it didn't compute.
    marginal_noise_basis: str | None = None


# Character fields that change the sim's numeric output. This is a plain
# alias for `core.character_fields.SIM_AFFECTING_CHARACTER_FIELDS` — the SAME
# tuple `ui/marginals.py::_char_marginals_signature` uses for its cache key —
# kept as a distinct name here (rather than every call site importing the
# core constant directly) so this module's own history stays legible: the
# two lists used to be hand-maintained SEPARATELY and drifted twice from the
# same root cause (a field added to `Character` updated one but not the
# other) — PR #275 (`_char_marginals_signature` alone was missing `agility`)
# and a second gap this alias itself used to carry silently (this list had
# `max_hp_override`/`detected_talent_spell_ids`, the marginals signature
# didn't, so two configs differing only in one of those could share a
# reproduction hash even though their cached marginals could legitimately
# differ). `core.character_fields` has zero runtime dependencies beyond
# stdlib (no `Character`, no streamlit), so importing it here doesn't
# reintroduce the import-cycle risk the old TYPE_CHECKING-only comment
# guarded against.
_REPRO_HASH_CHAR_FIELDS = SIM_AFFECTING_CHARACTER_FIELDS


def compute_reproduction_hash(cfg: RunConfig, char: Character | None) -> str | None:
    """Short hash over every input that determines a run's numeric output.

    Two shared results with the same hash used an identical character +
    engine configuration — a copy-pasteable way for a reader to verify "this
    exact input set produced this exact output" instead of taking a shared
    number on faith. Elite-tank ask (Active Triage Queue Batch F, 2026-07-06):
    "run manifest / reproduction hash."

    Deliberately does NOT include ``global_rmse``/``log_count``/residuals —
    those describe how trustworthy the model is, not what input produced
    this run, so a calibration refit shouldn't change a character's hash.

    ``cfg.constants_version`` is the human-readable patch LABEL (e.g.
    ``"12.0.5"``, from ``last_verified_patch`` in constants.yaml) — it does
    NOT bump on a mechanical refit (e.g. an Ardent Defender parameter fix),
    so it alone can't distinguish two runs against different constants.yaml
    revisions. The integer ``constants_version`` (constants.yaml's own
    counter, bumped on every such refit) is hashed alongside it so an
    uncommitted/experimental constants.yaml change always produces a
    different hash even when the patch label is unchanged.

    Returns ``None`` when no character is loaded (nothing to reproduce yet);
    callers should omit the line entirely, same convention as every other
    optional clause in this module.
    """
    if char is None:
        return None
    payload = [
        cfg.k_active,
        cfg.iterations,
        cfg.seed,
        cfg.constants_version,
        int(load_constants().get("constants_version", 0)),
        cfg.simf_sha,
        cfg.policy_version,
        cfg.item_db_date,
        cfg.healer_profile,
        sorted(cfg.dungeons),
        [normalize_sim_field(getattr(char, field)) for field in _REPRO_HASH_CHAR_FIELDS],
    ]
    digest = hashlib.sha1(
        json.dumps(payload, default=str).encode(), usedforsecurity=False
    ).hexdigest()
    return digest[:12]


def format_run_config(c: RunConfig) -> str:
    """Render the run-config strip as a single-line ' · '-separated string.

    Fields included (in fixed order):
        K=<n> · RMSE=<f> (<n> logs[, worst <dungeon> <pct>]) · iter=<n,nnn>
        · seed=<n> · constants <ver> · sha=<short> · itemdb=<YYYY-MM-DD>
        · policy=<ver> · dungeons:{...} · healer:<name> · talents:<hash>

    Optional clauses (worst residual, dungeon set) are omitted when their
    underlying data is missing — no "None", no broken strings.
    """
    parts: list[str] = [f"K={c.k_active}"]

    if c.global_rmse is not None:
        rmse_clause = f"RMSE={c.global_rmse:.3f} ({c.log_count} logs"
        if c.worst_residual_dungeon and c.worst_residual_pct is not None:
            sign = "+" if c.worst_residual_pct > 0 else "-"
            rmse_clause += (
                f", {c.worst_residual_dungeon} {sign}{abs(c.worst_residual_pct) * 100:.1f}%"
            )
        rmse_clause += ")"
        parts.append(rmse_clause)

    parts.append(f"iter={c.iterations:,}")
    parts.append(f"seed={c.seed}")
    parts.append(f"constants {c.constants_version}")
    parts.append(f"sha={c.simf_sha}")
    parts.append(f"itemdb={c.item_db_date}")
    parts.append(f"policy={c.policy_version}")

    if c.dungeons:
        parts.append("dungeons:{" + ", ".join(c.dungeons) + "}")

    parts.append(f"healer:{c.healer_profile}")
    parts.append(f"talents:{c.talent_hash}")

    return " · ".join(parts)


def format_log_surface_no_character_summary() -> str:
    """Compact trust-strip text for the Why-did-I-die surface with no
    character loaded — the one state where NOTHING on screen is
    sim-derived (dual-reviewed 2026-07-26, calibration-scientist +
    copy-microcopy-editor, closing a Season 2 punch-list item).

    The Cooldown Planner is the only sim-consuming panel on this surface
    (``log_cd_plan.py``'s ``render_cd_plan_panel`` -> ``optimize_cooldown_
    plan`` -> ``core.runner.run_simulation``) and it hard-returns before
    calling any of that without a loaded character's stats. Everything
    else rendered here (death recap, HP-trough ledger, per-segment risk,
    defensive coverage) is arithmetic over the log's own recorded events
    — no armor curve, no K, no DR stack. Reusing the Warrior-corpus ±X%
    forward-simulation RMSE here (the old ``format_run_config_summary``,
    now deleted — this function replaces its one remaining caller) claimed
    an error rate for a prediction that never happens on this page — a
    real honesty gap, confirmed independently by both reviewers, not
    defensible as "the corpus legitimately backs it" (that reasoning
    measures prediction drift, and this page makes no prediction). A bare
    "Uncalibrated build" fallback doesn't fit either — it reads as a
    whole-app trust complaint, which isn't what's true in this state.
    """
    return "Read straight from your log — not a prediction"


def format_run_config_details(
    c: RunConfig, *, is_corpus_spec: bool = True, log_surface_no_character: bool = False
) -> str:
    """Multi-line markdown for the trust-banner popover.

    Labels are tank-readable — the banner's purpose is to back up the
    summary's trust claim, not to dump engine internals. Power-user
    fields (seed, policy version, talent hash) still appear but under
    plain-English names.

    ``is_corpus_spec`` gates how the leading ±X% error is FRAMED, not whether
    it shows. ``c.global_rmse`` is Prot Warrior's 16-log number with no
    per-spec variant yet (``CALIBRATION_CORPUS_SPEC``). For any OTHER loaded
    spec — including another spec that happens to sit at the `calibrated`
    tier — presenting it as authoritative would be a false per-spec precision
    claim (advisor catch, review round R2, 2026-07-17), so it's reframed as a
    reference figure from that one corpus, not this build's own error.
    Defaults ``True`` for the corpus spec.

    When ``is_corpus_spec`` is true, whether the lead reads "the number to
    trust" additionally depends on ``CALIBRATION_CORPUS_SPEC``'s OWN live
    ``calibration_tier`` (read fresh from ``load_constants()`` here, not
    threaded in as a parameter — this branch only ever runs when the loaded
    spec IS the corpus spec, so reading that spec's tier is exact). At
    `calibrated` it's the number to trust; at any other tier (e.g. Prot
    Warrior's `characterized`, since the 2026-07-25 cross-player downgrade —
    the fit doesn't yet generalise past the one player it was measured on)
    it reads as a real-but-single-player number instead, so this popover
    never contradicts the confidence chip one row above it.

    ``log_surface_no_character`` (2026-07-26) overrides ALL of the above —
    the Why-did-I-die surface with no character loaded is a THIRD state, not
    a variant of ``is_corpus_spec``: nothing on that page is sim-derived at
    all (see ``format_log_surface_no_character_summary``'s docstring), so
    neither framing of the ±X% clause applies, and the per-dungeon-residual
    table below (also forward-sim-specific) is skipped too. Only the
    generic run-settings lines (K, seed, patch, ...) still render — those
    describe real settings, not an error claim. This used to default
    ``is_corpus_spec=True`` for that state (docstring previously read "...and
    the no-spec log surface, which the Warrior corpus legitimately backs") —
    dual-reviewed (calibration-scientist + copy-microcopy-editor) and found
    to be a real honesty gap: the reasoning measured forward-simulation
    prediction drift, and this page runs no forward simulation.
    """
    lines: list[str] = []

    if log_surface_no_character:
        if c.global_rmse is not None and c.log_count:
            log_word = "log" if c.log_count == 1 else "logs"
            lines.append(
                "**Read straight from your log — not a prediction.** The death "
                "recap, HP-trough ledger, and coaching below are your own recorded "
                "events — exact, not simulated, so there's no model error to "
                f"report here. The ±{c.global_rmse * 100:.1f}% figure on the Gear "
                f"tab is simf's forward-simulation error (measured against "
                f"{c.log_count} Prot Warrior M+ {log_word}) — it applies to a "
                "predicted verdict, not a log replay. Load your character on the "
                "Gear tab for a predicted survivability call."
            )
        else:
            lines.append(
                "**Read straight from your log — not a prediction.** Load your "
                "character on the Gear tab for a predicted survivability call."
            )
    elif c.global_rmse is not None and c.log_count:
        log_word = "log" if c.log_count == 1 else "logs"
        # One descending hierarchy, most-authoritative first (review round R2,
        # 2026-07-17 — F-004, re-confirmed live by novice_tank + engaged_tank).
        # Before this the popover surfaced three independently-true error
        # numbers — the run-average ±X% (only in the header strip, never
        # restated here), a worst-dungeon −X%, and a single-hit ±10pp — with
        # no stated relationship, so a reader had to reconcile them alone and
        # (both tanks) trusted the tool *less* for it. Now the run-average
        # leads, and the worst-dungeon and single-hit figures sit beneath it as
        # finer grains, each related back to it: one story, not three numbers.
        if is_corpus_spec:
            # This is the corpus spec (Prot Warrior) with a character loaded —
            # the ±X% figure is the real, measured error for THIS build's own
            # prediction accuracy. The no-character log surface used to also
            # default through here (is_corpus_spec=True with no real spec) —
            # that's now its own `log_surface_no_character` branch above, not
            # this one.
            #
            # Whether it's "the number to trust" depends on the corpus spec's
            # OWN `calibration_tier`, checked live rather than threaded in as
            # a parameter: this branch only ever runs when the loaded spec IS
            # `CALIBRATION_CORPUS_SPEC`, so reading that spec's tier here is
            # exact, not a guess. Prot Warrior was downgraded `calibrated` ->
            # `characterized` 2026-07-25 (the fit doesn't generalise past the
            # one player it was measured on — see
            # docs/validation/protwarrior_calibrated_downgrade_2026_07_25.md)
            # while `global_rmse` itself stayed excellent — a stale "the
            # number to trust" claim sitting one popover row below a chip
            # that says "trust less" is exactly the contradiction this
            # tier check exists to prevent.
            tier = (
                load_constants()
                .get("specs", {})
                .get(CALIBRATION_CORPUS_SPEC, {})
                .get("calibration_tier", "")
            )
            if tier == "calibrated":
                lines.append(
                    f"**±{c.global_rmse * 100:.1f}% is the number to trust** — the typical "
                    f"run-to-run error (RMSE) across simf's {c.log_count} M+ {log_word}. Lower "
                    "means predictions closer to real combat logs."
                )
            else:
                lines.append(
                    f"**±{c.global_rmse * 100:.1f}%** is simf's typical run-to-run error "
                    f"(RMSE) on the {c.log_count} M+ {log_word} it was fitted to — one "
                    "player's logs. It hasn't been shown to hold for other players yet, "
                    "which is why this spec reads *characterized*: compare gear A vs gear "
                    "B with it, treat absolute numbers as indicative."
                )
        else:
            # Non-corpus spec: the number is real but it's Prot Warrior's, not
            # this build's. Say so plainly and defer to the confidence tier in
            # the chip rather than lending Warrior's precision to another spec.
            lines.append(
                f"**±{c.global_rmse * 100:.1f}% is simf's error on its one calibrated "
                f"corpus** — Prot Warrior, {c.log_count} M+ {log_word} — shown here as a "
                "reference. This spec's own error hasn't been measured against logs yet, "
                "so trust its numbers less (see the confidence tier on the chip above)."
            )
        if c.worst_residual_dungeon and c.worst_residual_pct is not None:
            sign = "+" if c.worst_residual_pct > 0 else "-"
            # Factual comparison only — RMSE does NOT bound an individual
            # dungeon's residual, so we do NOT claim the worst dungeon is
            # "inside the ±X% band" (it would read as a guarantee the math
            # can't make, and the mechanic-curious tank is exactly who'd
            # object). We state that its gap is close to the run-average, not
            # a large outlier. The K=3200 basis is preserved verbatim: these
            # per-dungeon residuals were measured at the empirical error
            # minimum (K=3200), never re-measured per-dungeon at this run's K,
            # and we deliberately do not editorialize an unverified shift
            # (same stance as the "Model error by dungeon" note below).
            lines.append(
                f"- **Worst single dungeon:** {c.worst_residual_dungeon} at "
                f"{sign}{abs(c.worst_residual_pct) * 100:.1f}% — close to the run-average "
                f"above, not a large outlier (measured at K=3200, not this run's K=3430)."
            )
        # Per-event vs per-run honesty, framed as the finest grain of the same
        # ladder. The aggregate RMSE above is per-run; the per-event mitigation
        # gap measured 2026-05-24 against 18 Brutoh logs is ~±10pp on physical
        # events. Don't let a tank read a single-pull prediction as if it's the
        # calibration number. See
        # docs/validation/per_school_gap_policy_tick_2026_05_24.md.
        lines.append(
            "- **Any one hit:** about ±10 percentage points on an individual physical "
            "hit — the finest grain. Treat a single predicted hit as a ballpark, not the "
            "run-average above."
        )
    else:
        lines.append(
            "Calibration data isn't available for this build yet — the fields below are "
            "this run's settings, not a measured error."
        )

    # Skipped entirely on the no-character log surface (2026-07-26) — this
    # table is forward-sim per-dungeon residuals, the same category of
    # inapplicable claim the lead paragraph above already replaced.
    if not log_surface_no_character and c.per_dungeon_residuals:
        # Sorted worst-first by absolute residual so the user sees the
        # least-trustworthy dungeons at the top. The worst entry is wrapped
        # in **bold** to draw the eye without needing color in markdown.
        # Entries with `null` residual (dungeon present in the catalog but
        # no usable calibration replay yet) are filtered out — we don't
        # render "unknown" in the trust strip.
        measured = [(n, r) for n, r in c.per_dungeon_residuals.items() if r is not None]
        # Dungeons with a `null` residual (no usable calibration replay yet)
        # were silently absent from this table with no pointer — a reader
        # here alone had no way to know 2 of their 8 prog dungeons weren't
        # shown at all (round-2 review, 2026-07-05). The Vault-tab warning
        # strip already names them as "unverified"; point to it instead of
        # duplicating that logic here.
        unverified = [n for n, r in c.per_dungeon_residuals.items() if r is None]
        if measured:
            ranked = sorted(measured, key=lambda kv: -abs(kv[1]))
            # These residuals are measured at K=3200 (the empirical RMSE
            # minimum), not the K=3430 this run actually uses (elite_tank
            # round-1 review, 2026-07-05). A round-2 fix labeled that gap
            # with a "shifts ~+5pp at K=3430" note — but that shift is
            # itself an unverified, un-remeasured hand-wave from the
            # constants.yaml comment: applying it flips the ranking (the
            # flagged-worst dungeon would become near-best, and an
            # unflagged one would become the real worst), while the GLOBAL
            # RMSE barely moves between the two Ks (0.064 -> 0.068) — too
            # little to support a uniform +5pp per dungeon. Narrating a
            # re-ranking we can't stand behind is the same disease as the
            # original unlabeled table (round-3 review, 2026-07-05): state
            # the measured basis, don't editorialize a shift we haven't
            # actually re-measured. The ⚠️ below is "worst in this K=3200
            # corpus," not a claim about the run's live K=3430 ranking.
            lines.append(
                "- **Model error by dungeon** (measured at K=3200, the empirical "
                "error minimum — not yet re-measured per-dungeon at this run's K=3430):"
            )
            for idx, (name, residual) in enumerate(ranked):
                sign = "+" if residual > 0 else "-" if residual < 0 else ""
                row = f"  - {name}: {sign}{abs(residual) * 100:.1f}%"
                if idx == 0:
                    row = f"  - {name}: **{sign}{abs(residual) * 100:.1f}%** ⚠️"
                lines.append(row)
            if unverified:
                noun = "dungeon" if len(unverified) == 1 else "dungeons"
                lines.append(
                    f"  - {len(unverified)} {noun} not shown — no calibration replay "
                    f"yet ({', '.join(unverified)}); see the Vault-tab note."
                )

    lines.append(f"- **Armor constant (K):** {c.k_active}")
    # This count is real for vault/gear's own marginals sweep, but reads as
    # a global setting — Talent A/B and build-diff run 500, Key-level
    # verdict runs 200. Nobody's lying locally; this line just overstated
    # uniformity (round-2 review, 2026-07-05).
    lines.append(
        f"- **Vault/gear sim iterations:** {c.iterations:,} (other panels run their own count)"
    )
    lines.append(
        f"- **Reproducibility:** fixed seed ({c.seed}) — the same build always "
        "gives the same numbers"
    )
    lines.append(f"- **Game patch:** {c.constants_version}")
    if _patch_currency_caveat_active(c.constants_version):
        lines.append(
            "- **Since this label was checked:** WoW patch 12.1.0 has shipped, and "
            "simf's constants haven't been re-verified against it yet. Ability "
            "tuning — absorb %, healing %, talent-driven damage reduction — is the "
            "category most likely to have moved. Treat those numbers as provisional "
            "until this patch label updates, and cross-check anything patch-sensitive "
            "against a fresh combat log if it matters for a real decision."
        )
    # Drop the item-db line when there's no cached snapshot — surfacing
    # "Item DB: unknown" was actively confusing ("did the sim use the
    # right stats?"). Live Wowhead / Blizzard lookups are the default
    # path; an absent snapshot is the normal state for most users.
    if c.item_db_date and c.item_db_date != "unknown":
        lines.append(f"- **Item DB snapshot:** {c.item_db_date}")
    lines.append(f"- **Cooldown logic version:** {c.policy_version}")
    if c.dungeons:
        lines.append(f"- **Prog dungeons:** {', '.join(c.dungeons)}")
    lines.append(f"- **Healer profile:** {_humanize_healer_profile(c.healer_profile)}")
    lines.append(f"- **Talents:** {c.talent_hash}")
    if c.repro_hash:
        lines.append(
            f"- **Reproduction hash:** `{c.repro_hash}` — same hash elsewhere means "
            "identical character + engine inputs"
        )
    if c.marginal_noise_summary:
        # The basis clause names the REAL sim this CI came from (a smaller,
        # dedicated compute — see ui.marginals.marginal_noise_basis_label) so
        # it can't be misread against this same popover's "iter=1,000" line
        # a few rows down, which counts a different, larger sim (the vault/
        # gear sweep). Falls back to a bare "95% CI" if the basis wasn't
        # threaded through — still true, just less specific — rather than
        # blocking the line entirely.
        basis = f"95% CI · {c.marginal_noise_basis}" if c.marginal_noise_basis else "95% CI"
        lines.append(
            f"- **Stat-weight noise ({basis}):** {c.marginal_noise_summary} "
            "— Monte Carlo noise only, not model error"
        )

    return "\n".join(lines)


_HEALER_PROFILE_LABELS = {
    "m+_high_key_healer": "High-key M+ healer",
}


def _humanize_healer_profile(raw: str) -> str:
    """A raw snake_case internal id (`m+_high_key_healer`) in a
    tank-readable popover was the last engine-speak leak in this panel
    (copy audit, round-1 review 2026-07-05). An unrecognized future profile
    falls back to the raw id rather than guessing a mangled transform."""
    return _HEALER_PROFILE_LABELS.get(raw, raw)


def _format_commit_age(seconds: float) -> str:
    """Coarse "N ago" bucketing — minutes/hours/days, no external deps.

    A negative delta (clock skew between this box and whatever committed)
    is clamped to 0 rather than printed as a nonsensical "-3m ago".
    """
    seconds = max(seconds, 0.0)
    if seconds < 60:
        return "just now"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m"
    hours = int(seconds // 3600)
    if hours < 24:
        return f"{hours}h"
    days = int(seconds // 86400)
    return f"{days}d"


def format_build_footer(sha: str, commit_iso: str) -> str:
    """Always-visible deploy-provenance caption: ``build <sha> ·
    committed <UTC timestamp> (<age> ago)``.

    This is provenance, not a staleness proof — it says when the running
    build's HEAD commit landed, not whether the live process has actually
    picked it up (a Streamlit process can run stale code after a `git
    pull` until it's restarted). ``_simf_sha``/``_simf_commit_time`` are
    ``st.cache_data`` with no TTL, so "age" is computed against wall-clock
    at whatever moment this first rendered after the process started —
    not necessarily "right now" for a long-lived process. A human reading
    "14h ago" can judge staleness themselves; we don't invent a boolean.

    Degrades gracefully: an ``"unknown"`` sha (e.g. `git` unavailable in a
    tarball install) drops the whole caption to "build unknown"; an
    ``"unknown"`` or unparseable ``commit_iso`` keeps the sha but omits
    the "committed ..." clause entirely rather than guessing a timestamp.
    """
    if not sha or sha == "unknown":
        return "build unknown"
    base = f"build {sha}"
    if not commit_iso or commit_iso == "unknown":
        return base
    try:
        committed = datetime.fromisoformat(commit_iso)
    except ValueError:
        return base
    if committed.tzinfo is None:
        committed = committed.replace(tzinfo=UTC)
    committed_utc = committed.astimezone(UTC)
    now = datetime.now(UTC)
    age = _format_commit_age((now - committed_utc).total_seconds())
    stamp = committed_utc.strftime("%Y-%m-%d %H:%M UTC")
    age_clause = "" if age == "just now" else f" ({age} ago)"
    return f"{base} · committed {stamp}{age_clause}"
