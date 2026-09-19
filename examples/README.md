# examples/ — real inputs for driving simf

A corpus of **real** character exports and combat logs, so agents (and humans)
can exercise simf with genuine data instead of only the built-in demo. The demo
proves the happy path renders; real inputs surface the bugs that actually bite.

There are two ways data enters simf, and this folder feeds both:
- **Paste a `/simc` export** → the "Load from /simc" path (gear + vault verdicts,
  stat weights, A/B compare). Use the `.simc` / `*-vault.txt` / `*-simc.txt` files.
- **Upload a `WoWCombatLog`** → the "Why did I die?" / log-analysis path (death
  reconstruction, mitigation uptime, per-segment risk). Use the `WoWCombatLog-*.txt`
  files.

When driving via Playwright MCP, `browser_file_upload` takes an **absolute path**
to one of these files.

## Character exports (`/simc`) — committed, portable

| Spec | Class | Best file (latest) | Use for |
|---|---|---|---|
| **Protection** | Warrior | `Brutoh Protection 2026-07-08-vault.txt` (12.1.0) | **The primary, calibrated tank.** Default for any tank flow — vault picks, gear, stat weights, A/B. Older Brutoh snapshots (`brutoh.simc`, `brutoh-284.simc`, `brutoh-288-vault.simc`, `brutoh-2026-06-10.simc`) exist for gear-progression / regression testing. `Bruttah …` is a near-duplicate variant. |
| **Guardian** | Druid | `AnonGuardian1 Guardian 2026-07-08-vault.txt`, `AnonGuardian2-guardian-druid.txt`, `AnonGuardian3.simc`, `anonguardian1-simc.txt` | A second tank spec — good for the **uncalibrated / "characterized"-tier UX** (does the app honestly warn it's not calibrated?) and elite cross-alt comparison. |
| **Vengeance** | Demon Hunter | `Lyney-vdh.txt` | Uncalibrated-spec UX; novice "brand-new VDH alt" (mode A). |
| **Brewmaster** | Monk | `anonbrewmaster1-monk-tank.txt` | Uncalibrated-spec UX; Stagger modelling caveats. |
| **Discipline** | Priest | `Drapris Discipline 2026-07-01-vault.txt` | **Healer** persona and healer/tank-coupling checks — NOT a tank. |

Don't hard-code which specs are calibrated here — that moves. Let the app's own
calibration banner (K + per-dungeon RMSE) be the source of truth; verifying that
banner is honest is itself a good test.

> Note: `brutoh-2026-05-29-mislabeled-guardian-body.txt` has a Guardian body
> under a header that reads "Brutoh - Protection" — a mislabel worth
> confirming; prefer `AnonGuardian3.simc` for a clean Guardian export.

## Combat logs (`WoWCombatLog-*.txt`) — LOCAL-ONLY (git-ignored)

These live on this dev box but are **not committed** (`examples/WoWCombatLog-*.txt`
and `examples/Logs/` are in `.gitignore` — too large for git). A fresh clone won't
have them; on this machine they're present.

- **Quick UI-flow test:** `WoWCombatLog-012526_203228.txt` (~122 KB) — small, fast
  to upload, exercises the log path end-to-end.
- **Realistic / heavier:** the `050626` / `051026` / `051526` / `051626` logs range
  from ~33 MB to ~239 MB. Streamlit's upload cap is 500 MB (`.streamlit/config.toml`),
  so they fit, but a 200 MB+ upload is slow through the browser — reach for these
  only when testing real-scale behaviour or the four-step log pipeline under load.

## Tips for agents
- Prefer the **latest** dated export for a spec unless you're specifically testing
  gear progression across snapshots.
- Uploading a real log then waiting out the `"Reading character from log…"` →
  `"…(step 4 of 4)…"` pipeline is the only way to judge death reconstruction —
  the demo can't show it.
- `Screenshot*.png` here are reference captures, not inputs.
