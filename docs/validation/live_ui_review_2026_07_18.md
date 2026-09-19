# Live-UI review + fixes (2026-07-18)

User ask, mid-session: *"spawn the agents to actually go to the tool and
check the UI, not the code, the UI... put together anything that might be
failing and address it."*

## What actually happened with the persona agents

A `Workflow` run (novice_tank warmup, engaged_tank, elite_tank, ui-craft-critic,
`lead` triage) reproduced a known, previously-documented failure mode
(`workflow_persona_playwright_tools_missing` memory note): 3 of 4 persona
agents had **zero** Playwright/`mcp__playwright__*` tools available — only a
bare `Read` tool — and correctly refused to fabricate a review, reporting the
blocker instead. Re-tried via direct `Agent` tool spawns (not `Workflow`) on
the theory that the tool grant would carry through more reliably that way —
same result: `novice_tank` and `engaged_tank`, spawned directly, still had 0
Playwright tool calls available.

**Conclusion: this session (a background job) does not have the Playwright
MCP server connected at all** — a session-level environment gap, not a
per-agent-type or per-spawn-method bug. Further attempts via either
mechanism would fail identically; not worth more agent-launches chasing it.

**Only `ui-craft-critic` produced admissible findings**, because it doesn't
depend on the MCP tool grant — it drives `scripts/screenshot.py` via `Bash`
(a real headless-Chromium/Firefox process the script launches itself, not an
MCP tool). Its findings were screenshot-verified and code-cross-checked by
`lead`. Given the MCP gap, the rest of this review continued the same way:
`scripts/screenshot.py` + direct DOM/computed-style probes via a throwaway
Playwright script run through `Bash` (this main session has Playwright
installed as a Python package for the screenshot tooling — the gap is
specifically the *MCP tool* wiring, not Playwright's availability at all).

## Findings (from ui-craft-critic + lead triage), and what was done

Screenshots: `/tmp/simf_shots/{01_landing,02_gear,03_vault,04_gear_200}.png`
(ui-craft-critic's originals) plus this session's own verification shots
under `/tmp/simf_verify/`.

### 1. Header control cluster breaks at 200% zoom — FIXED, but not as first diagnosed

Initial fix attempt: widen the `@media (max-width: 640px)` narrow-viewport
breakpoint (already used for `.st-key-header-nav-row`/`.st-key-run-config-row`)
to 900px, on the theory that CSS `zoom` shrinks the effective layout
viewport. **That theory was wrong** — verified empirically with a direct
Playwright probe against both engines: `document.documentElement.style.zoom`
does **not** change `window.innerWidth`/`clientWidth`/media-query evaluation
in either Firefox or Chromium. (This is actually consistent with
`scripts/screenshot.py`'s own docstring, which claims the narrower thing —
`clientWidth` stays fixed while `scrollWidth` grows — not that zoom
shrinks the viewport; the false claim was this session's own
misreading, now corrected in the `app.py` comment.) Reverted the breakpoint
back to 640px.

**Real root cause** (found via a DOM/computed-style probe): the calibration
chip is a Streamlit popover-trigger `<button>` with default
`white-space: nowrap; overflow: hidden; text-overflow: ellipsis` — but
Streamlit centers button labels by default (`justify-content: center;
text-align: center` at every nested level), and `text-overflow: ellipsis` on
centered nowrap text doesn't resolve to a readable trailing "…" — it clips
symmetrically from both ends with no ellipsis shown at all. At 200% zoom the
label (rendered 2x larger) exceeds the button's fixed width, producing
exactly the reported symptom: "cterized — checked against a few logs — trus"
(the middle slice of "± Characterized — checked against a few logs — trust
less", with both ends silently cut).

**Fix**: `src/simf/ui/css.py::_render_calibration_badge_css()` — left-align
the chip label (`justify-content: flex-start; text-align: left` on the
button and all its nested label wrappers, scoped to
`[class*="st-key-calibration-chip-"]` only) so the existing nowrap+ellipsis
mechanism resolves normally: "± Characterized — checked against a few lo…".
Verified live: before/after screenshots at 200% zoom in
`/tmp/simf_verify/`. The "Change character" button's own mid-word wrap at
extreme zoom is a real but lower-severity cosmetic artifact of the same
"same fixed-px column, 2x-larger text" root cause — left as-is (not worth
the layout risk of restructuring the row for a zoom-only edge case).

Test: `tests/test_calibration_badge.py::test_calibration_chip_label_is_left_aligned_for_every_tier`.

### 2. "No vault upgrade this week" styled as an error — FIXED

Two of the three `verdict-warn` (bronze left-border) call sites in
`vault_panel.py::_render_verdict_card` were genuinely **good news** ("every
offer duplicates gear you already own," "your gear beats every vault
choice") — painting reassurance with the same visual grammar as a real
problem erodes trust on a tool whose core value is honesty. Added a third
verdict state, `.verdict-neutral` (reuses the existing neutral
`--border-default` gray — no new hue), and moved those two call sites to it.
The third `verdict-warn` site ("simf can't read this item's stats yet" — a
genuine parsing failure) correctly stays `verdict-warn`.

Tests: `tests/test_vault_track_aware_app.py::test_all_dead_week_keeps_no_upgrade_headline_with_dead_count`
(updated) + `::test_gear_already_beats_every_vault_offer_uses_neutral_not_warn` (new).

### 3. Gear-tab swap headline visually under-weighted — FIXED

"N slots recommend a swap · total +X% (+Y eHP)" — the single most
decision-relevant number on the Gear tab — rendered as plain markdown bold
(body-text size, ~14px), quieter than the section headers around it.
`src/simf/ui/recommend.py` now renders it through a dedicated
`.gear-swap-headline` CSS class (19px, weight 600) instead of `**bold**`
markdown.

Test: `tests/test_gear_sheet_layout.py::test_swap_recommendation_headline_has_visual_weight`.

### 4. Default browser-blue underlined links — FIXED

`.item-link`/`.talent-link` had a `:focus-visible` ring but no BASE color or
`text-decoration` rule — outside a quality-tinted container (e.g.
`.gear-card-name`, which already overrides to `color: inherit`), these fell
through to the browser default: blue, underlined. Visible on the
talent-build-explain panel's chips and every Vault-card Wowhead item-name
link. Added a base rule in `app.py` tokenizing both classes to
`--accent-gold` (steel), underline on hover only (kept for WCAG 1.4.1 — never
color alone). Verified the `.gear-card-name`/`.slot-row-name` overrides still
win the cascade (same specificity, later source order).

Test: `tests/test_item_talent_link_base_color.py` (new file, 2 tests).

### 5. Inconsistent ΔeHP delta formatting — INVESTIGATED, not a bug

ui-craft-critic's cited example ("≈0% (+0 eHP)" vs "-0.58% (-11,626 eHP)") is
actually the SAME template (`_format_ehp_delta` in `state.py`) applied
correctly — one just happened to land in the documented near-zero-rounds-to-
"≈0%" case. This function's docstring records a 2026-07-04 fix for exactly
this shape of complaint (always show a percentage, never drop the
parenthetical). No change made. The inline `ⓘ` glyphs per stat term (also
flagged as "noise") are the mechanism-transparency feature from the
2026-07-09/10 eHP/eHPS workshop (PRs #325-333) — a deliberate, hard-won trust
feature, not noise; not touched without stronger evidence it's actually
disliked rather than just unfamiliar to one reviewer.

### 6. Two card systems, dark paperdoll as a bolt-on island — NOT CHASED

`lead`'s triage explicitly flagged this as a deliberate, standing design
exception (the dark paperdoll), not a defect — lowest priority, correctly
not actioned here.

## Test/verification summary

- 5 new/updated test files, 2797 passed + 7 skipped (was 2792 before this
  session's VDH work; +5 from VDH, +0 net here since one stale test was
  deleted after its premise was invalidated and replaced by a better one
  elsewhere).
- mypy: 26 pre-existing errors in `src/simf/ui/`, unchanged before/after
  (confirmed via `git stash` diff) — this session introduced zero new ones.
- Every fix mutation-verified (temporarily reverted the code change, watched
  its own new test fail, restored).
- Every fix visually confirmed via real screenshots against the relaunched
  dev instance (`/tmp/simf_verify/`), not just asserted from test-string
  matches.

## What's still open

- The Playwright-MCP-in-background-jobs gap itself — worth a dedicated
  investigation in an interactive session (where the MCP server may well be
  connected) to confirm whether this is background-job-specific, and if so,
  whether it's fixable or just a standing limitation to plan around for
  future live-UI review asks made from a background job.
- Finding #5's `ⓘ` glyphs and delta-format concern: worth a fresh look from
  a live-driving persona (once the MCP gap is resolved) rather than a single
  screenshot-based reviewer's aesthetic read.
