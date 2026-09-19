# UI copy voice — "calm expert" do/don't sheet

This file exists because simf's UI copy keeps getting re-litigated one card
at a time (six review rounds across 2026-07-04 to 2026-07-06, PRs #272-#284)
instead of checked against a shared standard. It collects the rules the
project has actually already enforced — grounded in real before/after diffs,
not invented — so the next caption gets it right on the first pass.

## The voice: "calm expert"

Not a hype-y game guide ("CRUSH your keys!"), not a dry engineering readout
(raw field names and no context). A calm expert: someone who respects that
you're capable of understanding real numbers and won't hide behind vague
reassurance, but also won't assume you already speak simf's jargon. Two
personas set the two edges of this bar and both have to be satisfied at
once:

- **elite_tank** wants zero infantilization — every number reproducible, no
  hand-waving, no "trust me."
- **novice_tank** bounces off an undefined acronym — every term glossed
  inline the first time it appears, or the sentence around it is useless.

CONTRIBUTING.md names this exact tension from the 2026-07-05 gem-trust review:
copy that "reads as a jargon-dump to novice players" while the same copy is
"the plain-English standard" elite reviewers praised for precision. The
resolution isn't a compromise that half-satisfies both — it's doing both
at once: state the real number, then define the one term in it a newcomer
wouldn't know, in the same breath.

A calm expert also states uncertainty and scope plainly. Not hidden behind
silence (a stale caveat, or no caveat at all), and not over-hedged into
meaninglessness (a wall of qualifiers that tells the reader nothing they can
act on). Say what's known, say what isn't, say what to do about it.

---

## Do / don't rules

Each rule below is grounded in a real fix already shipped — quoted from the
actual before/after text, not paraphrased.

### 1. Gloss jargon inline on first appearance — don't hide it

A term load-bearing to every sentence around it must be defined the first
time a visitor can see it, not buried in a tooltip they may never open.

- BAD: `"Scores only this week's 3 vault offers, rescaled to your gear."`
  — no definition of "eHP," the term the very next line depends on.
- GOOD: `"Scores only this week's 3 vault offers, rescaled to your gear.
  eHP = effective HP — how much raw damage you can survive, counting armor
  and defensives."`
- PR #282 (`vault_panel.py`, `_EHP_GLOSS`); the same pass glossed DTPS/HRPS
  in the key-level verdict caption.

### 2. Never leak dev/internal instructions into a user-facing error

If the fix for an error is something only the app owner can do (add API
credentials, change a build pipeline), that's not an action the visitor in
front of the error can take. Give them one they actually can.

- BAD: `"Update SimulationCraft to a version that emits
  gear_haste_rating= lines, or add Blizzard API credentials so simf can
  fetch stats directly."`
- GOOD: `"Vault rows below show what was parsed. Try pasting a fresh
  /simc export — in-game type /simc, copy the result, then Change
  character → paste."`
- PR #282 (`vault_panel.py`, the stats-unresolved warning).

### 3. Name the player's action, not the algorithm's output

Copy that describes what the engine computed reads as engine-speak. Copy
that describes what the player is already doing right reads as coaching.

- BAD: `"Heuristic CD usage is already best for this run."`
- GOOD: `"You're already pressing your CDs as well as a scheduled plan
  would."`
- Cooldown-planner polish, 2026-05-17 (commit `ac81626`).

### 4. State scope honestly instead of collapsing two truths into one

Two panels that answer a related-but-different question must say so, or
their numbers will look like they contradict each other. They named the
exact failure this caused: the same item read as a "-11,503 eHP dead pick"
on Vault and a "+56,244 eHP recommended swap" on Gear, with nothing telling
the reader the two tabs score different things.

- BAD: no scope statement on either tab — the reader has to infer why two
  panels disagree.
- GOOD: `"Scores only this week's 3 vault offers, rescaled to your gear."`
  (Vault) alongside `"The Gear tab re-optimizes everything you already
  own — gems, enchants, rings, bag & vault pieces — and may still find
  something worth doing."` (the bridge sentence pointing from Vault to Gear).
- PR #273 (scope subtitles on both tabs); the underlying two-handed-weapon
  contradiction that made this visible was fixed in PR #272.

### 5. Time-bound any action that takes longer than an instant click

A button with no time estimate reads the same as an instant action. A
timed real click at 34s with no warning first reads as the app hanging.

- BAD: a bare `"Compute verdict"` button, no caption.
- GOOD: `"Takes about 20-30s — it sweeps every key level from +2 to
  +24."` rendered as a caption above the button.
- PR #282 (`verdict.py`).

### 6. Replace an over-claiming badge with honest distinct states

A single badge covering two different truths ("genuinely the best" vs. "a
real gap exists but is too small to act on") reads as a lie the moment
someone finds the gap. Split it into as many states as there are truths.

- BAD: `💎 empty socket · optimal` — an ungemmed socket and the model's
  actual best pick rendered identically.
- GOOD: three distinct states — confident `💎 {name} · optimal`; neutral
  `💎 empty socket · kept — best: {name} {delta}` when a real gap exists
  below the swap-worthiness bar; an actionable `💎 {name} · +{delta} eHP`
  swap otherwise.
- PRs #275/#276 (the gem "optimal"-label trust fix).

### 7. Grammar must agree with dynamic content

A hardcoded singular/plural reads as broken the moment the list it
describes has the other cardinality.

- BAD: hardcoded `"was"` / `"it"` regardless of how many lever names got
  joined into the sentence.
- GOOD: `ready_was = "was" if len(lever_names) == 1 else "were"` (and the
  matching `"it"` / `"them"` pronoun swap).
- PR #284 (coaching-copy grammar fix, round-3 review).

### 8. Card hierarchy leads with the answer to the question being asked

If the question is "what should I use instead?", the recommended item is
the answer — it belongs in the hero position, not buried behind an arrow
after the thing the reader is trying to move away from.

- BAD: the card's headline named the **equipped** item; the recommended
  item only appeared as a secondary `→ {recommended item}` line.
- GOOD: the card's headline is the **recommended** item; the equipped
  item demotes to a muted `↳ replaces {equipped item}` line. The paperdoll
  header saw the same fix: `"Best in slot"` → `"Best available per slot"`
  (the old wording self-contradicted on any card showing an active swap).
- PRs #272/#273 (Gear-tab swap-card hierarchy flip).

### 9. Precise, non-misleading action labels

A button label or delta caption is a claim. If it isn't literally true for
every row it renders on, reword it rather than let it mislead on the rows
where it's wrong.

- BAD: `"Try {item}"` on a row that is a net **loss** vs. equipped gear —
  reads as a recommendation on a card that just said the opposite.
- GOOD: `"Trial anyway · {item}"` on those rows (power users can still
  trial it; nobody mistakes it for advice).
- BAD: `"{delta} eHP vs prior"` on the trial banner — the delta is
  actually computed against the *equipped* item, not whatever was trialed
  immediately before it.
- GOOD: `"{delta} eHP vs equipped"`.
- PR #272 (`"Trial anyway"`); PR #282 (`"vs equipped"`).

### 10. Tag non-live/sample data explicitly

Data that isn't the visitor's own must say so the instant it renders, or a
cold visitor reasonably reads it as a bug or a privacy leak.

- BAD: a bundled example log pre-selected in the Why-died picker with no
  signal it wasn't the visitor's data (a round-1 reviewer's reaction: "whose
  AnonTank1 is this?").
- GOOD: `sample_tag = " (sample)"` appended to the log label whenever
  `_is_bundled_example_log()` is true.
- PR #282 (log picker + cascading character picker).

---

## Trust-voice formula for shaky numbers

A specialized case of the voice above, worth its own section because it has
a proven, extractable shape — not a new rule, a name for a pattern this
project's best captions already use. Every caveat or caption attached to a
number the reader might not fully trust should hit **four beats, in order**:

| Beat | What it answers | If you skip it |
|------|------------------|-----------------|
| **Number** | What is the actual figure/claim, precisely stated? | The reader has nothing concrete to react to. |
| **Confidence** | How much should the reader trust it — is this precise, a bound, noisy? | A number reads as exact when it isn't, or vague when it's actually solid. |
| **Cause** | *Why* is it uncertain/bounded/noisy — what's the mechanism? | "Trust me" with no reason invites the reader to trust it either too much or not at all. |
| **Action** | What should the reader actually *do* about it? | The reader is left informed but stuck — the single most common failure mode below. |

A caveat that stops after Cause is common and looks complete, but it isn't:
the reader now understands the problem and still doesn't know what to do
next. Action is the beat that turns a disclaimer into something useful.

### Reference case: the death-rate noise-floor caption

`src/simf/ui/verdict.py`, immediately under the key-level sweep's bullet
list (`~lines 298-307`):

> "**±pp next to death%** is the sweep's own statistical noise floor at {N}
> iterations per key — two death-rate readings within that range aren't
> meaningfully different. **≤pp** on a 0.0% row means zero deaths were
> observed, not zero risk — it's a one-sided upper bound (rule of three),
> not a symmetric error bar. Re-run at a higher iteration count (Talent A/B
> panel) if you need to compare builds closer than this."

Annotated beat-by-beat:

1. **Number** — "±pp next to death%" / "≤pp on a 0.0% row": names exactly
   which figures on the screen this caveat is about.
2. **Confidence** — "aren't meaningfully different": tells the reader two
   readings inside that band are statistically indistinguishable, not that
   one is better than the other.
3. **Cause** — "the sweep's own statistical noise floor at {N} iterations
   per key" / "a one-sided upper bound (rule of three)": names the actual
   mechanism (finite sample size; the edge case where zero observed deaths
   is a bound, not a point estimate).
4. **Action** — "Re-run at a higher iteration count (Talent A/B panel) if
   you need to compare builds closer than this": a concrete next step,
   naming the exact control (which panel) to use.

A second worked example in the same voice: `src/simf/ui/log_cd_plan.py`
(`~lines 533-543`), which pairs "a very high death rate here can reflect
either genuine danger or a known modeling gap" (Confidence + Cause) with
"Cross-check against **Where you died** above before trusting the absolute
number" (Action) — same four beats, different surface.

### Counter-example: the Guardian per-spec caveat

`src/simf/ui/state.py`, `_SPEC_MODELING_CAVEAT["guardian_druid"]`
(`~lines 544-553`) ends:

> "A first held-out cross-validation run (2026-07-07) landed just under
> the promotion bar — under investigation, not yet resolved either way."

This has Number ("landed just under the promotion bar") and Cause/Confidence
("a first held-out cross-validation run," "under investigation") — but it
stops there. "Not yet resolved either way" is a status, not an action: it
tells the reader nothing they can *do* with this information. Compare to
`_uncalibrated_spec_warning()`'s own `"characterized"` branch a few lines
above (`~lines 509-517`), which reaches "Gear A vs gear B comparisons within
this spec are still useful" — a real Action, if a fairly soft one (see
below).

Note the `"characterized"` branch is itself only a partial success: Number
+ Confidence + Cause are all present and clear, but its Action ("still
useful") is the weakest form this beat can take — true, but it doesn't tell
the reader *when* to reach for gear A/B vs. when not to. A future pass on
that caption should tighten the Action beat, not just add one where it's
missing entirely.

### Self-check

Before shipping a new caveat or caption about an uncertain number, confirm
in under a minute:

1. Is the number itself stated plainly, not just alluded to?
2. Does a sentence tell the reader how much to trust it (exact / bounded /
   noisy)?
3. Does a sentence name *why* — the actual mechanism, not just "it's
   complicated"?
4. Does it end with something the reader can concretely do next? If the
   caveat ends on a status ("under investigation," "not yet resolved")
   instead of a next step, it's missing the fourth beat.
