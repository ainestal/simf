"""simf — v0.9 Streamlit UI.

Two surfaces:
  1. Gear (this file's main flow)        — vault verdict + gear list
  2. Why did I die? (delegates to log_view) — unchanged behaviour

Plus an Advanced-mode toggle for stat-weights, manual entry, etc.

The run-config strip is always visible above the page — trust banner, not
advanced knob. Per elite-tank's hard requirement.

This file is thin orchestration: every non-trivial computation lives in a
pure helper under `optimizer/` or `ui/helpers/` so it has its own tests.
"""

from __future__ import annotations

import streamlit as st

from simf.core.character import Character
from simf.ui.helpers.danger_pull_cheatsheet import render_danger_pull_cheatsheet
from simf.ui.helpers.privacy_footer import render_privacy_footer
from simf.ui.helpers.run_config import format_build_footer
from simf.ui.helpers.seo_head import inject_seo_head_tags
from simf.ui.log_view import render_surface_log

# ─── page config + CSS ────────────────────────────────────────────────────────

# The full title also drives document.title on every rerun via Streamlit's
# own page-config mechanism (reliable, no override race) — it's what a
# JS-executing crawler's rendered-DOM read and the browser tab both see.
# Non-JS crawlers (link-unfurlers) never run this JS at all and still see
# the raw "Streamlit" title baked into Streamlit's vendored index.html —
# see seo_head.py's docstring.
st.set_page_config(
    page_title="simf — Free WoW Mythic+ Tank Survivability Simulator",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="collapsed",
)
inject_seo_head_tags()

st.markdown(
    """
<style>
  :root {
    /* Codex palette — steel + bronze on warm paper.
       Token names kept from the v0.9 dark palette so every selector
       below still maps; only the hex values move. Contrast measured
       against --surface-base (the #f4f1ea page bg). The ratios were
       overstated before the 2026-06-14 a11y pass — --text-muted actually
       read 3.7:1 (NOT the claimed 6.3:1), failing WCAG 2.2 AA for normal
       text; it's darkened below. Real ratios now:
         ink    / paper  14.98 : 1  (body, headings)        — text-primary
         ink-72 / paper   6.29 : 1  (lede, supporting body) — text-secondary
         ink-70 / paper   5.89 : 1  (meta, captions, AA ok) — text-muted
         steel  / paper   6.97 : 1  (accent)
         bronze / paper   7.35 : 1  (warn)
       --text-dim (2.77:1) / --text-faint (2.14:1) are BELOW AA by design:
       decorative sub-meta + disabled/placeholder only (the latter two are
       WCAG-exempt) — never for essential text. Guarded by
       tests/test_palette_contrast.py. */
    --text-primary:   #1a1d22;                 /* ink — body + headings */
    --text-secondary: rgba(26, 29, 34, 0.72);  /* lede, supporting body */
    --text-muted:     rgba(26, 29, 34, 0.70);  /* meta, captions — AA on paper */
    --text-dim:       rgba(26, 29, 34, 0.45);  /* decorative sub-meta only */
    --text-faint:     rgba(26, 29, 34, 0.35);  /* placeholder / disabled */
    --surface-base:   #f4f1ea;                 /* warm paper page bg */
    --surface-elev:   #ffffff;                 /* card / verdict / banners */
    --border-subtle:  rgba(26, 29, 34, 0.14);  /* hairlines */
    --border-default: rgba(26, 29, 34, 0.28);  /* stronger rules, icon frames */
    --accent-gold:    #34556e;                 /* steel — survival/answer accent */
    --accent-good:    #34556e;                 /* steel — eHP up, mitigation */
    --accent-warn:    #7a3d2c;                 /* bronze — death, eHP down, damage */
    --q-epic:         #a335ee;                 /* WoW epic-purple item border (4.3:1 on cream — WCAG 1.4.11) */
    --q-legendary:    #d35f00;                 /* legendary-orange item border — darkened from Blizzard #ff8000 (2.2:1) to clear 3:1 on cream (3.4:1, WCAG 1.4.11) */
    /* Quality NAME text — the WoW signal "everyone knows". Darkened from the
       border hues above to clear WCAG 1.4.3 (4.5:1) on cream: border colors
       only needed 3:1, item-name TEXT needs 4.5:1. */
    --q-epic-text:      #9b30e0;               /* epic-purple name (4.8:1 on cream) */
    --q-legendary-text: #9a4a00;               /* legendary-orange name (5.5:1 on cream) */
    /* ── Shape + rhythm scale (2026-06-20 cohesion pass) ───────────────────
       Before this, radii were scattered across five raw values (3/4/6/8/14px)
       and section gaps across four (2/4/12/16px), so every card, banner, chip
       and input rounded its corners a slightly different amount — the quiet
       "smashed together" tax. Collapse each ROLE to one token:
         card    — verdict cards, banners, the dark gear panel, vault cells
         control — buttons, inputs, selectboxes, the upgrade panel rows
         chip    — badges, tags, regime/cap pills
       Raw values below stay only where a surface genuinely needs a distinct
       radius (the 52px gear-card icons, the 14px outer dark frame keeps its
       own larger curve as a deliberate "framed window" cue). */
    --radius-card:    10px;   /* cards, banners, dark panel inner cards, vault cells */
    --radius-control: 6px;    /* buttons, inputs, selectboxes */
    --radius-chip:    4px;    /* badges, tags, small pills */
    /* One shared content column. The page used Streamlit's full-wide block
       container (~1280px on a desktop) for ALL chrome EXCEPT the dark gear
       panel, which capped itself at 1040px + centred — making the panel the
       only centred element and reading as a detached island. Pin the whole
       column to the SAME width so top chrome, recommendation, paperdoll,
       browse control and the upgrade panel all share one left/right edge. */
    --content-max:    1040px;
  }

  /* Codex is light-only by design — see `ui-revamp/phase-2/SPEC.md` §1:
     "Print-typography calm. Source Serif 4 on warm paper." The 5-token
     palette above is the entire vocabulary. A previous `@media
     (prefers-color-scheme: dark)` branch was added then reverted on
     2026-05-25 — the OS-driven trigger landed dark mode on users who
     didn't ask for it, and the resulting cascade of Streamlit-widget
     overrides was the tell that dark wasn't a re-skin, it was a
     different design language entirely (per ui-craft-critic + visual-
     system-auditor audits). If dark mode is reintroduced, it needs its
     own SPEC, its own co-designed palette, and an OPT-IN trigger
     (session-state toggle or query param), not a media query. */

  /* Page background — Streamlit's main container reads its own bg from
     the theme, so paint the app root explicitly to land the cream. */
  .stApp, [data-testid="stAppViewContainer"] {
    background: var(--surface-base) !important;
    color: var(--text-primary) !important;
  }

  /* ── One content column (2026-06-20 cohesion pass) ─────────────────────────
     Cap the main block container to --content-max so the WHOLE page shares the
     dark gear panel's width and centre line. Before this the dark panel was the
     lone centred element on an otherwise full-wide page; now the panel's
     `max-width: 1040px; margin: 0 auto` becomes a no-op (it already equals the
     column) and the island reads as part of the column, not a separate sheet.
     Streamlit re-keys the block-container testid across minor versions, so
     target the stable role + both known testids. */
  [data-testid="stMainBlockContainer"],
  [data-testid="stAppViewBlockContainer"],
  .stMainBlockContainer,
  section.main > div.block-container,
  .block-container {
    max-width: var(--content-max) !important;
    /* Centre the capped column — `max-width` alone leaves it left-aligned in
       the wide viewport with a lopsided right margin. */
    margin-left: auto !important;
    margin-right: auto !important;
    padding-top: 3rem !important;
  }

  /* ── Input parity (2026-06-20 cohesion pass) ───────────────────────────────
     Text inputs + textareas rendered as near-invisible flat cream boxes (they
     inherited the page bg with no border) while selectboxes on the SAME row had
     a crisp white fill + border — two inputs, two design systems. Give the
     BaseWeb input + textarea wrappers the SAME white fill + subtle border +
     control radius the selectbox already gets from the theme, so every field on
     a row reads as one control family. Scoped to the BaseWeb input containers so
     it can't leak onto buttons or other widgets. */
  .stTextInput div[data-baseweb="input"],
  .stTextArea div[data-baseweb="textarea"],
  .stNumberInput div[data-baseweb="input"] {
    background: var(--surface-elev) !important;
    border: 1px solid var(--border-default) !important;
    border-radius: var(--radius-control) !important;
  }
  .stTextInput div[data-baseweb="input"]:focus-within,
  .stTextArea div[data-baseweb="textarea"]:focus-within,
  .stNumberInput div[data-baseweb="input"]:focus-within {
    border-color: var(--accent-gold) !important;
    box-shadow: 0 0 0 1px var(--accent-gold) !important;
  }
  /* BaseWeb paints its own inner border on the raw <input>/<textarea>; null it
     so we don't get a double hairline inside the wrapper above. */
  .stTextInput div[data-baseweb="input"] input,
  .stTextArea div[data-baseweb="textarea"] textarea,
  .stNumberInput div[data-baseweb="input"] input {
    background: transparent !important;
    border: none !important;
  }

  /* ── Control radius parity ──────────────────────────────────────────────────
     Buttons + selectboxes to the shared control radius so every interactive
     surface rounds the same amount as the inputs above. */
  .stButton button,
  .stDownloadButton button,
  div[data-baseweb="select"] > div {
    border-radius: var(--radius-control) !important;
  }

  /* ── Button family (2026-06-20 cohesion pass) ──────────────────────────────
     One vocabulary: a SINGLE solid-steel primary per surface, bordered-cream
     for everything else, with real hover/active/focus-visible states. The hue
     (#34556e steel = --accent-gold = config primaryColor) is correct; before
     this the buttons had radius only and no states, so the solid fill read as
     anonymous SaaS-blue and secondaries had no shared border. Streamlit re-keys
     these testids across minor versions — target both the kind= attr and the
     stBaseButton- testid. */
  .stButton button[kind="primary"],
  .stButton button[data-testid="stBaseButton-primary"] {
    background: var(--accent-gold) !important;
    border: 1px solid var(--accent-gold) !important;
    color: #fff !important;
    box-shadow: none !important;
    font-weight: 600 !important;
  }
  .stButton button[kind="primary"]:hover,
  .stButton button[data-testid="stBaseButton-primary"]:hover {
    background: #2b475d !important; border-color: #2b475d !important;
  }
  .stButton button[kind="primary"]:active,
  .stButton button[data-testid="stBaseButton-primary"]:active { background: #243c4f !important; }
  .stButton button[kind="secondary"],
  .stButton button[data-testid="stBaseButton-secondary"] {
    background: var(--surface-elev) !important;
    border: 1px solid var(--border-default) !important;
    color: var(--text-primary) !important;
  }
  .stButton button[kind="secondary"]:hover,
  .stButton button[data-testid="stBaseButton-secondary"]:hover { border-color: var(--text-muted) !important; }
  .stButton button:focus-visible {
    outline: 2px solid var(--accent-gold) !important; outline-offset: 2px !important;
  }
  /* Selectbox hover, to match the bordered controls. */
  div[data-baseweb="select"] > div:hover { border-color: var(--text-muted) !important; }
  /* st.warning() text measured 4.24:1 on its own tinted background —
     below the 4.5:1 AA minimum for normal text (WCAG 1.4.3), and this is
     the exact surface most likely to carry text a user needs to read
     correctly (calibration/trust caveats). Darkened to 5.52:1, pixel-
     verified against the live composited background (round-1
     accessibility audit, 2026-07-05). Success/info/error weren't
     independently confirmed failing — left alone rather than guess-fixed. */
  [data-testid="stAlertContentWarning"] { color: #7c5b04 !important; }
  /* Radio/select: BaseWeb hides the native input and paints a custom
     dot/box, so the browser's default focus ring (which draws around the
     *native* element) has nothing visible to attach to — a keyboard-only
     or low-vision user tabbing through the "Compare at item level" radio
     (every slot dialog + Vault + upgrade panel) or the "Prog dungeons"
     multiselect got zero visible confirmation of where focus landed
     (WCAG 2.4.7, round-1 accessibility audit, 2026-07-05). `:focus-within`
     on the wrapper forwards focus state to the visible control, same
     pattern as the text-input rule above. */
  label[data-baseweb="radio"]:focus-within > div:first-child {
    box-shadow: 0 0 0 2px var(--accent-gold) !important;
  }
  div[data-baseweb="select"] > div:focus-within {
    border-color: var(--accent-gold) !important;
    box-shadow: 0 0 0 1px var(--accent-gold) !important;
  }

  /* ── Section rhythm: normalize every separator ─────────────────────────────
     Separators come from both st.divider() and st.markdown("---") via different
     DOM paths; with no hr rule their margins fell to differing Streamlit
     defaults → uneven vertical rhythm. One rule covers both. */
  hr {
    margin: 32px 0 !important;
    border: none !important;
    border-top: 1px solid var(--border-subtle) !important;
  }

  /* ── Expander cards ────────────────────────────────────────────────────────
     The detail/upgrade expanders sat as a bare unframed stack while everything
     above was carded. Give them the shared light card frame so they join the
     one card family (the dark gear panel stays the single deliberate
     exception). */
  [data-testid="stExpander"] details {
    border: 1px solid var(--border-subtle) !important;
    border-radius: var(--radius-card) !important;
    background: var(--surface-elev) !important;
  }

  /* ── Heading font (2026-06-20 cohesion pass) ───────────────────────────────
     Streamlit defaults HEADINGS to a serif (Source Serif) while body + the
     theme `font = "sans serif"` render sans — so H1/H2/H3 fought the body in a
     serif/sans mix. The Codex SPEC's "print serif" was never actually wired
     (no @import, no headingFont), so the rendered serif is Streamlit's default,
     not a chosen face. Pin headings to the body sans family for one type voice.
     Mono stays only on the trust strip + numeric columns (a deliberate data
     signal, not drift). */
  .stApp h1, .stApp h2, .stApp h3, .stApp h4,
  [data-testid="stMarkdownContainer"] h1,
  [data-testid="stMarkdownContainer"] h2,
  [data-testid="stMarkdownContainer"] h3,
  [data-testid="stMarkdownContainer"] h4 {
    font-family: inherit !important;
    letter-spacing: -0.01em;
  }

  /* Hide the Streamlit sidebar entirely (panel + the collapsed >> arrows).
     simf never built sidebar content that earned its space; the empty
     panel on load + a single "Change character" button on a loaded state
     prompted "what's this menu FOR?" The change-character action lives
     inline now (see _render_run_config_strip); load-summary banners use
     st.toast(). Keep both selectors — Streamlit renders the panel and
     the collapsed toggle as separate elements. */
  [data-testid="stSidebar"],
  [data-testid="stSidebarCollapsedControl"] {
    display: none !important;
  }

  /* Trust strip — small, dense, monospace, sits beneath the H1 so the
     page title and verdict get the visual budget. No border/divider — the
     strip is supporting metadata, not chrome. */
  .run-config-strip {
    font-family: 'SF Mono', Monaco, Consolas, monospace;
    font-size: 11px;
    color: var(--text-muted);
    padding: 0 0 8px 0;
    margin: -4px 0 16px 0;
    overflow-x: auto;
    white-space: nowrap;
  }

  /* Demote multiselect chips ("Prog dungeons") from saturated gold fills
     to a ghost outline — saturation was eating the verdict card next to
     it. Gold stays as the answer-tier accent (active tabs, winner rule,
     trial banner) rather than every selected filter. */
  [data-baseweb="tag"] {
    background-color: transparent !important;
    border: 1px solid var(--border-default) !important;
    color: var(--text-secondary) !important;
  }
  [data-baseweb="tag"]:hover {
    border-color: var(--text-muted) !important;
  }
  [data-baseweb="tag"] span,
  [data-baseweb="tag"] svg { color: var(--text-secondary) !important; }

  /* Gear-tab swap-recommendation headline ("N slots recommend a swap ·
     total +X% (+Y eHP)") — the single most decision-relevant number on the
     Gear tab (recommend.py). Was plain markdown bold (body-text size,
     ~14px), reading quieter than the section headers around it (live-UI
     review, 2026-07-18: "the eye lands on chrome first"). Sized between the
     verdict card's 24px <h2> (the app's single biggest answer) and body
     text — this is a secondary-but-still-primary answer, not a caption. */
  .gear-swap-headline {
    font-size: 19px;
    font-weight: 600;
    letter-spacing: -0.005em;
    color: var(--text-primary);
    margin: 4px 0 10px 0;
  }

  /* Verdict card — flat surface, accent bar carries the meaning.
     `.verdict-neutral` (2026-07-18, live-UI review) is a THIRD state,
     distinct from good/warn: "nothing to do, your gear already wins" is
     genuinely good news, not a warning — painting it with the same bronze
     bar as a real problem (e.g. "can't read this item's stats") misleads on
     a tool whose whole value proposition is trust. Reuses the existing
     neutral --border-default gray rather than inventing a new hue. */
  .verdict-best, .verdict-warn, .verdict-neutral {
    background: var(--surface-elev);
    color: var(--text-primary);
    padding: 20px 24px;
    border-radius: var(--radius-card);
    margin-bottom: 16px;
    border-left: 3px solid var(--accent-good);
  }
  .verdict-warn { border-left-color: var(--accent-warn); }
  .verdict-neutral { border-left-color: var(--border-default); }
  /* h3 alongside h2: `log_formatters.py`'s `_verdict_card` (the Why-died
     surface's death-verdict card, sharing these same `.verdict-best`/
     `.verdict-warn` classes) renders its headline as an h3 — that surface's
     own page title ("Why did I die?") is the h2, so the card demoted to
     avoid a second top-level heading (2026-07-28 accessibility pass). Same
     visual weight either way; only the semantic level differs by caller. */
  .verdict-best h2, .verdict-warn h2, .verdict-neutral h2,
  .verdict-best h3, .verdict-warn h3 {
    margin: 0 0 6px 0;
    font-size: 24px;
    font-weight: 600;
    letter-spacing: -0.01em;
  }
  .verdict-best p, .verdict-warn p, .verdict-neutral p {
    margin: 0;
    font-size: 13px;
    color: var(--text-muted);
  }
  /* `Weakest: PoS -300 eHP.` callout sits inline inside the verdict <p>
     but tinted with --accent-warn so the eye picks up the dungeon where
     the swap is *worst*, not just the dungeons where it's best. */
  .verdict-best .verdict-weakest,
  .verdict-warn .verdict-weakest,
  .verdict-neutral .verdict-weakest { color: var(--accent-warn); }
  /* Coverage sub-headline ("Wins all 7 prog dungeons …") sits as a small
     second <p> below the verdict sentence. The verdict names the top two
     dungeons; the coverage line tells the user the swap holds across
     every prog key. engaged_tank ask 2026-05-16. */
  .verdict-best .verdict-coverage,
  .verdict-warn .verdict-coverage,
  .verdict-neutral .verdict-coverage {
    margin: 10px 0 0 0;
    padding-top: 10px;
    border-top: 1px solid var(--border-subtle);
    font-size: 12px;
    color: var(--text-secondary);
    font-family: 'SF Mono', Monaco, Consolas, monospace;
  }

  /* Prescription card — used for derivative answers that follow the
     primary verdict (the CD-plan panel on the Why-died surface).
     Demoted from h2 to h3, 2px accent bar vs 3px, smaller padding.
     ONE h2 verdict per surface (v0.9 redesign principle, ui-critic
     2026-05-17). */
  .cd-prescription {
    background: var(--surface-elev);
    color: var(--text-primary);
    padding: 14px 18px;
    border-radius: var(--radius-card);
    margin-bottom: 12px;
    border-left: 2px solid var(--accent-good);
  }
  .cd-prescription.cd-prescription-warn {
    border-left-color: var(--accent-warn);
  }
  .cd-prescription h3 {
    margin: 0 0 4px 0;
    font-size: 17px;
    font-weight: 600;
    letter-spacing: -0.005em;
  }
  .cd-prescription p {
    margin: 0;
    font-size: 13px;
    color: var(--text-muted);
  }
  /* Calibration regime tag — sits inline at the end of the headline
     when no_plan death rate is in the "unphysically high" range and
     the absolute percent has been suppressed. */
  .cd-regime-tag {
    display: inline-block;
    margin-left: 8px;
    padding: 1px 8px;
    font-size: 11px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    border-radius: var(--radius-chip);
    background: var(--surface-base);
    color: var(--text-secondary);
    border: 1px solid var(--border-subtle);
  }

  /* Run-identity header — "Now analyzing: Ara-Kara +18 · Timed 28:34 ·
     2026-05-22". Sits at the top of the analysis surface so the user
     always knows which run inside the log is being analyzed. Visually
     subordinate to the verdict card below it (smaller, no border-left
     accent) but emphatic enough to read pre-attentively. Brutoh idea #a,
     2026-05-25. */
  .run-identity-header {
    background: var(--surface-elev);
    color: var(--text-primary);
    padding: 10px 14px;
    border-radius: var(--radius-card);
    font-size: 13px;
    margin-bottom: 12px;
    border: 1px solid var(--border-subtle);
  }
  .run-identity-header .run-identity-label {
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-size: 11px;
    color: var(--text-muted);
    margin-right: 6px;
  }
  .run-identity-header .run-identity-body {
    font-weight: 600;
    color: var(--text-primary);
  }

  /* Multi-run picker — shown only when the log has 2+ runs. Bold the
     selected radio label and tint its background so the active run reads
     as distinct from the idle siblings. The native Streamlit radio dot
     is already a marker, but Brutoh idea #a calls out "visually distinct
     from idle rows" — without these rules the only visual signal is the
     small filled dot, which is too quiet on a row of duplicate-looking
     +18 entries.

     Scoped to the radio's stable element-container class
     (`.st-key-v9_log_run_pick`, derived from the widget's ``key=``) so
     the rules don't leak to other radios on the surface. Streamlit
     wraps every widget's container in ``.st-key-<key>``. */
  .st-key-v9_log_run_pick label[data-baseweb="radio"] {
    padding: 6px 10px;
    border-radius: 4px;
    margin-bottom: 2px;
    transition: background-color 120ms ease;
  }
  .st-key-v9_log_run_pick label[data-baseweb="radio"]:hover {
    background-color: var(--surface-elev);
  }
  .st-key-v9_log_run_pick label[data-baseweb="radio"]:has(input:checked) {
    background-color: var(--surface-elev);
    border-left: 3px solid var(--accent-gold);
    padding-left: 7px;
  }
  .st-key-v9_log_run_pick label[data-baseweb="radio"]:has(input:checked) p {
    font-weight: 600;
    color: var(--text-primary);
  }

  /* Damage-school badges (Brutoh idea #d, 2026-05-25).
     Every "Why did I die?" surface that names an ability prefixes the
     name with a colored pill carrying the school text ("Physical",
     "Fire", "Magic", "Physical (bleed)" …). Tank decision-making is
     school-routed ("this is Magic → Spell Reflect, not Shield Block"),
     so the school is load-bearing context.

     Color is decoration; the text label is the WCAG 1.4.1 signifier
     (color must never be the only carrier of meaning). The pill `fg`
     is the ink color and `bg` is the fill at ~22% alpha — keeps the
     pill subtle next to the spell name and lets the ink hit 4.5:1
     contrast against `--surface-elev` (the card / table background).

     Color tokens live in `data/constants.yaml` under
     `damage_school_colors`; Python (`render_school_badge`) reads them
     there and emits `<span class="school-badge {school}">`. The fg/bg
     values below MUST stay in sync with that YAML — drift would mean
     the rendered pill carries a color the YAML thinks is something
     else. Kept inline here (rather than CSS-var-driven from the YAML)
     because Streamlit's stylesheet is a static string injected at
     page load, not a Python-rendered template.

     The bleed modifier dims the physical pill slightly so the user
     visually distinguishes "Physical" from "Physical (bleed)" without
     leaning on the parenthetical alone. */
  .school-badge {
    display: inline-block;
    padding: 1px 7px;
    margin-right: 4px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.02em;
    border-radius: var(--radius-chip);
    line-height: 1.5;
    white-space: nowrap;
    border: 1px solid currentColor;
  }
  /* Per-school color rules are injected from `damage_school_colors:`
     in `data/constants.yaml` immediately AFTER this <style> block —
     see `_render_school_badge_css()` below. Edit the YAML, not the
     CSS here, when changing colors. */
  /* Bleed: armor-bypass DOT (Rake, Rip, Rend, Deep Wounds …). Same hue
     family as physical, dashed border so the eye spots "this is
     physical but different" without losing the school tag. The
     fg/bg colors are computed from the physical entry of the
     `damage_school_colors:` YAML, lightened, in the injected CSS
     block below. */

  /* Wowhead icons rendered inline next to item names. Always served at
     `medium` (36px native) from zamimg and sized in CSS, so the browser
     downsamples a sharp source instead of upscaling a tiny one. */
  .item-icon {
    width: 36px; height: 36px;
    vertical-align: middle;
    border-radius: 4px;
    margin-right: 8px;
    border: 1px solid var(--border-default);
  }
  .slot-row .item-icon { width: 28px; height: 28px; margin-right: 10px; }

  /* Base link color for .item-link (live-UI review, 2026-07-18): outside a
     quality-tinted container (e.g. .gear-card-name, which already sets
     color:inherit + no underline) this fell through to the browser's
     default blue-underlined <a> — visible on every Vault-card Wowhead
     item-name link. Tokenized to the existing steel accent, underline only
     on hover (kept for the non-color-alone affordance, WCAG 1.4.1). */
  a.item-link, a.item-link:visited {
    color: var(--accent-gold);
    text-decoration: none;
  }
  a.item-link:hover {
    text-decoration: underline;
  }

  /* Wowhead-linked icon anchor. The anchor exists so Wowhead Power.js
     fires its rich tooltip on icon hover — without it, only the text
     link triggered. Strip the default <a> chrome so the icon reads as
     an image, not a clickable link. Click still opens wowhead in a
     new tab (matching the text-link affordance). */
  a.icon-tooltip-anchor {
    display: inline-flex;
    text-decoration: none;
    color: inherit;
    line-height: 0;
  }
  a.icon-tooltip-anchor:focus-visible {
    outline: 2px solid var(--accent-gold);
    outline-offset: 2px;
    border-radius: 4px;
  }

  /* Inline item-name Wowhead link (`_item_link_html`). On the vault cells and
     the slot-dialog alt/current rows this is the primary keyboard affordance
     (the item-details popover was removed there to avoid a second, unscaled
     stat number), so it needs a visible focus ring — the default is
     near-invisible on the cream surface (WCAG 2.4.7 Focus Visible). */
  a.item-link:focus-visible {
    outline: 2px solid var(--accent-gold);
    outline-offset: 2px;
    border-radius: 4px;
  }

  /* Vault 3-up grid — one cell per vault pick. Framed as a light card so the
     Vault tab rhymes structurally with the verdict cards + the gear paperdoll
     (one card skeleton, two palettes — the dark panel is the lone exception).
     The winner gets a gold left-edge so the eye lands on it. */
  .vault-cell {
    background: var(--surface-elev);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-card);
    padding: 12px 14px;
    margin-bottom: 8px;
  }
  .vault-cell-winner {
    border-color: var(--accent-gold);
    box-shadow: inset 3px 0 0 0 var(--accent-gold);
  }
  /* Dead picks (owned at the offer's reachable ceiling) stay rendered for
     transparency but recede — never brighter than a live choice. */
  .vault-cell-dead { opacity: 0.6; }
  .vault-cell-slot {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-muted);
    margin-bottom: 2px;
  }

  /* Read-only banner — only shown when ?ro=1 cold-share param is active.
     Distinct visual treatment from the trial banner so a viewer can tell
     "I'm looking at someone else's gear" from "I'm mid-trial." */
  .read-only-banner {
    background: var(--surface-elev);
    border-left: 3px solid var(--accent-warn);
    color: var(--text-secondary);
    padding: 10px 14px;
    border-radius: var(--radius-card);
    font-size: 13px;
    margin-bottom: 16px;
  }
  .read-only-banner strong { color: var(--text-primary); }
  .read-only-banner code {
    background: var(--surface-base);
    padding: 1px 4px;
    border-radius: var(--radius-chip);
    font-size: 11px;
  }

  /* Tier-set badge — sits in the header band beside the trust strip when a
     character is loaded. Gold border because tier-set state is gear-tier
     signal, same family as the vault winner rule. Brutoh's "next single
     thing" ask 2026-05-16 — promoted out of the collapsed sidebar (UI
     critic Round 1 #10) so vault-Tuesday users see the 4pc / 2pc state
     without two clicks. */
  .tier-badge {
    display: inline-block;
    padding: 3px 8px;
    margin: 0 0 0 12px;
    border: 1px solid var(--accent-gold);
    border-radius: var(--radius-chip);
    font-size: 11px;
    color: var(--text-primary);
    background: var(--surface-elev);
    vertical-align: middle;
    line-height: 1;
    /* Bound + ellipsize instead of hard-clipping with no "…" — its column
       narrows to a fraction of the viewport at <=640px (see the run-config
       media query below) and this inline-block had no overflow handling
       at all, so a long set-bonus name (e.g. "4pc Night Ender's Vesture")
       just stopped mid-word (R3 review, 2026-07-19/20). */
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .tier-badge strong { color: var(--accent-gold); }
  .tier-badge-muted {
    border-color: var(--border-default);
  }
  .tier-badge-muted strong { color: var(--text-muted); }

  /* Trial-swap banner — sits above the verdict when a trial is active. */
  .trial-banner {
    background: var(--surface-elev);
    border-left: 3px solid var(--accent-gold);
    color: var(--text-secondary);
    padding: 10px 14px;
    border-radius: var(--radius-card);
    font-size: 13px;
    margin-bottom: 12px;
  }
  .trial-banner strong { color: var(--text-primary); }
  /* Slot count chip — same family as the secondary text but slightly
     muted so the bold banner label still leads. */
  .trial-banner__count { color: var(--text-secondary); }

  /* Slot row in gear list — character-sheet style.
     The slot label is a small uppercase eyebrow ABOVE the item name so
     the icon + name pair owns the row; ilvl sits right-aligned and dim.
     UI critic 2026-05-16: "paperdoll was two columns of buttons in
     costume — let the icon do the labeling work." */
  .slot-row {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 6px 8px;
    border-bottom: 1px solid var(--border-subtle);
  }
  .slot-row-icon {
    width: 52px; height: 52px;
    border-radius: 4px;
    border: 1px solid var(--border-default);
    flex: 0 0 52px;
    object-fit: cover;
  }
  /* Item-quality border — mirrors WoW's quality-colored icon frame so the
     paperdoll reads like the in-game character sheet. Quality is classified
     Python-side (epic floor + legendary allowlist — see
     constants.yaml: gear.quality_border) and emitted as a `q-epic` /
     `q-legendary` class on the icon. 2px so the hue is legible at 52px
     without out-shouting the row hairline. Both border colors clear WCAG
     1.4.11 non-text contrast (3:1) against the cream paper surface —
     legendary is darkened from Blizzard's #ff8000 (only 2.2:1) for that. */
  .slot-row-icon.q-epic      { border: 2px solid var(--q-epic); }
  .slot-row-icon.q-legendary { border: 2px solid var(--q-legendary); }
  .slot-row-text { flex: 1; min-width: 0; }
  .slot-row-eyebrow {
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-muted);
    margin-bottom: 2px;
  }
  .slot-row-name {
    color: var(--text-primary);
    font-size: 14px;
    font-weight: 500;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    display: block;
  }
  .slot-row-name a,
  .slot-row-name a:visited { color: inherit; text-decoration: none; }
  .slot-row-name a:hover { text-decoration: underline; }
  .slot-row-ilvl {
    flex: 0 0 auto;
    font-size: 11px;
    color: var(--text-muted);
    font-family: 'SF Mono', Monaco, Consolas, monospace;
  }
  /* Upgrade-track badge — sits left of the ilvl ("Myth 6/6" · i289) so the
     player reads track + rank at a glance instead of a bare number. The
     per-track tint color is injected from constants.yaml
     (gear.upgrade_track_colors) by `_render_track_badge_css()`; this rule
     only owns the layout + a muted fallback so an untinted track still
     renders legibly. */
  .track-badge {
    flex: 0 0 auto;
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--text-muted);
    margin-right: 6px;
  }
  .slot-row.empty .slot-row-name { font-style: italic; color: var(--text-muted); }
  .slot-row.empty .slot-row-icon {
    background: var(--surface-elev);
    border-style: dashed;
  }

  /* ── Gear-card paperdoll — the dark "character sheet" (the Gear tab) ───────
     The universal WoW / Raidbots / QE Live paperdoll (examples/screenshots/
     ui.png): a dark inset "character window" deliberately embedded in the
     light Codex page — boxed gear CARDS recessed into a warm-slate panel,
     quality-coloured item name leading each card, "Slot · Track · iLvl"
     subtitle, two mirrored columns with an empty centre channel, weapons
     centred below. DISPLAY ONLY — no buttons on the sheet; browse / trial-swap
     lives off it (the control below + the upgrade panel).

     Why dark works here when full dark mode was reverted (~line 133): this
     surface is PURE display HTML with zero Streamlit widgets inside it, so it
     can't trigger the widget-override cascade that killed the OS-driven dark
     theme. Everything is scoped to .gear-sheet-dark — the cream app, and all
     its widgets, are untouched. NO :root edits, NO prefers-color-scheme.
     Palette + AA contrast ratified by an 8-agent design + a11y workflow
     (2026-06-20): every name/meta/signal hue clears WCAG AA (≥4.5:1) on the
     #121218 card, every icon-quality border clears 1.4.11 (≥3:1).
     Guarded by tests/test_gear_dark_contrast.py.

     `.gear-sheet-dark` is reused (not paperdoll-exclusive) by the upgrade
     panel's row list further down this file — see the `.upgrade-panel-dark`
     rules below the upgrade-panel CSS block for that second, smaller
     instance of this same scope. */
  .gear-sheet-dark {
    --gs-panel:        #191922;   /* warm-slate panel (not cold #000) — harmonises with cream */
    --gs-card:         #121218;   /* recessed card, darker than the panel — the ui.png inset look */
    --gs-q-epic:       #b86ff2;   /* epic-purple name + icon border (5.86:1 on card) */
    --gs-q-legendary:  #ff9a40;   /* legendary-orange (8.84:1) */
    --gs-q-rare:       #4aa3ff;   /* rare-blue (7.09:1) */
    --gs-q-uncommon:   #5fd66b;   /* uncommon-green (10.06:1) */
    --gs-q-common:     #e6e7ec;   /* common/poor + default name (15.11:1) */
    --gs-meta:         #969cab;   /* "Slot · Track · iLvl" subtitle (6.79:1) */
    --gs-swap:         #ffc24b;   /* WoW upgrade-gold — the survivability moat (11.61:1) */
    --gs-best:         #828a98;   /* quiet "Best you own" (5.37:1) */
    --gs-gem:          #6ee7d8;   /* aquamarine gem-upgrade line (12.52:1) — distinct from swap gold so a re-gem never reads as "replace this item" */
    --gs-setbreak:     #ff8866;   /* coral caution — swap breaks a tier-set bonus (7.45:1) */
    --gs-head:         #f0f1f5;   /* panel title (15.46:1 on panel) */
    --gs-eyebrow:      #9aa0af;   /* spec eyebrow (6.67:1 on panel) */
    background: var(--gs-panel);
    border: 1px solid rgba(255, 255, 255, 0.09);
    border-radius: 14px;
    /* Top inset highlight + soft outer shadow = a designed surface sitting ON
       the page like a media embed, not a flat dark rectangle. */
    box-shadow:
      inset 0 1px 0 rgba(255, 255, 255, 0.06),
      0 2px 6px rgba(10, 10, 18, 0.30),
      0 14px 34px -14px rgba(10, 10, 18, 0.58);
    padding: 20px 24px 24px;
    /* Cap + centre so the rails keep the in-game character-sheet proportion
       (ui.png is ~1000px). Full content width made each rail ~620px, so the
       outer-hugging card content left a vast empty inner that read as dead
       space. A capped panel floats in the cream page like a framed window. */
    max-width: 1040px;
    margin: 0 auto;
  }
  .gear-sheet-head {
    display: flex; flex-direction: column; gap: 3px;
    margin-bottom: 16px; padding-bottom: 13px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.07);
  }
  .gs-eyebrow {
    font-size: 11px; font-weight: 600; letter-spacing: 0.08em;
    text-transform: uppercase; color: var(--gs-eyebrow);
  }
  .gs-title { font-size: 22px; font-weight: 600; letter-spacing: -0.01em; color: var(--gs-head); }
  /* Two columns with a 28px centre gutter = the clean empty middle the
     reference keeps. One markdown block (not st.columns) so the slate is a
     single continuous surface — st.columns would inject cream seams. */
  .gear-sheet-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 28px; }
  .gear-sheet-weapons {
    display: grid; grid-template-columns: 1fr 1fr; gap: 8px 28px;
    margin-top: 8px; width: 66%; margin-inline: auto;
  }
  /* min-width: 0 is load-bearing, not decorative: .gear-col is a CSS Grid
     item under .gear-sheet-grid's `1fr 1fr` columns, and a grid item's
     automatic min-width defaults to its content's min-content size — which
     for a column flexbox is the widest min-content contribution among its
     children, regardless of any `min-width: 0` set further down on
     .gear-card/.gear-card-text. Without this, one long nowrap gem/enchant
     line (e.g. "Flawless Versatile Peridot → Flawless Masterful Lapis
     +0.29% (+5,535 eHP)") forces .gear-col past its column's width,
     overflowing .gear-sheet-grid
     and .gear-sheet-dark horizontally — confirmed live (2026-07-05): at a
     1072px viewport this clipped the right column's trinket cards at the
     page edge in Chromium, and the resulting reflow desynced the weapon
     row's centered grid from the dark sheet's rounded bottom edge, reading
     as a card rendering outside its container. Firefox's slightly different
     text-metric rounding happened not to tip over the same threshold, which
     is why headless-Firefox repros of the original report came back clean. */
  .gear-col { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
  .gear-sheet-dark .gear-card {
    display: flex;
    align-items: center;
    gap: 11px;
    padding: 9px 11px;
    background: var(--gs-card);
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 8px;
    min-width: 0;
  }
  .gear-sheet-dark .gear-card.mirror { flex-direction: row-reverse; text-align: right; }
  /* Empty (cosmetic shirt/tabard, or unequipped) cards RECEDE — a touch darker
     than the filled card and a hair more transparent border, so a real piece
     always reads louder than a gap. */
  .gear-sheet-dark .gear-card.empty { background: #0f0f15; border-color: rgba(255, 255, 255, 0.04); }
  .gear-sheet-dark .gear-card-icon {
    width: 52px; height: 52px;
    border-radius: 6px;
    border: 1px solid rgba(255, 255, 255, 0.16);
    box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.45);
    flex: 0 0 52px;
    object-fit: cover;
  }
  /* Icon quality border (1.4.11, ≥3:1 on card) — all four colour tiers ship
     now so they paint the moment a quality field lands; today the classifier
     emits epic/legendary only (epic floor + legendary allowlist). The additive
     inset glow makes the colour read as an in-game item frame, not a stroke
     (purely decorative — contrast comes from the 2px solid border). */
  .gear-sheet-dark .gear-card-icon.q-epic {
    border: 2px solid var(--gs-q-epic);
    box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.55), inset 0 0 6px rgba(184, 111, 242, 0.45);
  }
  .gear-sheet-dark .gear-card-icon.q-legendary {
    border: 2px solid var(--gs-q-legendary);
    box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.55), inset 0 0 6px rgba(255, 154, 64, 0.45);
  }
  .gear-sheet-dark .gear-card-icon.q-rare {
    border: 2px solid var(--gs-q-rare);
    box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.55), inset 0 0 6px rgba(74, 163, 255, 0.45);
  }
  .gear-sheet-dark .gear-card-icon.q-uncommon {
    border: 2px solid var(--gs-q-uncommon);
    box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.55), inset 0 0 6px rgba(95, 214, 107, 0.45);
  }
  .gear-sheet-dark .gear-card-icon.empty { background: #0c0c11; border: 1px dashed rgba(255, 255, 255, 0.12); box-shadow: none; }
  .gear-card-text { flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
  .gear-card-name {
    font-size: 14px;
    font-weight: 600;
    color: var(--gs-q-common);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  /* Quality-coloured names (the "everyone knows" WoW signal). The name is also
     a Wowhead link, so the colour must hit the span, the <a>, and a:visited. */
  .gear-card-name.q-epic, .gear-card-name.q-epic a,
  .gear-card-name.q-epic a:visited { color: var(--gs-q-epic); }
  .gear-card-name.q-legendary, .gear-card-name.q-legendary a,
  .gear-card-name.q-legendary a:visited { color: var(--gs-q-legendary); }
  .gear-card-name.q-rare, .gear-card-name.q-rare a,
  .gear-card-name.q-rare a:visited { color: var(--gs-q-rare); }
  .gear-card-name.q-uncommon, .gear-card-name.q-uncommon a,
  .gear-card-name.q-uncommon a:visited { color: var(--gs-q-uncommon); }
  .gear-card-name a, .gear-card-name a:visited { color: inherit; text-decoration: none; }
  .gear-card-name a:hover { text-decoration: underline; text-underline-offset: 2px; }
  .gear-card-name.empty { color: var(--gs-best); font-weight: 500; font-style: italic; }
  .gear-card-meta {
    font-size: 11px;
    color: var(--gs-meta);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  /* Survivability signal: a gold "↑ +X eHP" when a better option exists, a
     quiet "Best you own" otherwise. The number is the moat — never a glyph. */
  .gear-card-swap { font-size: 13px; font-weight: 700; color: var(--gs-swap); }
  /* The item being REPLACED, named so the sheet still answers "swap FROM
     what?" without a click — the recommended item itself is the card's
     hero name up top now (2026-07-05: the old hierarchy led with the
     equipped item and buried the recommendation here in gold, which read
     as "swap to WHAT?" pointing at the wrong item — ui-craft-critic
     review). Muted meta grey, not swap gold: this line names the outgoing
     piece, not the upgrade. Truncates with an ellipsis; the name is a
     focusable Wowhead link. */
  .gear-card-swap-to {
    font-size: 11px; color: var(--gs-meta); opacity: 0.82;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .gear-card-swap-to a, .gear-card-swap-to a:visited { color: inherit; text-decoration: none; }
  .gear-card-swap-to a:hover { text-decoration: underline; text-underline-offset: 2px; }
  .gear-card-best { font-size: 11px; color: var(--gs-best); }
  /* Gem line — a socketed item's "should I re-gem this?" answer. Aquamarine,
     not the swap gold: a gem call-out is a much smaller action (one gem, not
     a whole item) and must not read as "this piece needs replacing". The name
     is a Wowhead link (hover = native stats tooltip), so it needs the same
     truncation as the swap-to line — a long gem name + delta must not wrap. */
  .gear-card-gems {
    font-size: 11px; color: var(--gs-gem); display: block;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .gear-card-gems.optimal { color: var(--gs-best); }
  /* Below-swap-bar state: a real gap exists but sits below the swap-
     worthiness bar. Neutral meta tone (NOT the green "optimal" color) —
     2026-07-05 gem-trust review: green here read as a false "genuinely the
     best pick" claim. No verdict word either way (2026-07-30 — dropped
     "kept" too): the line just names current → best gem + the real ΔeHP
     number and lets the reader decide. The nowrap/ellipsis default above
     would cut that number off mid-digit with no way to recover it, so let
     this specific honesty state wrap to 2 lines instead (`white-space:
     normal` REDUCES min-content width vs. the nowrap default, so it cannot
     reintroduce the PR #279 `.gear-col` grid overflow that a long nowrap
     line caused). */
  .gear-card-gems.below-bar {
    color: var(--gs-meta);
    white-space: normal; display: -webkit-box; -webkit-line-clamp: 2;
    -webkit-box-orient: vertical; overflow: hidden;
  }
  .gear-card-gems a, .gear-card-gems a:visited { color: inherit; text-decoration: none; }
  .gear-card-gems a:hover { text-decoration: underline; text-underline-offset: 2px; }
  /* Enchant line — same weight/role as the gem line (a modifier on an
     already-equipped item, not a whole-item swap), so it shares the gem's
     aquamarine/quiet-gray pair rather than introducing a 3rd audited color.
     ".unmodeled" is ONLY the two purely-informational states with no link
     to click — "no enchant" (genuinely empty + unpriced) and "no enchant
     exists for this slot" — where the neutral meta tone signals there's
     nothing here to look at. A real, Wowhead-linked enchant name that
     merely isn't priced (head/shoulder/weapon Avoidance/Leech/Speed/procs)
     does NOT get this class (2026-07-31 user ask) — it keeps the base
     aquamarine below like every other clickable enchant name, since muting
     a real link bought no honesty the "not modeled" text doesn't already
     state plainly. */
  .gear-card-enchant {
    font-size: 11px; color: var(--gs-gem); display: block;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .gear-card-enchant.optimal { color: var(--gs-best); }
  .gear-card-enchant.unmodeled { color: var(--gs-meta); }
  /* Below-swap-bar state: mirrors .gear-card-gems.below-bar's own comment
     — a real gap exists but sits below the swap-worthiness bar, no verdict
     word either way (2026-07-30). Neutral meta tone, not the green
     "optimal" color, for the identical reason PR #276 fixed for gems: a
     recognized-but-suboptimal enchant used to read a flat "· optimal"
     (is_optimal true via optimal_grace, without the stricter
     is_identity_optimal check the renderer now uses first).
     Wraps to 2 lines rather than truncating, same reasoning as the gem
     sibling rule above — the ΔeHP number this line carries must survive. */
  .gear-card-enchant.below-bar {
    color: var(--gs-meta);
    white-space: normal; display: -webkit-box; -webkit-line-clamp: 2;
    -webkit-box-orient: vertical; overflow: hidden;
  }
  .gear-card-enchant a, .gear-card-enchant a:visited { color: inherit; text-decoration: none; }
  .gear-card-enchant a:hover { text-decoration: underline; text-underline-offset: 2px; }
  /* Swap breaks an active 2pc/4pc — the ΔeHP doesn't count the set bonus, so
     caution the player rather than let the gold gain read as a clean win. */
  .gear-card-setbreak { font-size: 11px; color: var(--gs-setbreak); display: block; }
  /* This "swap" is an M+ loot-table drop the player doesn't own yet — same
     muted meta tone as the "↳ replaces" line, not the setbreak amber (it's
     provenance, not a caution) and not swap gold (nothing to Trial tonight). */
  .gear-card-source { font-size: 11px; color: var(--gs-meta); display: block; }
  /* Per-stat ΔeHP composition (Phase 2 of the eHP-transparency feature) —
     "eHP: +905 Stamina · +338 Versatility" under a gem/enchant/gear-swap/
     upgrade-panel ΔeHP number. Deliberately NOT `white-space: nowrap` (unlike
     the gem/enchant lines above): this text can genuinely run long (several
     stats), and the real PR #279 `.gear-col` overflow bug was exactly a long
     nowrap line escaping the grid — this one is allowed to wrap onto a
     second line inside the card instead. Base rule targets the light-themed
     surfaces (Vault cards, the slot-alternatives dialog); the `.gear-sheet-
     dark` override further down retints it for the dark paperdoll/upgrade
     panel, matching `.upgrade-ilvl`'s base+override pattern. */
  .gear-card-composition {
    font-size: 11px; color: var(--text-muted); display: block;
    white-space: normal; line-height: 1.35;
  }
  .gear-card-composition.trinket-registry { font-style: italic; }
  /* A stat name inside the composition line whose survivability mechanism is
     known gets a dotted underline + a small "ⓘ" glyph + native `title=`
     tooltip (a short parenthetical would make the top-line composition long
     enough to risk overflow again; a hover affordance keeps the line short
     per the brief's "short parenthetical OR hover/tooltip" — see
     stat_composition.py). The underline alone read as an invisible
     convention (round-2 readability workshop: 4 of 6 personas needed to
     zoom in to confirm it exists) — the glyph is the primary discoverability
     cue now; the underline stays as reinforcement, not the sole signifier
     (WCAG 1.4.1 — never color/decoration alone). */
  .stat-mechanism { border-bottom: 1px dotted currentColor; cursor: help; }
  /* Small enough to read as a footnote marker, not a headline; no color
     override here — it inherits the (already-legible, per-theme) muted tone
     of the surrounding composition text, same as the underline. */
  .stat-mechanism-glyph { margin-left: 1px; font-size: 0.85em; }
  /* "+N more" — a native <details>/<summary>, not st.expander: it has to work
     standing alone inside an already-joined HTML blob (the paperdoll cards
     and the upgrade panel each emit ALL their rows through one st.markdown
     call — a real Streamlit widget mid-blob would break that). */
  .gear-card-composition-more { font-size: 11px; color: var(--text-muted); }
  .gear-card-composition-more summary {
    cursor: pointer; list-style: none; display: inline;
  }
  .gear-card-composition-more summary::-webkit-details-marker { display: none; }
  .gear-card-composition-more summary::before { content: "▸ "; }
  .gear-card-composition-more[open] summary::before { content: "▾ "; }
  .gear-card-composition-more-list { margin-top: 2px; padding-left: 10px; }
  /* A slot with a better option gets a gold inset edge on the centre-facing
     side + a faint gold wash (layered OVER the card colour so swap text keeps
     its contrast), so the eye finds upgrades with no button chrome. Reinforces
     the "↑ +X eHP" text — not the sole cue (WCAG 1.4.1). */
  .gear-sheet-dark .gear-card.swap {
    border-color: #5a4a26;
    background-color: var(--gs-card);
    background-image: linear-gradient(90deg, rgba(255, 194, 75, 0.10), rgba(255, 194, 75, 0.02) 55%);
    box-shadow: inset 3px 0 0 0 var(--gs-swap);
  }
  .gear-sheet-dark .gear-card.mirror.swap {
    background-image: linear-gradient(270deg, rgba(255, 194, 75, 0.10), rgba(255, 194, 75, 0.02) 55%);
    box-shadow: inset -3px 0 0 0 var(--gs-swap);
  }
  /* Narrow viewport: collapse to one column, un-mirror, flip the edge + wash left. */
  @media (max-width: 640px) {
    .gear-sheet-grid, .gear-sheet-weapons { grid-template-columns: 1fr; width: auto; }
    .gear-sheet-dark .gear-card.mirror { flex-direction: row; text-align: left; }
    .gear-sheet-dark .gear-card.mirror.swap {
      background-image: linear-gradient(90deg, rgba(255, 194, 75, 0.10), rgba(255, 194, 75, 0.02) 55%);
      box-shadow: inset 3px 0 0 0 var(--gs-swap);
    }
  }

  /* At <=640px, Streamlit stacks EVERY column full-width — the header's
     logo/"Why did I die?"/"Gear & vault" and the run-config strip's trust-text/
     "Calibration details"/"Change character" landed as 5 separate
     full-width rows above any verdict (round-1 review, 2026-07-05). The
     leading column (logo, trust text) stays full-width on its own row —
     it's the widest/most-variable content and reads fine alone — but the
     buttons after it share the next row instead of stacking one-per-row.
     `key=` on the wrapping containers (`_render_header`,
     `_render_run_config_strip`) is what makes `.st-key-*` a valid,
     narrowly-scoped hook here; every other multi-column layout in the app
     is untouched. This handles a genuinely NARROW viewport (real device
     width). It does NOT help browser zoom — measured empirically
     (2026-07-18): `document.documentElement.style.zoom` never changes
     `window.innerWidth`/`clientWidth`/media-query evaluation in either
     engine (confirmed via a live probe, Firefox and Chromium both), so no
     px breakpoint can ever catch a zoom-only overflow. This is consistent
     with scripts/screenshot.py's own docstring (clientWidth stays FIXED
     while scrollWidth grows under zoom — that's the mechanism the tool
     uses to reveal overflow bugs); the earlier version of this comment
     mis-stated that claim as "zoom shrinks the viewport," which is wrong —
     corrected here. Zoom purely re-renders text/controls LARGER inside the
     SAME fixed-px columns — a real 200%-zoom clip/wrap
     bug this media query cannot fix by widening its breakpoint. The
     calibration chip's specific 200%-zoom symptom (a confusing
     symmetric text clip) is fixed at the source instead — left-aligning
     its label so Streamlit's existing nowrap+ellipsis resolves to a
     normal trailing "…" — see `_render_calibration_badge_css()` in
     `ui/css.py`. The "Change character" button's word-wrap at extreme
     zoom is a real but lower-severity cosmetic artifact of the same
     root cause, left as-is. */
  @media (max-width: 640px) {
    .st-key-header-nav-row [data-testid="stHorizontalBlock"],
    .st-key-run-config-row [data-testid="stHorizontalBlock"] {
      flex-wrap: wrap;
    }
    .st-key-header-nav-row [data-testid="stColumn"]:first-child,
    .st-key-run-config-row [data-testid="stColumn"]:first-child {
      min-width: 100% !important;
    }
    .st-key-header-nav-row [data-testid="stColumn"]:not(:first-child),
    .st-key-run-config-row [data-testid="stColumn"]:not(:first-child) {
      min-width: 0 !important;
      flex: 1 1 0 !important;
    }

    /* Mobile chrome-before-verdict reflow (2026-07-26 — Season 2 readiness
       punch list item, named 2026-07-05/07-16). Measured on the live app at
       390px: ~915px of pure chrome (header, run-config trust strip, the
       collapsed calibration-note expander, the "Prog dungeons" multiselect,
       the Vault/Gear subtab buttons) rendered above ANY verdict-bearing
       content on both subtabs — more than a full mobile viewport of
       scrolling before a first-time visitor sees an answer. Two of the
       biggest, safest-to-fix contributors:

       (1) The Vault/Gear subtab row — unlike the header/run-config rule
       above, both columns are equal buttons (no wide "logo" column to keep
       full-width), so both get the same override: share one row instead of
       stacking as two separate 96px-tall full-width rows. */
    .st-key-vault-gear-subtab-row [data-testid="stHorizontalBlock"] {
      flex-wrap: wrap;
    }
    .st-key-vault-gear-subtab-row [data-testid="stColumn"] {
      min-width: 0 !important;
      flex: 1 1 0 !important;
    }

    /* (2) The "Prog dungeons" multiselect — the single biggest offender
       (184px with a fully-selected 8-dungeon catalog). Caps the chip/tag
       area (BaseWeb's `ValueContainer`, the select control's first child —
       the second child is the clear/dropdown-arrow icon column) to a
       scrollable ~3-row window instead of letting it grow with every chip.
       Purely visual — every chip stays selected and removable, just inside
       a scroll area — so this can't change what dungeons are averaged over,
       only how tall the control looks. Targets `data-baseweb="select"`
       (a stable BaseWeb attribute, not one of Streamlit's own
       version-churning atomic classnames) so a future Streamlit upgrade
       degrades gracefully to today's uncapped behavior rather than breaking. */
    .st-key-_dungeon_select [data-baseweb="select"] > div > div:first-child {
      max-height: 96px;
      overflow-y: auto;
      /* A bare `overflow-y: auto` measured with NO visible scrollbar in
         Firefox (auto-hiding overlay scrollbar) — a hidden 4-of-8-selected
         list reads as "that's everything," not "scroll for more," which is
         exactly the silent-truncation trap this project's own design ethos
         rejects everywhere else. Force a persistent, visible thumb instead
         of relying on the browser's default hover/auto-hide behavior. */
      scrollbar-width: thin;
      scrollbar-color: var(--border-default) transparent;
    }
    .st-key-_dungeon_select [data-baseweb="select"] > div > div:first-child::-webkit-scrollbar {
      width: 6px;
    }
    .st-key-_dungeon_select [data-baseweb="select"] > div > div:first-child::-webkit-scrollbar-thumb {
      background: var(--border-default);
      border-radius: 3px;
    }
  }

  /* Upgrade-impact panel rows — same skeleton as .slot-row, two extra
     trailing columns for the ilvl arrow and the ΔeHP / ΔDPS pair.
     Right-aligned so the numbers form a column you can scan. */
  .upgrade-ilvl {
    flex: 0 0 auto;
    font-size: 11px;
    color: var(--text-muted);
    font-family: 'SF Mono', Monaco, Consolas, monospace;
    text-align: right;
    min-width: 76px;
  }
  .upgrade-deltas {
    flex: 0 0 auto;
    font-size: 12px;
    color: var(--text-primary);
    text-align: right;
    min-width: 160px;
  }
  .upgrade-deltas .upgrade-dps { color: var(--text-muted); }
  /* "capped" badge appears when the upgrade clamps to the season ilvl
     ceiling (Voidcore can't push past 295 in Midnight 12.0.5). Muted
     yellow chip — informative, not alarming. */
  .upgrade-cap-badge {
    display: inline-block;
    margin-left: 6px;
    padding: 0 5px;
    font-size: 9.5px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    border-radius: var(--radius-chip);
    background: rgba(204, 156, 0, 0.18);
    color: #8a6a00;
    vertical-align: 1px;
  }

  /* Eyebrow naming the upgrade panel a distinct, subordinate utility (a
     ranker over the same equipped set, not a second paperdoll) — light-page
     styling since it sits above a real `st.number_input` widget, which can't
     live inside the dark display-only scope below. Same recipe as
     `.gs-eyebrow` (11px/600/uppercase/letter-spaced) so the two read as the
     same design language, just on the two different surfaces they're stuck
     living on (P4 polish, 2026-07-02). */
  .upgrade-panel-eyebrow {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text-muted);
    margin-bottom: 2px;
  }

  /* ── Upgrade panel's dark rows (P4 polish, 2026-07-02) ──────────────────────
     Reuses `.gear-sheet-dark` (not a new scope) so these rows inherit the SAME
     `--gs-*` tokens as the paperdoll cards above — visually the ranker's
     OUTPUT (the rows) joins the dark "character sheet" world, while its INPUT
     (the ilvl-gain number field above) stays on the normal light control
     chrome, same as every other widget in the app. `upgrade-panel-dark` only
     overrides the outer shell to read a size step SMALLER/flatter than the
     primary panel (no recessed per-row card, just hairline dividers) — a
     deliberate "subordinate, not a second sheet" cue. Every `--gs-*` token
     below is reused as-is: computed contrast against the lighter `--gs-panel`
     bg (this box has no separate recessed card tier) still clears WCAG AA —
     see tests/test_gear_dark_contrast.py. */
  .gear-sheet-dark.upgrade-panel-dark {
    max-width: none;
    margin: 0;
    padding: 6px 20px 10px;
  }
  .gear-sheet-dark .slot-row { border-bottom-color: rgba(255, 255, 255, 0.07); }
  .gear-sheet-dark .slot-row:last-child { border-bottom: none; }
  .gear-sheet-dark .slot-row-icon {
    border-color: rgba(255, 255, 255, 0.16);
    box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.45);
  }
  .gear-sheet-dark .slot-row-icon.q-epic      { border-color: var(--gs-q-epic); }
  .gear-sheet-dark .slot-row-icon.q-legendary { border-color: var(--gs-q-legendary); }
  .gear-sheet-dark .slot-row-eyebrow { color: var(--gs-meta); }
  .gear-sheet-dark .slot-row-name    { color: var(--gs-q-common); }
  .gear-sheet-dark .upgrade-ilvl     { color: var(--gs-meta); }
  .gear-sheet-dark .upgrade-deltas   { color: var(--gs-q-common); }
  .gear-sheet-dark .upgrade-deltas .upgrade-dps { color: var(--gs-meta); }
  .gear-sheet-dark .upgrade-cap-badge {
    background: rgba(255, 194, 75, 0.18);
    color: var(--gs-swap);
  }
  /* Composition line retint for the dark paperdoll cards + upgrade-panel
     rows — same `--gs-meta` the card's other meta/best text already uses,
     so it reads as part of the same sheet rather than an inconsistent
     lighter/darker patch. */
  .gear-sheet-dark .gear-card-composition,
  .gear-sheet-dark .gear-card-composition-more { color: var(--gs-meta); }
  /* The dotted-underline affordance (see `.stat-mechanism` above) inherits
     `currentColor`, which on this dark card resolves to `--gs-meta` — fine
     contrast for TEXT (6.79:1) but a hairline dotted border rendered in
     that same muted tone reads as barely-there at 100% zoom (the exact
     "had to zoom in to confirm it exists" complaint). Give the underline
     its own explicit, brighter color on this surface only, independent of
     the muted text color it sits under, so the affordance itself clears a
     comfortable margin over AA's 3:1 non-text minimum instead of riding on
     text contrast alone. */
  .gear-sheet-dark .stat-mechanism { border-bottom-color: var(--gs-head); }

  /* Visually-hidden helper for screen-reader-only copy. Used to give
     iconic buttons (gear, info, "Try this") accessible names that name
     the *target* (e.g. "for Helm") without changing visual layout. */
  .visually-hidden {
    position: absolute !important;
    width: 1px; height: 1px;
    padding: 0; margin: -1px;
    overflow: hidden;
    clip: rect(0,0,0,0);
    white-space: nowrap;
    border: 0;
  }

  /* Hide Streamlit-framework chrome (Deploy button, three-dot menu, the
     running-man status indicator, the Snowflake deploy modal). No serious
     product ships with the framework's deploy CTA visible to end users —
     ui-craft-critic 2026-05-16 P0. We keep the <header> element itself so
     the sidebar toggle (also a header child) remains accessible. */
  /* Hide individual chrome children — but NOT the toolbar itself.
     `stExpandSidebarButton` (the >> arrow) is a child of stToolbar; if
     we hide the toolbar, the sidebar button collapses to a 0x0
     unclickable target. Audit P0-1, 2026-05-16. */
  /* `stDeployButton` was renamed `stAppDeployButton` in a later Streamlit
     release (currently on 1.57.0) — the old selector alone silently stopped
     hiding it, and "Deploy" leaked back onto every screenshot (round-2
     usability review, 2026-07-05, ui-craft-critic). Keep both testids so a
     future Streamlit version bump in either direction doesn't reopen this. */
  [data-testid="stDeployButton"],
  [data-testid="stAppDeployButton"],
  [data-testid="stStatusWidget"],
  [data-testid="stMainMenu"],
  [data-testid="stDecoration"] { display: none !important; }
  header[data-testid="stHeader"] {
    background: transparent !important;
    min-height: 48px !important;
  }
  /* Toolbar stays visible (so the sidebar button stays clickable) but
     its background is transparent and its right-side children are
     hidden above. */
  [data-testid="stToolbar"] {
    background: transparent !important;
    min-height: 48px !important;
  }
  /* The sidebar-expand chevron itself — pin a real click target so it
     can't be sized down to 0 by its empty-flex-row parent. */
  [data-testid="stSidebarCollapsedControl"],
  [data-testid="stExpandSidebarButton"],
  [data-testid="collapsedControl"] {
    min-width: 32px !important;
    min-height: 32px !important;
    display: flex !important;
    visibility: visible !important;
  }

  /* Header / nav button labels — never wrap. Streamlit's column
     allocation can squeeze a label until it stacks one glyph per line
     (audit P0-2). `nowrap` + `text-overflow: ellipsis` keeps the label
     readable when the cell is narrower than the text. */
  [data-testid="stPopover"] button,
  [data-testid="stPopover"] button p {
    white-space: nowrap !important;
    overflow: hidden !important;
    text-overflow: ellipsis !important;
  }

  /* Wowhead Power popover — portal'd into <body> at the page root.
     Power.js renders its tooltip with class/id starting with `whtt-`
     (modern build) and historically also `wowhead-tooltip` / `powerinfo`.

     Three things we have to enforce:
       1. Z-index 2147483646 (one below max int) so the tooltip
          ALWAYS beats Streamlit `st.dialog` / `st.popover` modals
          (BaseWeb modal stack sits ~10,000).
       2. An opaque dark background. Wowhead's own CSS provides one
          but takes a beat to load; until then the body shows through
          and the tooltip reads as transparent against the dark app
          surface.
       3. DO NOT force `position: fixed` — Wowhead's own positioner
          uses `position: absolute` and a fixed override clamps the
          tooltip to viewport coords, which collapses its computed
          height and bleeds content past the visible border.

     The previous `position: fixed !important` rule was a stale
     workaround for a stMain `overflow: visible` bug (also reverted).
     2026-05-16 user report: tooltip transparent + content overflow. */
  .wowhead-tooltip,
  #wowhead-tooltip,
  div[id*="wowhead"],
  div[class*="powerinfo"],
  .whtt,
  [id^="whtt"],
  [class^="whtt"],
  [class*=" whtt"] {
    z-index: 2147483646 !important;
    background-color: #15171b !important;
    color: #e6edf3 !important;
    border: 1px solid #2a2d33 !important;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.6) !important;
  }
  /* Inner panels Wowhead uses for the colored body — same dark fill so
     the gradient backdrop doesn't bleed through. Wowhead tooltips are
     dark by convention (their own CSS ships dark): keep them dark even
     under the Codex light palette so the item-quality colours read
     correctly. Light text is hard-coded here (not --text-primary) so
     the palette flip can't accidentally make this dark-on-dark. */
  .whtt-tooltip,
  .whtt-tooltip-content,
  .whtt-stats,
  .whtt-text {
    background-color: #15171b !important;
    color: #e6edf3 !important;
  }
</style>
""",
    unsafe_allow_html=True,
)


# ─── CSS / data-loader call sites (defs live in css.py / state.py) ────────────
# The badge-CSS injectors run at module load (after st.set_page_config above),
# so they must be imported HERE, before their call sites — the rest of the
# foundation re-exports sit at end-of-file. Re-exported for the external
# contract via the end-of-file block too (`_inject_wowhead_tooltips` etc.).
from simf.ui.css import (  # noqa: E402
    _render_calibration_badge_css,
    _render_reduced_motion_css,
    _render_school_badge_css,
    _render_track_badge_css,
)

_render_school_badge_css()
_render_track_badge_css()
_render_calibration_badge_css()
_render_reduced_motion_css()


# ─── Surface 1 — Gear ─────────────────────────────────────────────────────────


def _render_dungeon_filter() -> None:
    """Multi-select for the dungeons the verdict + slot-dialog should average
    over. Empty selection falls back to the full catalog (treats 'I cleared
    the box' as 'I haven't chosen yet'). When the fallback fires we tell
    the user explicitly — audit P1-1 (2026-05-16) flagged the silent
    "all 8" rebound as a trust leak."""
    catalog = _dungeon_catalog()
    options = [d["id"] for d in catalog]
    labels = {d["id"]: d.get("name") or d["id"] for d in catalog}
    stored = _ss().get("selected_dungeon_ids") or options
    default = [d for d in stored if d in options] or options
    selected = st.multiselect(
        "Prog dungeons",
        options=options,
        default=default,
        format_func=lambda d: labels.get(d, d),
        key="_dungeon_select",
        help=(
            "Vault verdict and slot-swap deltas average ΔeHP across these "
            "dungeons. Narrow to the keys you're pushing this week."
        ),
    )
    if not selected:
        st.caption(
            "ℹ️ No dungeons selected — averaging across all "
            f"{len(options)}. Pick the keys you're pushing to narrow the verdict."
        )
    if _season_pool_is_stale():
        st.caption(
            "⚠️ This list is still Season 1's dungeons. simf hasn't seen real "
            "Season 2 combat logs yet, so it can't verify each dungeon's damage "
            "profile well enough to switch the pool over. Cross-check your actual "
            "weekly keys in-game rather than assuming this list matches — simf "
            "will switch to the real Season 2 pool automatically once that data lands."
        )
    effective_ids = selected or options
    _ss()["selected_dungeon_ids"] = effective_ids
    render_danger_pull_cheatsheet([d for d in catalog if d["id"] in effective_ids])


_GEAR_SUBTAB_KEY = "_gear_active_subtab"


def _set_gear_subtab(subtab: str) -> None:
    """`on_click=` for the Vault/Gear sub-nav buttons below — mirrors
    `_set_view`'s pattern (Streamlit fires `on_click` before the script
    reruns, so the new value is visible in time for THIS render's `type=`
    computation). Replaces a naive `st.tabs(["Vault", "Gear"])` pair, which
    has no persisted-active-tab state across a rerun: a live 2026-07-11
    review found that ANY fast (no-spinner) rerun elsewhere on the page —
    flipping a radio, clicking an unrelated button — silently bounced the
    visible sub-tab back to Vault mid-interaction, even though nothing was
    lost server-side."""
    st.session_state[_GEAR_SUBTAB_KEY] = subtab


def _render_surface_gear() -> None:
    char = Character.from_dict(_char_data_effective())
    _refresh_baseline_ehp(char)

    _render_dungeon_filter()
    dungeons = _selected_dungeons()

    _render_trial_banner(char, dungeons)

    # Surface any sim-derived-marginals fallback. The closed-form path
    # returns zero for haste/crit/mastery/strength — exactly the bias
    # Phase 2.7 fixed. If we silently re-enter that path, the picker
    # becomes wrong again with no visible signal. Render a caveat so a
    # future regression doesn't hide.
    fallback = _surv_marginals_fallback_warning(char)
    if fallback:
        st.caption(
            f"⚠️ Stat weights using closed-form fallback — {fallback}. "
            "Mastery / haste / strength values are under-credited."
        )

    # Which sub-tab defaults active on a FRESH session — computed once (via
    # `setdefault` below) rather than re-derived every render, since
    # `st.tabs()`'s "always activates its first tab" quirk is exactly what
    # made this reset on every rerun (see `_set_gear_subtab`). Gear defaults
    # active when there's no vault this week — landing on an empty "No vault
    # choices found" panel was the worst first impression (real-flow review
    # 2026-05-15).
    has_vault = bool(_vault())
    default_subtab = "gear" if not has_vault else "vault"
    active_subtab = _ss().setdefault(_GEAR_SUBTAB_KEY, default_subtab)

    # Keyed container so the mobile CSS (`.st-key-vault-gear-subtab-row`, same
    # `_render_header`/`_render_run_config_strip` technique) can pull these two
    # buttons onto one shared row at narrow widths instead of stacking as two
    # separate full-width rows (measured 2026-07-26: this row alone cost 96px
    # of pure chrome at 390px, on top of the header/run-config/dungeon-filter
    # chrome already above it — part of the "chrome before verdict" mobile
    # reflow, see docs/validation or the PR description for the full budget).
    with st.container(key="vault-gear-subtab-row"):
        sub_cols = st.columns(2)
        with sub_cols[0]:
            st.button(
                "Vault",
                width="stretch",
                type=("primary" if active_subtab == "vault" else "secondary"),
                on_click=_set_gear_subtab,
                args=("vault",),
                key="gear_subtab_vault",
            )
        with sub_cols[1]:
            st.button(
                "All gear",
                width="stretch",
                type=("primary" if active_subtab == "gear" else "secondary"),
                on_click=_set_gear_subtab,
                args=("gear",),
                key="gear_subtab_gear",
            )
    if active_subtab == "vault":
        _render_vault_panel(char, dungeons)
    else:
        _render_gear_list(char, dungeons)

    # Key-level verdict sits BELOW the vault/gear tabs so the vault
    # verdict card remains the page's first paint — ui-craft-critic
    # Phase 2.1 round 1 #2: two H3 verdicts stacked above the vault
    # card was a hierarchy bug.
    _render_key_level_verdict_panel(char)


# ─── Surface 2 — Why did I die? (ported renderer, behaviour unchanged) ────────


def _render_surface_log() -> None:
    if not _has_character():
        # Cold log surface (no character): the only way back to loading was the
        # header "Gear & vault" button (wrong verb), and a `?view=log` shared-URL
        # visitor could miss that simf also rates gear + the Great Vault. Give a
        # labelled cross-sell + back-to-loading affordance (panel review,
        # 2026-06-09). `_set_view("gear")` with no character routes to the load
        # form. Hidden once a character is loaded (incl. log auto-hydrate).
        msg_col, btn_col = st.columns([3, 1])
        with msg_col:
            st.caption(
                "No character loaded — load one to also check survivability "
                "and your **Great Vault**."
            )
        with btn_col:
            st.button(
                "Load a character",
                on_click=_set_view,
                args=("gear",),
                key="log_load_character",
                width="stretch",
            )
    cd = _ss().get("char_data") or {}
    # Combat logs reference players as `Name-Server-Region` (e.g.
    # `Brutoh-Uldum-EU`). Pre-fill the target field with the full name
    # when we have the server/region from SimC; bare `Brutoh` matches
    # zero events in any log. Fall back to plain name when missing —
    # the field stays editable. ROADMAP 3.11.
    name = cd.get("name", "")
    server = cd.get("server", "")
    region = (cd.get("region", "") or "").upper()
    full_name = f"{name}-{server.title()}-{region}" if name and server and region else name
    healer_profile = _ss().get("healer_profile", "m+_high_key_healer")
    render_surface_log(
        character_name=full_name,
        class_spec=cd.get("class_spec"),
        char_dict=cd or None,
        healer_profile=healer_profile,
        uncalibrated_warning=_uncalibrated_spec_warning(),
        public=_is_public_mode(),
    )


# ─── Advanced mode (toggle) ───────────────────────────────────────────────────

# Feature flag: gates both the header `Advanced` toggle and the panel body.
# Flip to True in the same commit that ships a real Advanced surface
# (stat-weight Pareto, manual entry, CSV export) — until then, exposing the
# toggle leaks a TODO + raw JSON dump to users and is a trust leak
# (ui-critic #9, engaged_tank #2, 2026-05-16).
_ADVANCED_HAS_CONTENT = False


def _render_advanced(char: Character | None) -> None:
    if not _ADVANCED_HAS_CONTENT:
        return
    if not _ss().get("advanced_mode"):
        return
    with st.expander("Advanced mode", expanded=True):
        st.caption("Stat weights · manual entry · CSV export · per-card iter override.")
        if char is not None:
            st.json(
                {
                    "race": char.race,
                    "class_spec": char.class_spec,
                    "max_hp": int(char.max_hp()),
                    "armor_dr": round(char.armor_dr(), 4),
                    "vers_pct": round(char.versatility_pct() * 100, 2),
                }
            )
        st.caption(
            "Stat-weight Pareto + per-stat scaling curves are deferred from "
            "this minimal v0.9 shell. See ROADMAP.md."
        )


# ─── Top-level layout ─────────────────────────────────────────────────────────


def _render_header() -> None:
    # The public instance now exposes the WCL-URL "Why did I die?" surface
    # (ADR 0001), so it gets the same Gear / Why-did-I-die nav as owner mode.
    # The Advanced toggle stays gated off everywhere via _ADVANCED_HAS_CONTENT.
    # Header columns shrink when Advanced is gated off — keeping a dead
    # fourth slot would leave a visible empty column and lopsided layout.
    layout = [4, 1, 1, 1] if _ADVANCED_HAS_CONTENT else [4, 1, 1]
    # Keyed container so the mobile CSS below (`.st-key-header-nav-row`) can
    # pull just the nav buttons — not the logo — onto a shared row at narrow
    # widths, without touching every other multi-column layout in the app
    # (round-1 review, 2026-07-05: at 390px the logo, "Why did I die?",
    # "Gear," "Calibration details," and "Change character" stacked as 5
    # separate full-width rows before any verdict was visible).
    with st.container(key="header-nav-row"):
        cols = st.columns(layout)
        with cols[0]:
            # `simf` is the page title — must be the only <h1> for WCAG 2.4.6 /
            # 1.3.1 heading hierarchy. Bumped from `## simf` (<h2>) so the
            # verdict-card <h2> below sits one level beneath it instead of
            # siblings-without-a-parent.
            st.markdown("# simf")
        current_view = _ss().get("view", "gear")
        # Cold landing (no character) has no active gear view to highlight —
        # the body is the "Check your gear and vault picks" load form, not
        # the gear surface. Lighting
        # "Gear & vault" there is an active-state lie ("you're already in
        # Gear & vault" while we're asking you to load) and reads as
        # competing chrome (panel review, 2026-06-09). Only light it once
        # gear content exists.
        gear_is_active = current_view == "gear" and _has_character()
        with cols[1]:
            st.button(
                "Why did I die?",
                width="stretch",
                type=("primary" if current_view == "log" else "secondary"),
                on_click=_set_view,
                args=("log",),
                key="nav_why",
            )
        with cols[2]:
            st.button(
                "Gear & vault",
                width="stretch",
                type=("primary" if gear_is_active else "secondary"),
                on_click=_set_view,
                args=("gear",),
                key="nav_gear",
            )
        if _ADVANCED_HAS_CONTENT:
            with cols[3]:
                st.toggle(
                    "Advanced",
                    key="advanced_mode",
                    help="Show stat weights, manual entry, CSV export.",
                )


def _render_read_only_banner() -> None:
    if not _is_read_only():
        return
    # Public instance: read-only is permanent and enforced inline at each
    # disabled control (the Try buttons carry their own reason), so a top-of-page
    # disclaimer is pure noise to a first-time visitor — paint nothing. Only the
    # ?ro=1 SHARE banner (which advertises the ?ro=0 escape on the owner build)
    # ever renders.
    if _is_public_mode():
        return
    # No aria-label: for role="status" the accessible name is the element's
    # text content. A short aria-label here would mask the visible recovery
    # instructions from SR users. The eye glyph is decorative — aria-hidden so
    # it isn't announced as "eye".
    body = (
        "<strong>Read-only share</strong> — you're viewing someone else's "
        "loadout, so trial swaps are disabled. Append <code>?ro=0</code> to "
        "the URL to interact, or open simf with no parameters to start fresh."
    )
    st.markdown(
        '<div class="read-only-banner" role="status">'
        '<span aria-hidden="true">👁️&nbsp;&nbsp;</span>' + body + "</div>",
        unsafe_allow_html=True,
    )


# ─── re-exports (foundation split, PR 1) ──────────────────────────────────────
# The L0/L1 foundation layers were extracted into models/format_html/css/state.
# These imports both (a) satisfy app.py's own internal references to the moved
# names and (b) preserve the external contract: every symbol historically
# importable via ``from simf.ui.app import X`` stays importable that way. All of
# app.py's executable code runs inside functions called from ``main()`` (invoked
# at the very bottom), so these names are bound before any of them run.
from simf.ui.css import (  # noqa: F401,E402
    _WOWHEAD_TOOLTIPS_JS,
    _inject_wowhead_tooltips,
)
from simf.ui.format_html import (  # noqa: F401,E402
    _SKILL_ASSUMPTION_CDS_BY_SPEC,
    _SPEC_PLURAL_LABEL,
    _base_secondary_pool,
    _composite_score,
    _dps_to_ehp_ratio,
    _filter_qualifier,
    _humanize_loadout_key,
    _item_quality_class,
    _pick_ladder_key,
    _school_mix_caption,
    _skill_assumption_caption_for_spec,
    _surv_axis_label,
    _track_badge_html,
    _trinket_scoring_caveat,
    _verdict_claimed_key,
    _wowhead_dungeon_url,
)
from simf.ui.gear_surface import _render_gear_list, _render_slot_browse_control  # noqa: F401,E402
from simf.ui.item_html import (  # noqa: F401,E402
    _icon_link_html,
    _icon_link_html_q,
    _item_link_html,
    _slot_card_html,
)
from simf.ui.load import (  # noqa: F401,E402
    _PUBLIC_MAX_EXTRA_ITEMS,
    _apply_share_url,
    _build_run_config,
    _do_raider_io_load,
    _do_simc_load,
    _load_demo_character,
    _render_run_config_strip,
    _render_simc_paste,
    _scroll_to_top_if_requested,
    _surface_load_summary_toast,
    _tier_sets_badge_html,
)
from simf.ui.marginals import (  # noqa: F401,E402
    _char_marginals_signature,
    _marginals_for,
    _surv_marginals_fallback_warning,
)
from simf.ui.models import (  # noqa: F401,E402
    _PAIRED_SLOTS,
    _card_for,
    _dedupe_paired_picks,
    _flag_set_breaks,
    _is_meaningful_swap,
    _SlotPick,
)

# ─── re-exports (L3 render-panel split) ───────────────────────────────────────
# The L3 render-panel layer was extracted into recommend/slot_dialog/
# trial_banner/upgrade_panel/vault_panel/verdict. Same contract as the blocks
# above: these (a) bind the names app.py's gear surface still calls and (b)
# preserve ``from simf.ui.app import X`` for every moved panel. Bound before
# ``main()`` runs (and the gear surface runs only from inside ``main()``).
from simf.ui.recommend import (  # noqa: F401,E402
    _PAPERDOLL_LEFT,
    _PAPERDOLL_RIGHT,
    _PAPERDOLL_WEAPONS,
    _per_slot_picks,
    _render_paperdoll_grid,
    _render_recommendation_summary,
    _render_surv_slider,
)
from simf.ui.state import (  # noqa: F401,E402
    _HIGH_RESIDUAL_THRESHOLD_PCT,
    _ITEM_DB,
    _MIN_SANE_BASELINE_EHP,
    _SIM_LOCK,
    _SPEC_MODELING_CAVEAT,
    _announce_status,
    _apply_trial_all,
    _apply_trial_swap,
    _baseline_ehp,
    _baseline_equipped,
    _char_baseline_ehp,
    _char_data_effective,
    _dungeon_catalog,
    _equipped,
    _format_ehp_delta,
    _has_character,
    _high_residual_warning,
    _icon_for_item,
    _is_public_mode,
    _is_read_only,
    _item_db_date,
    _loadouts_for_spec,
    _refresh_baseline_ehp,
    _reset_rio_realm,
    _reset_trial_swaps,
    _resolve_equipped_stats,
    _revert_trial_slot,
    _season_pool_is_stale,
    _selected_dungeons,
    _set_view,
    _sim_slot,
    _simf_commit_time,
    _simf_sha,
    _ss,
    _stats_for_item,
    _stats_unresolved,
    _surv_weight,
    _trial_state,
    _uncalibrated_spec_warning,
    _vault,
)
from simf.ui.trial_banner import (  # noqa: F401,E402
    _render_trial_banner,
    _trial_delta_vs_baseline,
)
from simf.ui.upgrade_panel import (  # noqa: F401,E402
    _render_upgrade_panel,
    _upgrade_row_html,
)
from simf.ui.vault_panel import (  # noqa: F401,E402
    _render_vault_cell,
    _render_vault_grid,
    _render_vault_panel,
    _render_verdict_card,
)
from simf.ui.verdict import (  # noqa: F401,E402
    _KEY_VERDICT_ITERATIONS,
    _SKILL_LADDER_ITERATIONS,
    _render_key_level_verdict_panel,
    _render_skill_ladder_panel,
    _render_world_ceiling_caption,
)
from simf.ui.widgets import (  # noqa: F401,E402
    _compare_mode_radio,
    _render_compare_control,
    _render_gem_suggestions,
    _render_per_dungeon_row,
    _render_unresolved_stats_banner,
)


def _record_view_reached(view: str) -> None:
    """Record the first time THIS session reaches a given top-level view
    (gear vs log) — a privacy-safe "which surface do visitors ever find"
    signal (2026-07-31 visitor-signal follow-through). Only fires once per
    session per distinct view: Streamlit reruns the whole script on every
    widget interaction, so without the `_views_logged` de-dupe set this
    would spam one line per rerun instead of one per view-reached-for-the-
    first-time. Caller gates this on `_is_public_mode()` — the owner's own
    dev/local usage must never pollute the funnel signal."""
    logged = _ss().setdefault("_views_logged", set())
    if view in logged:
        return
    logged.add(view)
    from simf.core import share_hits

    share_hits.record({"event": "view_reached", "view": view})


def main() -> None:
    # Must run before _render_header() — a demo/online/`/simc` load (or the
    # reverse, "Change character") swaps the whole body under the visitor
    # without moving their browser scroll offset; see `_flag_scroll_to_top`'s
    # docstring in load.py. Consumes its own one-shot flag, so this is a
    # no-op on every render that isn't the one right after such a load.
    _scroll_to_top_if_requested()
    _inject_wowhead_tooltips()
    _apply_share_url()
    char = None
    if _has_character():
        try:
            char = Character.from_dict(_char_data_effective())
        except Exception:
            char = None

    iterations = _ss().get("iterations", 1000)
    seed = _ss().get("seed", 42)
    dungeons = _selected_dungeons()
    # Display names, not abbreviations — the "Model error by dungeon" table
    # in the same popover resolves to display names too (round-1 review,
    # 2026-07-05); one name form per dungeon across the whole popover.
    dungeon_labels = [d.get("name", d["id"]) for d in dungeons]
    healer = _ss().get("healer_profile", "m+_high_key_healer")
    talent_hash = (_ss().get("char_data") or {}).get("talents", "—")
    cfg = _build_run_config(iterations, seed, dungeon_labels, healer, talent_hash, char)

    _render_header()
    view = _ss().get("view", "gear")
    if _is_public_mode():
        _record_view_reached(view)
    # The model-error / build trust strip belongs next to a verdict — not on the
    # cold load form, where a first-time visitor reads "±X% model error · build
    # <sha>" as dev-status noise before they've done anything (cold-load review,
    # 2026-06-14). Show it once there's a character (gear surface) or we're on
    # the log surface (which produces its own verdict to attach the number to).
    on_cold_load_form = view != "log" and not _has_character()
    if not on_cold_load_form:
        _render_run_config_strip(cfg)
    _render_read_only_banner()
    # ROADMAP Batch H (2026-07-08) — replaces the always-expanded
    # `st.warning(uncal)` wall of text that used to sit here for every spec
    # but Prot Warrior/Guardian Druid. `_uncalibrated_spec_warning()` is
    # UNCHANGED (same signature, same return-value contract — see its
    # docstring and the incident history above `log_cd_plan.py`'s own
    # truthiness check on it): both the badge and this text read the exact
    # same `calibration_tier`, so they can never disagree. The full text is
    # not deleted, only moved into a collapsed expander next to a compact
    # badge — a page-wide caveat paragraph on every load was the actual
    # trust cost this batch item targets, not the information itself.
    # The standalone "Model calibration: ✓ Calibrated" pill row that used to
    # live here was folded into the single calibration chip on the run-config
    # strip above (F-001/F-002, review round R2, 2026-07-17 — one element
    # pairing confidence + number, itself the details click target). The full
    # caveat text stays: it moves nowhere, still opening in its own collapsed
    # expander for the non-calibrated specs that have one.
    uncal = _uncalibrated_spec_warning()
    cd = _ss().get("char_data") or {}
    spec = cd.get("class_spec", "")
    if spec and uncal:
        with st.expander("Calibration note"):
            st.markdown(uncal)
    _surface_load_summary_toast()

    # Log surface stands on its own — no character required. Route by view
    # first so the "Why did I die?" nav button isn't swallowed by the
    # SimC-paste onboarding when no character is loaded.
    # In SIMF_PUBLIC mode the surface renders the WCL-URL flow ONLY (no local
    # file upload — the dominant OOM/abuse vector); see _render_surface_log +
    # render_surface_log(public=...) and ADR 0001.
    if view == "log":
        _render_surface_log()
    elif not _has_character():
        _render_simc_paste()
    else:
        _render_surface_gear()

    _render_advanced(char)

    # Deploy-provenance footer — renders on EVERY view (unlike the sha-in-
    # trust-strip above, which is cold-load-gated). The point is a human
    # glancing at any page can see what build is running; see
    # format_build_footer's docstring for what this does and does NOT prove.
    st.caption(format_build_footer(_simf_sha(), _simf_commit_time()))
    # Quiet feedback link — every view, not just public/anonymous sessions (any
    # user, owner included, might hit a bug worth reporting). Plain mailto
    # rather than a GitHub issue link — a bug report isn't always something
    # the reporter wants public.
    st.caption("Feedback or a bug? [info@simf.cc](mailto:info@simf.cc)")
    # AGPL-3.0 source-availability notice — every view, every mode. Required
    # by AGPL §13: anyone interacting with this program over a network must
    # be offered the corresponding source, not just users who happen to know
    # simf is open source. Deliberately unconditional, same as the two
    # captions around it.
    st.caption("AGPL-3.0 licensed — [get the source](https://github.com/ainestal/simf)")
    # Blizzard-affiliation disclaimer + privacy notice — every view, every
    # mode. Both are contractual conditions of the Blizzard Developer API
    # Terms of Use (attribution + a posted privacy policy), not etiquette,
    # so this can't be gated behind a mode check the way the trust-strip
    # caveats above are. See privacy_footer.py's docstring for the audit
    # finding and why the copy says what it says.
    render_privacy_footer()


main()
