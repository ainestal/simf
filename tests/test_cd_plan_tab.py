"""Regression tests for the Phase 6.1 UI surface — `Plan your cooldowns`.

Covers:
  - `_character_cache_key` is stable + invalidates on stat changes
  - The renderer routes correctly across the three onboarding states
    (no character / unrecognised spec / ready-to-run)
  - The cached optimizer call mirrors the CLI's healing-baseline
    calibration so UI and CLI agree on what's "best"

The full optimizer integration on a real log is exercised by
`test_cooldown_planner_optimizer.py`; this file pins the *UI wiring*
contract — anything that would silently break the tab.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from streamlit.testing.v1 import AppTest

from simf.ui import log_view

# Grounded via Path(__file__), NOT a bare relative string — AppTest.from_file
# resolves a relative path against the calling file's location, which is
# sensitive to import-order/xdist-worker scheduling in ways that only surface
# intermittently (CI run 31128045690, 2026-08-06: FileNotFoundError at
# ".../tests/src/simf/ui/app.py" — an extra "tests/" segment, not reproducible
# in ~10 local full-suite runs the same day). Matches the established pattern
# in tests/test_public_mode.py's APP_PATH.
_APP_PATH = Path(__file__).parent.parent / "src" / "simf" / "ui" / "app.py"


def _brutoh_char_dict() -> dict:
    """Minimal Prot Warrior char_data dict — what `_ss()['char_data']`
    contains after a SimC paste. Matches the Character.from_dict contract."""
    return {
        "name": "Brutoh",
        "server": "uldum",
        "region": "eu",
        "race": "earthen",
        "class_spec": "protection_warrior",
        "talents": "default",
        "stamina": 12000,
        "armor_from_gear": 5500,
        "haste_rating": 1200,
        "crit_rating": 800,
        "mastery_rating": 600,
        "versatility_rating": 1400,
        "max_hp_override": None,
    }


# ─── cache-key invariants ─────────────────────────────────────────────────────


def test_character_cache_key_is_hashable():
    """Streamlit `cache_data` requires every cache-key arg to hash. A
    bare char_dict isn't hashable; we discriminate with this tuple."""
    key = log_view._character_cache_key(_brutoh_char_dict())
    hash(key)  # raises TypeError if any element is unhashable


def test_character_cache_key_invalidates_on_stat_change():
    """A trial swap on the Gear tab mutates char_data['versatility_rating']
    etc. If the cache key didn't reflect that, the optimizer would return
    a stale plan computed against the *baseline* stats."""
    base = _brutoh_char_dict()
    swapped = dict(base)
    swapped["versatility_rating"] = 2000  # simulated trial swap

    assert log_view._character_cache_key(base) != log_view._character_cache_key(swapped)


def test_character_cache_key_ignores_cosmetics():
    """Renaming the character or changing region shouldn't bust the cache —
    those fields don't move survivability math."""
    base = _brutoh_char_dict()
    cosmetic = dict(base)
    cosmetic["name"] = "Other"
    cosmetic["region"] = "us"

    assert log_view._character_cache_key(base) == log_view._character_cache_key(cosmetic)


# ─── healing-baseline calibration parity with CLI ─────────────────────────────


def test_cached_cd_plan_mirrors_cli_healing_baseline():
    """The CLI calibrates `heal.baseline_hps_abs = replay.actual_dealt /
    replay.duration_s * 1.1` (cli.py:cd_plan). Without this, the heuristic
    policy fires CDs against a baseline that doesn't match the log's actual
    healing throughput and the UI's death-rate ranking drifts away from the
    CLI for the same input. Pin the calibration line so a future refactor
    can't quietly drop it."""
    src = inspect.getsource(log_view._cached_cd_plan)
    assert "baseline_hps_abs" in src, (
        "_cached_cd_plan no longer sets baseline_hps_abs — UI will diverge from CLI."
    )
    assert "actual_dealt" in src
    assert "duration_s" in src


# ─── renderer routing ─────────────────────────────────────────────────────────


def test_render_cd_plan_panel_signature_threads_uncalibrated_warning():
    """The renderer must accept `uncalibrated_warning` and surface it —
    Blood DK / VDH / Brewmaster / Guardian are still `calibrated: false`
    and a CD plan on top of an uncalibrated engine is double the trust ask.
    """
    sig = inspect.signature(log_view.render_cd_plan_panel)
    assert "uncalibrated_warning" in sig.parameters, (
        "render_cd_plan_panel dropped the uncalibrated_warning param — "
        "uncalibrated specs will silently get plans without the trust caveat."
    )


def test_render_log_analysis_threads_cd_plan_context():
    """The hop from `render_surface_log` to `render_cd_plan_panel` goes
    through `render_log_analysis(cd_plan_context=...)`. Pin the param so
    a refactor doesn't drop the wiring without a test failing."""
    sig = inspect.signature(log_view.render_log_analysis)
    assert "cd_plan_context" in sig.parameters


def test_render_surface_log_signature_accepts_char_dict():
    """The `render_surface_log` entry point needs the character payload —
    the optimizer can't run without armor/HP/stats. The signature change
    is the contract between app.py and log_view.py for this surface."""
    sig = inspect.signature(log_view.render_surface_log)
    for required in ("char_dict", "healer_profile", "uncalibrated_warning"):
        assert required in sig.parameters, (
            f"render_surface_log dropped `{required}` — CD-plan tab will lose its inputs."
        )


# ─── AppTest smoke: panel renders in the surface ──────────────────────────────


def test_cd_plan_panel_renders_load_character_cta_when_no_char():
    """When the user lands on Why-did-I-die without a loaded character,
    the panel must show the 'load your character first' CTA rather than
    fire the optimizer with empty stats."""
    src = inspect.getsource(log_view.render_cd_plan_panel)
    assert "Load your character first" in src or "load your character" in src.lower()


def test_cd_plan_panel_gated_behind_explicit_button():
    """The optimizer is 20-30s on Pi-class hardware. It must NOT auto-fire
    on every log open — gate behind an explicit st.button. Without this,
    every rerun (a slider tweak, a nav click) burns 30s of compute."""
    src = inspect.getsource(log_view.render_cd_plan_panel)
    assert "Find best plan" in src
    # Defensive: button must come before any cache_data call so the
    # spinner only fires on intent, not on load.
    button_idx = src.index("Find best plan")
    optimize_idx = src.index("_cached_cd_plan(")
    assert button_idx < optimize_idx, (
        "Optimizer fires before the user clicks — burns 30s on every page rerun."
    )


def test_cd_plan_panel_does_not_duplicate_the_global_uncalibrated_caveat():
    """The full per-spec calibration caveat already renders once, page-wide,
    above the Gear/Log tab switch (app.py's `_uncalibrated_spec_warning()`
    banner). Repeating the whole paragraph inside this panel too read as a
    rendering bug, not a deliberate caveat — repetition erodes trust in the
    caveat itself (round-1 multi-agent review, 2026-07-05). The panel still
    surfaces a SHORT local pointer whenever that banner has content; it must
    not re-render the long-form text verbatim a second time."""
    src = inspect.getsource(log_view.render_cd_plan_panel)
    assert "st.warning(uncalibrated_warning)" not in src
    assert "calibration note above" in src


def test_cd_plan_pointer_does_not_assert_calibration_status():
    """`_uncalibrated_spec_warning()` returns non-empty for BOTH a genuinely
    uncalibrated spec AND a calibrated:true spec with a per-spec caveat
    (Guardian) — see test_uncalibrated_spec_warning.py. A round-1 fix
    hardcoded "isn't fully calibrated yet" behind that same non-empty gate,
    which is false for the calibrated case and contradicted the "✓ Guardian
    is calibrated" banner rendered a few inches above it on the same page
    (round-2 review, 2026-07-05 — a fresh instance of the two-surfaces-
    disagree bug this cycle keeps hunting). The pointer must not assert
    calibration status either way — check the rendered caption text
    directly, not the whole source (which discusses the history in
    comments and would false-positive on the same words)."""
    src = inspect.getsource(log_view.render_cd_plan_panel)
    m = re.search(r'st\.caption\("([^"]*calibration note[^"]*)"\)', src)
    assert m, "expected a short calibration-note pointer caption in render_cd_plan_panel"
    rendered = m.group(1)
    assert "isn't fully calibrated" not in rendered
    assert "not calibrated" not in rendered


def test_cd_plan_panel_surfaces_ranking_caveat():
    """The CLI's framing 'RANKING between plans is the trustworthy signal'
    must be on the UI surface. Without it, users see '90% death rate' and
    think the tool is broken — the heuristic policy is intentionally
    pessimistic about CD usage."""
    caption = log_view._cd_plan_caption_for_spec("protection_warrior")
    assert "ranking" in caption.lower(), (
        "CD-plan caption missing the 'ranking, not absolute' caveat — "
        "users will read absolute death-rate numbers as predictions."
    )


# ─── caption is spec-aware (CD-plan suggestion spec correctness) ──────────────


def test_cd_plan_caption_lists_prot_warrior_cds():
    """Prot Warrior sees Shield Wall + Last Stand — the legacy hardcoded set."""
    caption = log_view._cd_plan_caption_for_spec("protection_warrior")
    assert "Shield Wall" in caption
    assert "Last Stand" in caption


def test_cd_plan_caption_lists_guardian_cds_not_warrior_cds():
    """AnonGuardian3 landed 2026-05-23 as the first Guardian profile in tree.
    The caption MUST name Guardian's actual emergency CDs (Incarnation +
    Survival Instincts) instead of inheriting Warrior labels — that was
    the visible bug the smoke test unmasked."""
    caption = log_view._cd_plan_caption_for_spec("guardian_druid")
    assert "Incarnation" in caption
    assert "Survival Instincts" in caption
    assert "Shield Wall" not in caption
    assert "Last Stand" not in caption
    assert "Ardent Defender" not in caption


def test_cd_plan_caption_lists_prot_pal_cd():
    """Prot Paladin only has Ardent Defender registered — single-CD path."""
    caption = log_view._cd_plan_caption_for_spec("protection_paladin")
    assert "Ardent Defender" in caption
    assert "Shield Wall" not in caption
    assert "Last Stand" not in caption


def test_cd_plan_caption_lists_blood_dk_cds():
    caption = log_view._cd_plan_caption_for_spec("blood_death_knight")
    assert "Vampiric Blood" in caption
    assert "Icebound Fortitude" in caption
    assert "Shield Wall" not in caption


def test_cd_plan_caption_lists_vdh_cd():
    caption = log_view._cd_plan_caption_for_spec("vengeance_demon_hunter")
    assert "Metamorphosis" in caption
    assert "Shield Wall" not in caption


def test_cd_plan_caption_lists_brewmaster_cd():
    caption = log_view._cd_plan_caption_for_spec("brewmaster_monk")
    assert "Fortifying Brew" in caption
    assert "Shield Wall" not in caption


def test_cd_plan_caption_falls_back_when_spec_unknown():
    """Empty or unrecognised class_spec must not crash and must still
    read sensibly — the spec-not-modelled CTA renders the caption above
    its info box."""
    caption_empty = log_view._cd_plan_caption_for_spec("")
    caption_alien = log_view._cd_plan_caption_for_spec("not_a_real_spec")
    assert "long cooldowns" in caption_empty
    assert "long cooldowns" in caption_alien
    # No spec-specific ability names should leak into the fallback.
    for label in (
        "Shield Wall",
        "Last Stand",
        "Ardent Defender",
        "Vampiric Blood",
        "Icebound Fortitude",
        "Metamorphosis",
        "Fortifying Brew",
        "Incarnation",
        "Survival Instincts",
    ):
        assert label not in caption_empty
        assert label not in caption_alien


def test_cd_plan_caption_covers_every_registered_spec():
    """Tripwire: if `LONG_CD_BUTTONS` gains a new spec, the per-spec
    caption tests above will silently miss it. This assertion catches
    that drift — every key in the registry must produce a caption that
    names at least one of its registered abilities."""
    from simf.core.cooldown_planner import LONG_CD_BUTTONS

    for class_spec, buttons in LONG_CD_BUTTONS.items():
        caption = log_view._cd_plan_caption_for_spec(class_spec)
        for ability, *_ in buttons:
            pretty = ability.replace("_", " ").title()
            assert pretty in caption, (
                f"CD-plan caption for {class_spec} dropped {pretty} — "
                f"either the helper or the registry drifted."
            )


def test_cd_plan_panel_handles_unknown_spec():
    """Specs without registered long-CD buttons (e.g. a hypothetical
    new tank class) must short-circuit with a calm info message instead
    of crashing the optimizer with `LONG_CD_BUTTONS[spec] -> KeyError`."""
    src = inspect.getsource(log_view.render_cd_plan_panel)
    assert "LONG_CD_BUTTONS" in src
    assert "not in LONG_CD_BUTTONS" in src or "spec not in" in src.lower()


def test_cd_plan_panel_handles_no_presses_branch():
    """The optimizer can legitimately return a winning plan with zero
    presses — `naive (CD-on-CD)` may beat search but the empty `no_plan`
    baseline may also win (heuristic-only is optimal). Pin a branch for
    that so we don't print an empty 'Press timeline' header."""
    src = inspect.getsource(log_view.render_cd_plan_panel)
    assert "best.plan.presses" in src
    # Player-subject framing post-polish (ui-critic 2026-05-17 #4) — the
    # empty-plan branch must NOT say "Heuristic CD usage is already best"
    # (engine-speak). It should subject the player.
    assert (
        "pressing your CDs" in src
        or "playing reactively" in src
        or "couldn't find a plan" in src.lower()
    )


# ─── post-polish: hierarchy / regime / noise floor / placement ────────────────


def test_cd_plan_uses_h3_prescription_card_not_h2_verdict():
    """v0.9 redesign principle: ONE h2 verdict per surface (the page's own
    "Why did I die?" title — see `log_surface.py`). The CD plan is a
    *prescription* derivative of the death verdict above — it must render
    with the lighter `_cd_prescription_card` (h3 + 2px accent), not the
    full `_verdict_card` (h3 + 3px accent, sharing the page's one h2 rather
    than adding a second) which the death verdict already owns.
    ui-critic 2026-05-17 #1."""
    src = inspect.getsource(log_view.render_cd_plan_panel)
    assert "_cd_prescription_card" in src, (
        "CD panel must render via _cd_prescription_card (h3) — using "
        "_verdict_card duplicates the death verdict's own card and "
        "breaks page hierarchy."
    )
    assert "_verdict_card(" not in src, (
        "CD panel called _verdict_card directly — that duplicates the "
        "death verdict's card and competes with it above."
    )


def test_cd_prescription_card_renders_h3_not_h2():
    """The card helper writes raw HTML; pin that it's an h3 (the death
    verdict card is also h3 today — see `log_formatters._verdict_card` —
    both sit under the page's one h2 title). A future refactor could
    silently upgrade it back to h2 — this test catches that."""
    src = inspect.getsource(log_view._cd_prescription_card)
    assert "<h3>" in src and "</h3>" in src, (
        "Prescription card must render an h3, not h2 — verdict hierarchy."
    )
    assert "<h2>" not in src, "Prescription card leaked an h2."


def test_death_rate_stderr_pp_matches_binomial_formula():
    """Verify the stderr helper actually computes √(p(1-p)/N) × 100.
    Wrong formula → users see wrong noise floor → trust a sub-noise
    delta as real signal. raid-lead 2026-05-17."""
    # p = 0.5, n = 50 → stderr = √(0.25/50) × 100 ≈ 7.07pp
    assert abs(log_view._death_rate_stderr_pp(0.5, 50) - 7.0710678) < 1e-4
    # p = 0.1, n = 200 → stderr = √(0.09/200) × 100 ≈ 2.121pp
    assert abs(log_view._death_rate_stderr_pp(0.1, 200) - 2.1213203) < 1e-4
    # p = 1.0 → degenerate, stderr 0
    assert log_view._death_rate_stderr_pp(1.0, 50) == 0.0
    # n = 0 → guard against div-by-zero
    assert log_view._death_rate_stderr_pp(0.5, 0) == 0.0


def test_panel_surfaces_noise_floor_inline():
    """The variance band must appear in the verdict copy (e.g.
    "±X.Xpp noise"), not just be available in some hidden expander.
    raid-lead 2026-05-17 — a 1pp delta at 50 iter is below the
    noise floor and the user has to know."""
    src = inspect.getsource(log_view.render_cd_plan_panel)
    assert "_death_rate_stderr_pp(" in src, "Variance computation missing."
    assert "noise" in src.lower(), (
        "Noise floor not surfaced in verdict copy — users will read "
        "sub-noise deltas as real signal."
    )


def test_panel_suppresses_absolute_percent_in_pessimistic_regime():
    """When the heuristic baseline death rate >= 50%, the absolute
    number is more misleading than informative — brutoh hit 100% on a
    key he died ONCE in. The pessimistic-regime branch must replace
    the percent with a delta-only headline + a "pessimistic regime"
    badge. Pin the threshold and the badge so the trust fix can't
    silently regress. brutoh 2026-05-17 #1."""
    src = inspect.getsource(log_view.render_cd_plan_panel)
    # Threshold constant must exist and be ≤ 0.50 (not so loose that the
    # badge never fires on real outputs).
    assert log_view._PESSIMISTIC_REGIME_THRESHOLD <= 0.50
    assert log_view._PESSIMISTIC_REGIME_THRESHOLD >= 0.20  # not so tight every run trips
    # The branch must surface a regime tag.
    assert "pessimistic regime" in src.lower(), (
        "Pessimistic-regime badge missing — 100% death-rate numbers will "
        "burn user trust the way brutoh's first walk showed."
    )


def test_panel_caveats_pessimistic_death_rate_against_the_healer_cap():
    """Healer-cap review finding (2026-07-07): a realistically finite healer
    combined with a pre-existing mitigation-model DTPS over-prediction can
    drain the budget on a long fight and read as near-certain death on a
    real, timed clear. The pessimistic-regime branch must caveat this
    explicitly rather than let a 100% death-rate number read as confirmed
    danger — don't silently ship a number that can be a modeling artifact."""
    src = inspect.getsource(log_view.render_cd_plan_panel)
    assert "modeling gap" in src.lower(), (
        "Pessimistic-regime death rate must caveat the known healer-cap / "
        "DTPS-over-prediction interaction on long fights, not present a "
        "very high number as unqualified fact."
    )


def test_panel_settings_render_below_verdict_not_above():
    """First-visit reading order must be: caption → button → verdict →
    details. Settings ABOVE the CTA are a power-user leak on a verdict
    surface. ui-critic 2026-05-17 #3.

    Pin source order: the `Find best plan` button must appear in the
    source before the `Tune search depth` expander."""
    src = inspect.getsource(log_view.render_cd_plan_panel)
    button_idx = src.index("Find best plan")
    settings_idx = src.index("Tune search depth")
    assert button_idx < settings_idx, (
        "Settings expander rendered before the primary CTA — first-visit "
        "users hit engine knobs before they hit the button."
    )


def test_panel_suppresses_press_timeline_when_search_didnt_improve():
    """When the brute-force search did NOT beat the reactive heuristic,
    rendering the 'best' candidate's press timeline below the
    'keep playing reactively' verdict reads as engine-confusion — a 10-row
    Shield Wall / Last Stand list next to 'didn't beat reactive play'
    contradicts itself.

    Pin the suppression so the timeline ONLY renders when the search
    actually found an improvement. The full candidates table behind the
    expander still has the data for power users."""
    src = inspect.getsource(log_view.render_cd_plan_panel)
    assert "_suppress_press_timeline" in src, (
        "Press timeline must be conditionally suppressed when the search "
        "didn't beat the reactive heuristic — otherwise the verdict and "
        "the timeline contradict each other."
    )
    # The render guard must consult the flag.
    assert "not _suppress_press_timeline" in src


def test_panel_voice_is_player_subject_not_algorithm_subject():
    """ui-critic 2026-05-17 #4: rewrite all verdict variants to subject
    the player ('You're already...', 'Search couldn't find a plan that
    beats reactive play') instead of the algorithm ('Heuristic CD usage
    is already best'). Engine-speak is a voice leak."""
    src = inspect.getsource(log_view.render_cd_plan_panel)
    # Forbid the previous engine-speak headlines (check only inside
    # quoted user-facing strings, not comments — `# "..."` comments
    # may reference the old copy as a reason-for-fix).
    quoted_blocks = [
        line for line in src.splitlines() if '"' in line and not line.lstrip().startswith("#")
    ]
    joined = "\n".join(quoted_blocks)
    assert "Heuristic CD usage is already best" not in joined, (
        "Engine-speak headline still in a user-facing string."
    )
    # At least one player-subject variant must exist.
    player_phrases = ("pressing your CDs", "you're already", "reactive play")
    assert any(p.lower() in src.lower() for p in player_phrases), (
        "No player-subject verdict copy found — voice drifted to algorithm-subject."
    )


# ─── full-app smoke: the page still loads with the new panel wired ────────────


def test_analyze_is_sticky_across_reruns():
    """The log surface used to gate the entire analysis on a one-shot
    `if not st.button("Analyze"): return`. That meant ANY later button
    click (Find best plan, Also aggregate) would rerun the script, the
    Analyze button would return False, and the whole analysis would
    vanish — including the CD-plan panel that hadn't run yet.

    Pin the session-state-backed sticky flag so a future refactor can't
    quietly reintroduce the one-shot gate. The local-log flow lives in
    ``_render_local_log_flow`` (the WCL-URL tab is a sibling); inspect
    that helper's source so the sticky-gate pin survives the tab refactor.
    """
    src = inspect.getsource(log_view._render_local_log_flow)
    # Sticky flag must be set on click and consulted on every rerun.
    assert "session_state" in src, "Analyze gate is not session-state-backed."
    # The fragile pattern would be `if not st.button("Analyze"): return`
    # with NO session-state guard. Forbid it.
    bad = 'if not st.button("Analyze"'
    if bad in src:
        # Allowed only when paired with a session-state check.
        idx = src.index(bad)
        window = src[idx : idx + 400]
        assert "session_state" in window, (
            "Analyze button is gated only by st.button — clicking any later "
            "button will collapse the analysis surface."
        )


def test_app_loads_log_surface_without_crash():
    """The CD-plan wiring touches both app.py and log_view.py.
    Smoke-test that the surface renders without raising — a stray
    bad-attribute reference would surface here, not in unit tests."""
    at = AppTest.from_file(str(_APP_PATH), default_timeout=60)
    at.run()
    # Click "Why did I die?" to land on the log surface.
    matching = [b for b in at.button if b.label == "Why did I die?"]
    assert matching, "Could not find the 'Why did I die?' nav button"
    matching[0].click()
    at.run()
    # Page rendered without exception — any markdown/caption rendered is fine.
    assert not at.exception, f"Log surface raised: {[str(e) for e in at.exception]}"
