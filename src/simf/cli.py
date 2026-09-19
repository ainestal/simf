from dataclasses import replace
from pathlib import Path
from typing import Any

import typer
import yaml

from .core.character import Character
from .core.constants import DATA_DIR
from .core.profiles import load_damage_profile, load_healing_profile
from .core.runner import run_simulation
from .io.combat_log import parse_challenge_modes, summarize_run
from .io.log_replay import load_replay
from .io.simc_import import load_simc_file, simc_to_character_yaml
from .optimizer.stat_weights import SURVIVABILITY_METRICS, compute_stat_weights
from .reports.cli_report import render_ab, render_log_summary, render_stat_weights, render_summary

app = typer.Typer(help="simf — WoW retail tank damage mitigation simulator")


@app.callback()
def _main() -> None:
    """Reserved for future global options."""


@app.command()
def run(
    character: str = typer.Option(
        str(DATA_DIR / "characters" / "example_warrior.yaml"),
        help="Character YAML path",
    ),
    damage_profile: str = typer.Option("m+_pull_caster", help="Damage profile name or path"),
    healing_profile: str = typer.Option("m+_high_key_healer", help="Healing profile name or path"),
    iterations: int = typer.Option(2000, help="Max Monte Carlo iterations"),
    target_error: float = typer.Option(
        0.0,
        help="Stop early when relative std-err of mean DTPS drops below this (0 = run all iterations)",
    ),
    seed: int = typer.Option(42),
    ab: str | None = typer.Option(
        None, help="A/B mode: 'earthen' to compare with/without Earthen racial"
    ),
):
    """Run a survivability simulation."""
    char = load_character(character)
    dmg = load_damage_profile(damage_profile)
    heal = load_healing_profile(healing_profile)

    if ab == "earthen":
        char_a = replace(char, race="human") if char.race == "earthen" else char
        char_b = replace(char_a, race="earthen")
        result_a = run_simulation(
            char_a, dmg, heal, iterations=iterations, seed=seed, target_error=target_error
        )
        result_b = run_simulation(
            char_b, dmg, heal, iterations=iterations, seed=seed, target_error=target_error
        )
        render_summary(result_a, char_a, damage_profile, healing_profile)
        render_summary(result_b, char_b, damage_profile, healing_profile)
        render_ab(result_a, char_a.race, result_b, char_b.race)
    else:
        result = run_simulation(
            char, dmg, heal, iterations=iterations, seed=seed, target_error=target_error
        )
        render_summary(result, char, damage_profile, healing_profile)


@app.command(name="stat-weights")
def stat_weights(
    character: str = typer.Option(
        str(DATA_DIR / "characters" / "example_warrior.yaml"),
        help="Character YAML path",
    ),
    damage_profile: str = typer.Option("m+_pull_caster", help="Damage profile name or path"),
    healing_profile: str = typer.Option("m+_high_key_healer", help="Healing profile name or path"),
    iterations: int = typer.Option(1000, help="Iterations per perturbation"),
    delta: int = typer.Option(1000, help="Rating perturbation magnitude per stat"),
    seed: int = typer.Option(42),
    metric: str = typer.Option(
        "p99_10s_window",
        help=f"Survivability metric to weight against. One of: {', '.join(SURVIVABILITY_METRICS)}",
    ),
):
    """Compute survivability stat weights for each secondary."""
    char = load_character(character)
    dmg = load_damage_profile(damage_profile)
    heal = load_healing_profile(healing_profile)

    weights = compute_stat_weights(
        char,
        dmg,
        heal,
        delta_rating=delta,
        iterations=iterations,
        seed=seed,
        metric=metric,
    )
    render_stat_weights(weights, char, metric, iterations, delta)


def _safe_public_bind(value: str) -> str:
    """Sanitise a requested public bind address, failing CLOSED to loopback.

    Allows IPv4 (incl. ``0.0.0.0`` — only reaches the private LAN on a NAT'd
    home network) and IPv6 loopback (``::1``). REFUSES any non-loopback IPv6
    such as ``::``: the box carries a global IPv6 with ``bindv6only=0``, so
    binding it would listen on that public address and expose the instance to
    the internet directly, defeating the tunnel-only threat model. Unparseable
    or empty values also fall back to loopback. A comment-only warning isn't a
    boundary — this is (security review, 2026-06-14)."""
    import ipaddress

    raw = (value or "").strip()
    if not raw:
        return "127.0.0.1"
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        typer.echo(
            f"SIMF_PUBLIC_BIND={raw!r} is not an IP address — using 127.0.0.1.",
            err=True,
        )
        return "127.0.0.1"
    if ip.version == 6 and not ip.is_loopback:
        typer.echo(
            f"SIMF_PUBLIC_BIND={raw!r} is a non-loopback IPv6 — refusing (it would "
            "expose the box on its global IPv6, bypassing the tunnel). Using "
            "127.0.0.1. Use 0.0.0.0 for LAN access.",
            err=True,
        )
        return "127.0.0.1"
    return raw


def _public_streamlit_flags(host: str = "", bind: str = "127.0.0.1") -> list[str]:
    """Streamlit CLI flags for the locked-down PUBLIC instance behind a
    Cloudflare Tunnel (Phase 5, 2026-06-13). Passed as flags rather than
    committed config so the owner's `.streamlit/config.toml` (500 MB upload
    cap, theme) is left untouched.

    The WebSocket / CORS / XSRF trio is REQUIRED behind a reverse-proxy tunnel
    — without it Streamlit's socket handshake fails and the app renders blank
    (the single most common "the tunnel doesn't work" failure). The 25 MB cap,
    minimal toolbar (hides the Deploy button), and showErrorDetails=false are
    hardening — the log uploader is already hidden in SIMF_PUBLIC mode, so this
    is belt-and-suspenders.

    ``bind`` is the interface to listen on (default ``127.0.0.1`` — loopback
    only, so the box is reachable solely via the Cloudflare Tunnel, never the
    LAN). Set ``$SIMF_PUBLIC_BIND=0.0.0.0`` to ALSO serve the local network
    (e.g. another computer at ``http://<pi-ip>:8501``) — that trades the
    tunnel's edge WAF/rate-limit for direct LAN reach, fine on a trusted home
    network for a read-only, DoS-guarded app. The value is sanitised by
    :func:`_safe_public_bind`, which fails CLOSED to loopback and refuses a
    non-loopback IPv6 (``::``) — that would bind the box's global IPv6 and
    expose it to the internet directly, bypassing the tunnel.

    ``host`` (``$SIMF_PUBLIC_HOST``) sets ``browser.serverAddress`` to pin the
    client's WebSocket origin. The prod unit pins it to the tunnel hostname;
    for LAN access UNSET it (empty) so the client uses same-origin — i.e. LAN =
    ``SIMF_PUBLIC_BIND=0.0.0.0`` AND an empty ``SIMF_PUBLIC_HOST`` (the deploy
    drop-in does both). Same-origin then serves localhost, the LAN, and the
    tunnel from whatever address the browser actually used."""
    flags = [
        # Bind address — loopback by default (tunnel-only, no LAN). Override via
        # $SIMF_PUBLIC_BIND for LAN access (sanitised, fails closed to loopback).
        # Overrides the shipped config's 0.0.0.0.
        "--server.address",
        _safe_public_bind(bind),
        "--server.enableCORS",
        "false",
        "--server.enableXsrfProtection",
        "false",
        "--server.enableWebsocketCompression",
        "false",
        "--server.maxUploadSize",
        "25",
        "--client.showErrorDetails",
        "false",
        "--client.toolbarMode",
        "minimal",
    ]
    if host:
        flags += ["--browser.serverAddress", host]
    return flags


@app.command(name="ui")
def ui(
    port: int = typer.Option(8501, help="Port to run the Streamlit server on"),
    headless: bool = typer.Option(False, help="Suppress browser auto-open"),
    reload: bool = typer.Option(
        False,
        "--reload",
        help="Dev hot-reload: watch source files (poll-based) and rerun on save. "
        "Off by default — deployments (owner/public) run without a watcher. "
        "Uses the 'poll' watcher, not watchdog, which needs inotify/_ctypes.",
    ),
    public: bool = typer.Option(
        False,
        "--public",
        help="Locked-down read-only PUBLIC instance for the Cloudflare Tunnel "
        "deploy: sets SIMF_PUBLIC=1 + the WebSocket/CORS/XSRF tunnel flags + a "
        "25 MB upload cap + a hidden toolbar. Implies --headless.",
    ),
    host: str = typer.Option(
        "",
        "--host",
        help="Public hostname for --public (browser.serverAddress) — required "
        "behind the tunnel or the WebSocket connects to the wrong origin. "
        "Falls back to $SIMF_PUBLIC_HOST.",
    ),
):
    """Launch the Streamlit web UI for interactive simulation.

    Requires the 'ui' extra:  pip install -e ".[ui]"

    `--public` runs the locked-down instance meant to sit behind a reverse
    proxy / tunnel of your choosing.
    """
    try:
        import streamlit  # noqa: F401
    except ImportError:
        typer.echo(
            'Streamlit is not installed. Install the "ui" extra:\n  pip install -e ".[ui]"',
            err=True,
        )
        raise typer.Exit(1) from None

    import os
    import subprocess
    import sys

    from ._worktree import worktree_src_dir

    # When `simf ui` is invoked from inside a `.worktrees/` checkout the
    # parent's .venv editable install still serves the parent's
    # src/simf, so changes made in the worktree are invisible to
    # Streamlit. If we're in a worktree, prepend its src/ onto
    # PYTHONPATH and launch app.py from that copy. No-op from the
    # parent repo (worktree_src_dir() returns None).
    wt_src = worktree_src_dir()
    env: dict[str, str] | None
    if wt_src is not None:
        app_path = wt_src / "simf" / "ui" / "app.py"
        env = os.environ.copy()
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{wt_src}{os.pathsep}{existing}" if existing else str(wt_src)
        typer.echo(f"Detected worktree; serving UI from {wt_src}")
    else:
        app_path = Path(__file__).resolve().parent / "ui" / "app.py"
        env = None

    if public:
        if env is None:
            env = os.environ.copy()
        env["SIMF_PUBLIC"] = "1"
        headless = True  # a public server never auto-opens a browser

    # Watcher choice: 'poll' for dev hot-reload (--reload), else 'none'. We use
    # 'poll' rather than 'auto'/'watchdog' because watchdog's inotify observer
    # needs _ctypes (historically missing on this box, commit ddcf845) and
    # inotify watch limits bite on a Pi; polling avoids both. A --public/owner
    # deployment never sets --reload, so it keeps the watcher off.
    watcher = "poll" if (reload and not public) else "none"
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app_path),
        "--server.port",
        str(port),
        "--server.fileWatcherType",
        watcher,
    ]
    if public:
        cmd += _public_streamlit_flags(
            host or os.environ.get("SIMF_PUBLIC_HOST", ""),
            os.environ.get("SIMF_PUBLIC_BIND") or "127.0.0.1",
        )
    if headless:
        cmd += ["--server.headless", "true"]
    label = "PUBLIC (read-only) " if public else ""
    typer.echo(f"Launching simf {label}UI on http://localhost:{port}")
    subprocess.run(cmd, env=env)  # noqa: S603 — list-args, no shell=True, all operator-controlled (typer options/env)


@app.command(name="replay-log")
def replay_log(
    log_path: str = typer.Argument(..., help="Path to a WoWCombatLog-*.txt file"),
    character: str = typer.Option(
        # Frozen May-2026 snapshot: the bundled log corpus is May-era, and the
        # replay baselines were measured against that stat block.
        str(DATA_DIR / "characters" / "brutoh-calibration-2026-05.yaml"),
        help="Character YAML path (sim character config)",
    ),
    log_target: str = typer.Option(
        "Brutoh-Uldum-EU",
        help="Target name as it appears in the log",
    ),
    healing_profile: str = typer.Option("m+_high_key_healer"),
    run_index: int = typer.Option(-1, help="Which CHALLENGE_MODE run (-1 = last)"),
    iterations: int = typer.Option(50, help="Iterations (RNG-only — events are deterministic)"),
    seed: int = typer.Option(42),
    ab: str | None = typer.Option(
        None, help="A/B mode: 'earthen' to compare with/without Earthen racial"
    ),
):
    """Replay actual damage events from a combat log through our mitigation engine.

    This is the ground-truth validator: feeds the real damage stream from a logged
    M+ run into our sim and reports what damage WOULD have hit our HP given the
    character config — comparable to the actual damage from the log.
    """
    from dataclasses import replace as dc_replace

    char = load_character(character)
    heal = load_healing_profile(healing_profile)
    replay = load_replay(Path(log_path), log_target, run_index)

    typer.echo(
        f"Replay: {replay.run.map_name} +{replay.run.key_level}, "
        f"{replay.event_count:,} events, "
        f"{int(replay.duration_s // 60)}:{int(replay.duration_s % 60):02d} duration"
    )
    typer.echo(
        f"Actual log dealt damage: {replay.actual_dealt:,} "
        f"({replay.actual_dealt / replay.duration_s:,.0f} DTPS)"
    )

    heal = dc_replace(heal, baseline_hps_abs=replay.actual_dealt / replay.duration_s * 1.1)

    if ab == "earthen":
        char_a = dc_replace(char, race="human") if char.race == "earthen" else char
        char_b = dc_replace(char_a, race="earthen")
        result_a = run_simulation(
            char_a,
            None,
            heal,
            iterations=iterations,
            seed=seed,
            events_override=replay.events,
            duration_override=replay.duration_s,
        )
        result_b = run_simulation(
            char_b,
            None,
            heal,
            iterations=iterations,
            seed=seed,
            events_override=replay.events,
            duration_override=replay.duration_s,
        )
        render_summary(result_a, char_a, "log_replay", healing_profile)
        render_summary(result_b, char_b, "log_replay", healing_profile)
        render_ab(result_a, char_a.race, result_b, char_b.race)
    else:
        result = run_simulation(
            char,
            None,
            heal,
            iterations=iterations,
            seed=seed,
            events_override=replay.events,
            duration_override=replay.duration_s,
        )
        render_summary(result, char, "log_replay", healing_profile)


@app.command(name="analyze-log")
def analyze_log(
    log_path: str = typer.Argument(..., help="Path to a WoWCombatLog-*.txt file"),
    character: str = typer.Option(
        "Brutoh-Uldum-EU",
        help="Character name as it appears in the log (Name-Server-Region)",
    ),
    run_index: int = typer.Option(
        -1, help="Index of the CHALLENGE_MODE run to analyze (-1 = last)"
    ),
    top: int = typer.Option(10, help="Top N rows to show in source/ability tables"),
):
    """Analyze damage taken on a tank during a Mythic+ run from a raw combat log."""
    path = Path(log_path)
    runs = parse_challenge_modes(path)
    if not runs:
        typer.echo("No CHALLENGE_MODE_START / CHALLENGE_MODE_END pair found in log.")
        raise typer.Exit(1)

    # List runs
    typer.echo(f"Found {len(runs)} M+ run(s) in log:")
    for i, r in enumerate(runs):
        dur = r.duration_s()
        typer.echo(
            f"  [{i}] {r.map_name} +{r.key_level} affixes={r.affixes} "
            f"duration={int(dur // 60)}:{int(dur % 60):02d} "
            f"result={'timed' if r.success else 'depleted' if r.success is False else '?'}"
        )

    if run_index < 0:
        run_index = len(runs) + run_index
    if not (0 <= run_index < len(runs)):
        typer.echo(f"Invalid run_index {run_index}")
        raise typer.Exit(1)

    typer.echo(f"\nAnalyzing run [{run_index}]...")
    summary = summarize_run(path, character, runs[run_index])
    render_log_summary(summary, character, top_n=top)


def _load_measure_run_f():
    """Dynamically load ``measure_run_f`` from scripts/measure_run_f.py.

    That module is the diagnostic per-run F (base-to-applied damage
    multiplier) estimator behind the F-consistency gate already used by
    ``scripts/calibrate_spec_from_logs.py`` for every non-warrior spec (see
    its own docstring, and ``docs/validation/
    phase4_brewmaster_physical_gap_decomposition_2026_07_04.md``). It lives
    outside ``src/`` by deliberate convention — ``tests/test_measure_run_f.py``
    loads it the same way, by file path, rather than as an installed package
    module — so this mirrors that, instead of restructuring the package
    layout for one diagnostic helper.

    Returns ``None`` (never raises) when the script isn't present next to
    this checkout — e.g. a packaged deploy that ships only ``src/simf`` —
    matching ``measure_run_f``'s own "no evidence" contract: F reporting is
    diagnostic-only (see ``calibrate_k``'s docstring) and must never block
    the K sweep it annotates.
    """
    import importlib.util
    import sys

    script_path = Path(__file__).resolve().parents[2] / "scripts" / "measure_run_f.py"
    if not script_path.exists():
        return None
    mod_name = "_simf_cli_measure_run_f"
    spec = importlib.util.spec_from_file_location(mod_name, script_path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    # Must register in sys.modules BEFORE exec_module: measure_run_f.py
    # declares `@dataclass(frozen=True)` fields, and dataclasses resolves
    # forward-ref/KW_ONLY checks via `sys.modules[cls.__module__]` while the
    # class body is still executing — skipping this step raises
    # AttributeError deep inside dataclasses (module registered too late),
    # exactly the failure tests/test_measure_run_f.py's own loader avoids by
    # doing the same registration first.
    sys.modules[mod_name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        del sys.modules[mod_name]
        return None
    return getattr(mod, "measure_run_f", None)


def _load_run_loo_cv():
    """Dynamically load ``_run_loo_cv`` from scripts/calibrate_spec_from_logs.py,
    the same way ``_load_measure_run_f`` above loads its sibling helper — see
    that function's docstring for why this lives outside ``src/`` and why the
    loader is file-path-based rather than a package import.

    ``_run_loo_cv`` (Top-5 #4, 2026-07-06 retrospective) is pure
    post-processing of a ``{k: (rmse, deltas)}`` table — zero new simulation
    runs — so reusing it here rather than re-implementing the same gate a
    second time keeps exactly one leave-one-out implementation in the repo,
    the one ``docs/calibration.md`` and every promotion-bar doc already point
    at by name.

    Returns ``None`` (never raises) on a checkout without ``scripts/``, same
    graceful-degradation contract as ``_load_measure_run_f``.
    """
    import importlib.util
    import sys

    script_path = Path(__file__).resolve().parents[2] / "scripts" / "calibrate_spec_from_logs.py"
    if not script_path.exists():
        return None
    mod_name = "_simf_cli_calibrate_spec_from_logs"
    spec = importlib.util.spec_from_file_location(mod_name, script_path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        del sys.modules[mod_name]
        return None
    return getattr(mod, "_run_loo_cv", None)


@app.command(name="calibrate-k")
def calibrate_k(
    character: str = typer.Option(
        # Frozen May-2026 snapshot — K=3430 / RMSE 0.068 are anchored to it.
        # Sweeping K against a later gear state silently shifts the baseline.
        str(DATA_DIR / "characters" / "brutoh-calibration-2026-05.yaml"),
        help="Character YAML path",
    ),
    healing_profile: str = typer.Option("m+_high_key_healer"),
    log_target: str = typer.Option("Brutoh-Uldum-EU", help="Target name in logs"),
    logs_dir: str = typer.Option("examples", help="Directory containing WoWCombatLog-*.txt files"),
    corpus_manifest: str | None = typer.Option(
        None,
        help=(
            "Path to a versioned calibration-corpus manifest (data/calibration_corpora/*.yaml) "
            "pinning an exact file[run_index] list. Defaults to auto-detecting a manifest whose "
            "'character' field matches --character; pass --full-scan to bypass manifests entirely."
        ),
    ),
    full_scan: bool = typer.Option(
        False,
        "--full-scan",
        help=(
            "Recursively scan --logs-dir for ANY log containing --log-target, instead of using a "
            "ratified corpus manifest for the REPLAY LIST. This is how the 2026-05-18 Prot Warrior "
            "baseline (16 logs, RMSE 0.068) silently grew to 79 replays / RMSE 0.124 with no engine "
            "change — an explicit opt-in now, for exploring new logs before they earn their own "
            "manifest. A manifest matching --character (if any) is still consulted for its gear-"
            "snapshot date, to warn on scanned logs that drifted far from it — this flag skips "
            "manifest-based replay SELECTION, not that one hygiene check."
        ),
    ),
    wcl_url: str | None = typer.Option(
        None,
        help=(
            "Optional WCL report URL (e.g. https://www.warcraftlogs.com/reports/abc123#fight=4). "
            "When given, --logs-dir is ignored and the K sweep runs against the single WCL fight. "
            "Pair with --wcl-target for the character name as it appears in the report."
        ),
    ),
    wcl_target: str | None = typer.Option(
        None,
        help="Character name in the WCL report (with or without realm suffix). Required with --wcl-url.",
    ),
    wcl_cache_dir: str | None = typer.Option(
        None,
        help=(
            "Optional opt-in on-disk cache directory for --wcl-url fetches (see "
            "io.wcl_api._gql's docstring). Zero staleness risk — a past fight's data "
            "never changes. Omit for the default, uncached, always-fresh behaviour."
        ),
    ),
    k_min: int = typer.Option(2000, help="Minimum K to test"),
    k_max: int = typer.Option(12000, help="Maximum K to test"),
    k_step: int = typer.Option(250, help="K step size"),
    iterations: int = typer.Option(300, help="Iterations per K value (lower = faster)"),
    seed: int = typer.Option(42),
):
    """Calibrate the armor K constant against real WoW combat logs.

    Sweeps K values and finds the one that minimizes DTPS error vs actual log data.
    Run this after any mechanical fix that changes mitigation math, then update
    k_constant in data/constants.yaml with the result.

    On the local-log path (not --wcl-url, which has no per-hit live armor),
    each replay is also annotated with its diagnostic F — the per-run
    base-to-applied damage multiplier from ``scripts/measure_run_f.py`` (see
    ``docs/validation/phase4_brewmaster_physical_gap_decomposition_2026_07_04.md``
    and ``docs/validation/protwarrior_demo_shout_double_count_2026_07_17.md``
    for what F is and why it matters). This is reporting-only: F is never
    baked into constants.yaml, never changes the sim's damage math, and
    never gates the printed RMSE/best-K — it exists so a human can judge
    whether the corpus's own residual is F-consistent (a real modeling gap)
    or dominated by a per-run, un-modelable mob-side multiplier.
    """
    from .io.log_replay import load_replay

    char = load_character(character)
    heal = load_healing_profile(healing_profile)

    # --- WCL fight branch ----------------------------------------------
    # When --wcl-url is given we skip the rglob entirely and build a
    # single-entry ``replays`` list from a remote WCL fight. The K sweep
    # math is identical; the only difference is the event source.
    if wcl_url is not None:
        if not wcl_target:
            typer.echo("--wcl-target is required when --wcl-url is given.")
            raise typer.Exit(2)
        from .io.wcl_api import url_to_code_and_fight, url_to_source_id
        from .io.wcl_replay import wcl_to_replay_data

        report_code, default_fight_id = url_to_code_and_fight(wcl_url)
        if default_fight_id is None:
            typer.echo(
                "Could not find #fight=N in the WCL URL. "
                "Append e.g. '#fight=4' to select a single fight."
            )
            raise typer.Exit(2)
        source_actor_id = url_to_source_id(wcl_url)

        # Prefer a GEAR-CERTAIN character built from the report's own
        # CombatantInfo (the gear actually worn in this fight) over the
        # provided --character YAML. WCL surfaces gear + exact stats + talents
        # via events(CombatantInfo) when ACL was on. Falls back to --character
        # with a loud notice when the report has no CombatantInfo (ACL off) —
        # in that case the gear may not match the fight, so the result is only
        # characterization-grade. See io/wcl_combatant_info.py.
        from .io import item_db
        from .io.wcl_combatant_info import character_from_wcl

        _wcl_cache_path = Path(wcl_cache_dir) if wcl_cache_dir else None
        wcl_char = character_from_wcl(
            report_code,
            default_fight_id,
            wcl_target,
            target_actor_id=source_actor_id,
            resolve_stats_fn=item_db.resolve_equipped_stats,
            cache_dir=_wcl_cache_path,
        )
        if wcl_char is not None:
            char = Character.from_dict(wcl_char.char_data)
            typer.echo(f"  Gear-certain ({wcl_char.source}): {wcl_char.summary}")
        else:
            typer.echo(
                "  WARNING: report has no CombatantInfo (ACL off) — using --character "
                "gear, which may NOT match the fight (characterization-grade only)."
            )

        # Window-gated mitigation layers (VDH Metamorphosis ×3 armor,
        # Painbringer) are credited off event.active_buffs, stamped from the
        # log's real buff windows. Collect the spec's `*_spell_id` constants —
        # the same convention scripts/calibrate_spec_from_wcl.py uses — so the
        # shipped path credits the same layers as the characterization tooling
        # (omitting this silently zeroes those layers and skews the K read).
        from .core.constants import load_constants

        spec_cfg = load_constants().get("specs", {}).get(char.class_spec, {})
        buff_ids = {int(v) for k, v in spec_cfg.items() if k.endswith("_spell_id") and v}

        try:
            replay = wcl_to_replay_data(
                report_code,
                default_fight_id,
                wcl_target,
                target_actor_id=source_actor_id,
                buff_ability_ids=buff_ids or None,
                cache_dir=_wcl_cache_path,
            )
        except Exception as e:
            typer.echo(f"Failed to fetch WCL fight: {e}")
            raise typer.Exit(1) from e
        if replay.event_count == 0:
            typer.echo(
                f"WCL fight {report_code}#fight={default_fight_id} returned 0 events for {wcl_target}."
            )
            raise typer.Exit(1)
        real_dtps = replay.actual_dealt / replay.duration_s
        label = f"wcl:{report_code}#{default_fight_id}"
        replays = [(replay, real_dtps, label, char)]
        typer.echo(
            f"  Loaded {label}: {replay.run.map_name} +{replay.run.key_level}, "
            f"real DTPS={real_dtps:,.0f}, events={replay.event_count:,}"
        )
        # Skip the local-log scan; fall through to the K sweep with the
        # WCL-built ``replays`` list.
        return _run_k_sweep(
            replays=replays,
            heal=heal,
            k_min=k_min,
            k_max=k_max,
            k_step=k_step,
            iterations=iterations,
            seed=seed,
        )

    logs_path = Path(logs_dir)

    import functools
    from dataclasses import replace as dc_replace

    from .core.constants import load_constants
    from .io.calibration_corpus import (
        filename_embedded_date,
        find_manifest_for_character,
        load_calibration_corpus,
    )
    from .io.combat_log import iter_combatant_info, parse_challenge_modes

    # Diagnostic F (base-to-applied damage multiplier) — see calibrate_k's
    # own docstring above. Loaded once; None on a checkout without scripts/
    # (e.g. a packaged deploy), in which case every run reports "F=n/a" and
    # the sweep proceeds exactly as before this feature existed.
    _measure_run_f = _load_measure_run_f()
    _canon_k_for_f = load_constants()["armor"]["k_constant"]
    f_measurements: dict[str, Any] = {}  # label -> FMeasurement | None

    @functools.cache
    def _target_talent_spell_ids(log_path: Path, target_name: str) -> frozenset[int] | None:
        """Return the talent spell IDs for target_name in this log file.

        Two-pass: first collect COMBATANT_INFO guid→talent_spell_ids, then
        scan damage events to find destGUID for target_name, then look up.
        Cached per (file, target) — a manifest commonly lists several
        run_index values from the same file.
        """
        guid_to_talents: dict[str, frozenset[int]] = {
            guid: ts for guid, _sid, ts in iter_combatant_info(log_path)
        }
        if not guid_to_talents:
            return None
        # Scan damage events for the first occurrence of target_name as dest
        # to resolve name→GUID. WoW log format: destGUID=fields[4], destName=fields[5].
        try:
            with log_path.open() as f:
                for line in f:
                    if f",{target_name}," not in line:
                        continue
                    sep = line.find("  ")
                    if sep < 0:
                        continue
                    rest = line[sep + 2 :].strip()
                    parts = rest.split(",")
                    # dest is at parts[5] (0-indexed event fields: type,srcGUID,srcName,srcFlags,srcRaidFlags,dstGUID,dstName,...)
                    if len(parts) > 6 and parts[6] == target_name:
                        guid = parts[5]
                        if guid in guid_to_talents:
                            return guid_to_talents[guid]
        except OSError:
            pass
        return None

    # Window-gated mitigation layers (e.g. Prot Warrior's Keep Your Feet on
    # the Ground) are credited off event.active_buffs, stamped from the
    # log's real buff windows. Collect the spec's `*_spell_id` constants —
    # same convention as the --wcl-url branch above — so the local-log path
    # credits the same layers; omitting this would silently zero them (the
    # exact bug class fixed for VDH's --wcl-url Metamorphosis credit, see
    # docs/validation/phase4_vdh_characterization_2026_06_09.md).
    _local_spec_cfg = load_constants().get("specs", {}).get(char.class_spec, {})
    _local_buff_ids = {int(v) for k, v in _local_spec_cfg.items() if k.endswith("_spell_id") and v}

    runs_cache: dict[Path, list] = {}

    def _runs_for(lf: Path) -> list:
        """parse_challenge_modes(lf), cached — a manifest or a scan can both
        touch the same (potentially ~1GB) file more than once per run."""
        if lf not in runs_cache:
            runs_cache[lf] = parse_challenge_modes(lf)
        return runs_cache[lf]

    def _build_replay_entry(lf: Path, run_index: int):
        """Load one (file, run_index) into a (replay, real_dtps, label, run_char)
        tuple, echoing status. Returns None (and echoes why) if unusable.
        Applies the same failed-run/no-damage-events/exception handling
        whether the entry came from a ratified manifest or a directory scan
        — a manifest entry is not exempt from these checks."""
        try:
            runs = _runs_for(lf)
            if not runs:
                typer.echo(f"  Skipping {lf.name}[{run_index}]: No CHALLENGE_MODE runs in {lf}")
                return None
            replay = load_replay(
                lf,
                log_target,
                run_index=run_index,
                runs=runs,
                buff_ability_ids=_local_buff_ids or None,
            )
            if replay.run.success is False:
                typer.echo(
                    f"  Skipping {lf.name}[{run_index}] {replay.run.map_name} +{replay.run.key_level}: "
                    "failed run (death downtime)"
                )
                return None
            if replay.event_count == 0:
                typer.echo(
                    f"  Skipping {lf.name}[{run_index}] {replay.run.map_name} +{replay.run.key_level}: "
                    f"no damage events for {log_target}"
                )
                return None
            real_dtps = replay.actual_dealt / replay.duration_s
            tag = "" if replay.run.success else " [partial]"

            # Per-run character: inject log-detected talent spell IDs so
            # total_armor() picks up reinforced_plates / armor_specialization
            # from the log instead of the YAML talent loadout.
            target_ts = _target_talent_spell_ids(lf, log_target)
            run_char = (
                dc_replace(char, detected_talent_spell_ids=target_ts)
                if target_ts is not None
                else char
            )

            label = f"{lf.name}[{run_index}]"
            typer.echo(
                f"  Loaded {label}{tag}: {replay.run.map_name} +{replay.run.key_level}, "
                f"real DTPS={real_dtps:,.0f}"
            )

            f_meas = None
            if _measure_run_f is not None:
                try:
                    f_meas = _measure_run_f(
                        lf,
                        log_target,
                        start_time_s=replay.run.start_time_s,
                        end_time_s=replay.run.end_time_s,
                        start_byte_offset=replay.run.start_byte_offset,
                        k=_canon_k_for_f,
                        vers=run_char.versatility_dr(),
                    )
                except Exception as e:
                    typer.echo(f"     F measurement failed ({e}) — treating as n/a")
            f_measurements[label] = f_meas
            f_str = (
                f"F={f_meas.median_f:.3f} (n={f_meas.n_hits}, IQR {f_meas.p25:.3f}-{f_meas.p75:.3f})"
                if f_meas is not None
                else "F=n/a (no per-hit live-armor data — ACL off, or a WCL import)"
            )
            typer.echo(f"     {f_str}")
            return (replay, real_dtps, label, run_char)
        except Exception as e:
            typer.echo(f"  Skipping {lf.name}[{run_index}]: {e}")
            return None

    corpus_scan_cfg = load_constants().get("calibration", {}).get("corpus_scan", {})
    _DEDUP_CHUNK_BYTES = corpus_scan_cfg.get("dedup_hash_chunk_bytes", 65536)
    _DATE_DRIFT_WARN_DAYS = corpus_scan_cfg.get("date_drift_warn_days", 30)

    def _content_signature(lf: Path) -> tuple[int, str]:
        """Cheap exact-duplicate fingerprint: size + hash of head/tail chunk.

        Full-file sha256 would be correct too but costs a second full read of
        files already up to ~1GB; head+tail is enough to catch the confirmed
        byte-identical duplicates (same file copied into a subdirectory) this
        check exists for, without doubling I/O on the largest logs.
        """
        import hashlib

        n = _DEDUP_CHUNK_BYTES
        size = lf.stat().st_size
        with lf.open("rb") as f:
            head = f.read(n)
            tail = b""
            if size > n:
                f.seek(-n, 2)
                tail = f.read(n)
        return (size, hashlib.sha256(head + tail).hexdigest())

    def _scan_replays_hardened(snapshot_date) -> list:
        """Opt-in recursive scan (--full-scan or no manifest for this character).

        Hardened against the exact failure modes the 2026-07-12 RMSE-drift
        investigation found: byte-identical duplicate files (deduped by content
        signature), logs dated far from the calibration character's gear
        snapshot (warned, not silently included — residuals there reflect gear
        drift, not model error), and replays sourced from a subdirectory that
        may be another character's own dedicated corpus (flagged for review —
        NOT a full role check; a target can legitimately co-tank inside
        another character's corpus dir, so this is a prompt to verify by eye,
        not an exclusion).
        """
        log_files = sorted(logs_path.rglob("*WoWCombatLog-*.txt"))
        if not log_files:
            typer.echo(f"No *WoWCombatLog-*.txt found in {logs_dir} (searched recursively)")
            raise typer.Exit(1)

        seen_signatures: dict[tuple[int, str], Path] = {}
        out = []
        for lf in log_files:
            try:
                sig = _content_signature(lf)
            except OSError as e:
                typer.echo(f"  Skipping {lf}: {e}")
                continue
            if sig in seen_signatures:
                typer.echo(f"  Skipping {lf}: duplicate content of {seen_signatures[sig]}")
                continue
            seen_signatures[sig] = lf

            try:
                rel = lf.relative_to(logs_path)
            except ValueError:
                rel = lf
            if len(rel.parts) > 1:
                typer.echo(
                    f"  Note: {lf} is in subdirectory '{rel.parts[0]}/' — verify this isn't "
                    "another character's dedicated calibration corpus before trusting its residual."
                )

            log_date = filename_embedded_date(lf.name)
            if snapshot_date is not None and log_date is not None:
                delta_days = abs((log_date - snapshot_date).days)
                if delta_days > _DATE_DRIFT_WARN_DAYS:
                    typer.echo(
                        f"  Warning: {lf.name} is {delta_days} days from the calibration character's "
                        f"gear snapshot ({snapshot_date}) — its residual may reflect gear drift, not "
                        "model error."
                    )

            try:
                runs = _runs_for(lf)
            except Exception as e:
                typer.echo(f"  Skipping {lf.name}: {e}")
                continue
            if not runs:
                typer.echo(f"  Skipping {lf.name}: No CHALLENGE_MODE runs in {lf}")
                continue

            for i in range(len(runs)):
                entry = _build_replay_entry(lf, i)
                if entry is not None:
                    out.append(entry)
        return out

    # Resolved once and shared: the manifest-selection decision below and the
    # hardened scan's gear-drift date check (which applies even under
    # --full-scan — that flag skips manifest-based REPLAY SELECTION, not the
    # snapshot-date hygiene check, since a manifest happening to document
    # this character's gear date is useful context regardless of which path
    # supplies the actual replay list) both need it; computing it twice would
    # re-glob and re-parse every file in data/calibration_corpora/ for the
    # same answer.
    own_manifest = find_manifest_for_character(character)

    manifest_path = Path(corpus_manifest) if corpus_manifest else None
    if manifest_path is None and not full_scan:
        manifest_path = own_manifest

    replays = []  # (replay, real_dtps, label, char_for_run)
    if manifest_path is not None:
        corpus = load_calibration_corpus(manifest_path)
        typer.echo(
            f"Using ratified corpus manifest: {manifest_path} ({len(corpus.replays)} replays)"
        )
        for filename, run_index in corpus.replays:
            lf = logs_path / filename
            if not lf.exists():
                typer.echo(
                    f"  Skipping {filename}[{run_index}]: file not found at {lf} "
                    "(manifest entry missing from --logs-dir)"
                )
                continue
            entry = _build_replay_entry(lf, run_index)
            if entry is not None:
                replays.append(entry)
    else:
        typer.echo(
            f"No ratified manifest found for this character — doing a full recursive scan of "
            f"{logs_dir}. Create one under data/calibration_corpora/ for a reproducible corpus."
        )
        snapshot_date = (
            load_calibration_corpus(own_manifest).character_snapshot_date if own_manifest else None
        )
        replays = _scan_replays_hardened(snapshot_date)

    if not replays:
        typer.echo("No usable logs found.")
        raise typer.Exit(1)

    _run_k_sweep(
        replays=replays,
        heal=heal,
        k_min=k_min,
        k_max=k_max,
        k_step=k_step,
        iterations=iterations,
        seed=seed,
        f_measurements=f_measurements,
    )


def _run_k_sweep(
    *,
    replays: list,
    heal,
    k_min: int,
    k_max: int,
    k_step: int,
    iterations: int,
    seed: int,
    f_measurements: dict[str, Any] | None = None,
    skip_loo_cv: bool = False,
    return_result: bool = False,
) -> dict[str, Any] | None:
    """Run the K sweep over ``replays`` (built by either log path).

    Extracted from ``calibrate_k`` so the WCL branch reuses the same
    sweep math — single source of truth for the per-K simulation, RMSE
    computation, and result table. ``replays`` is a list of tuples
    ``(replay, real_dtps, label, run_char)`` as the local-log path builds.

    ``f_measurements`` (label -> FMeasurement | None) is optional and
    local-log-only — the WCL branch never passes it, since a WCL fight
    carries no per-hit live-armor data for ``measure_run_f`` to read. When
    present, an extra diagnostic section prints an F-consistency check and
    an F-corrected residual at the canonical K (see ``calibrate_k``'s
    docstring) — reporting only, never fed back into the sweep itself.

    A leave-one-out cross-validation gate (``scripts/calibrate_spec_from_logs.py``'s
    ``_run_loo_cv``, the same implementation ``docs/calibration.md``'s
    `calibrated`-promotion bar names — reused rather than re-implemented, see
    ``_load_run_loo_cv``) always runs at the end (unless ``skip_loo_cv``),
    pure post-processing of the same per-K ``sims`` this sweep already
    computed. It needs the sweep's own best-fit K, not necessarily the
    canonical one, so the swept grid always includes the canonical K (unioned
    in below) purely so the F-layer section above can find it too — LOO-CV
    itself works off whatever grid results. ``skip_loo_cv=True`` is for
    callers whose ``replays`` aren't repeated runs of the same setup (e.g.
    ``scripts/cross_player_validation.py``'s one-fight-per-different-player
    corpus) — a leave-one-out fold there wouldn't test what LOO-CV is for.

    ``return_result=True`` additionally returns
    ``{"best_k", "best_rmse", "deltas": [...], "labels": [...]}`` (percent
    signed deltas, same order as ``replays``) instead of ``None`` — purely
    additive, every existing caller (default ``False``) is byte-identical to
    before this parameter existed.
    """
    import math

    from .core.constants import load_constants

    typer.echo(
        f"\nSweeping K from {k_min} to {k_max} step {k_step} against {len(replays)} log(s)..."
    )

    def _rmse(sims, real_dtps_values) -> float:
        return math.sqrt(
            sum(((s - rd) / rd) ** 2 for s, rd in zip(sims, real_dtps_values, strict=True))
            / len(sims)
        )

    real_dtps_values = [rd for _replay, rd, _label, _run_char in replays]

    # All callers share the same mutable dict returned by lru_cache — mutate in-place.
    c = load_constants()
    orig_k = c["armor"]["k_constant"]
    best_k = orig_k
    best_rmse = float("inf")
    best_sims: list[float] = []
    rows = []

    # Union the canonical K into the requested grid (mirrors
    # scripts/calibrate_spec_from_logs.py's own `{*range(...), canon_k}`
    # convention) — guarantees the F-layer/LOO-CV sections below always have
    # a canonical-K row to key off, even when --k-min/--k-max/--k-step don't
    # happen to land on it exactly. A no-op whenever they already do (every
    # existing pinned-K invocation, e.g. --k-min 3430 --k-max 3430).
    ks = sorted({*range(k_min, k_max + 1, k_step), orig_k})

    try:
        for k in ks:
            c["armor"]["k_constant"] = k

            sims = []
            for replay, _real_dtps, _, run_char in replays:
                heal_r = replace(
                    heal, baseline_hps_abs=replay.actual_dealt / replay.duration_s * 1.1
                )
                r = run_simulation(
                    run_char,
                    None,
                    heal_r,
                    iterations=iterations,
                    seed=seed,
                    events_override=replay.events,
                    duration_override=replay.duration_s,
                    compute_metrics=False,
                )
                sims.append(r.mean_dtps)

            rmse = _rmse(sims, real_dtps_values)
            rows.append((k, rmse, sims))
            if rmse < best_rmse:
                best_rmse = rmse
                best_k = k
                best_sims = sims
    finally:
        c["armor"]["k_constant"] = orig_k

    typer.echo("\nResults:")
    for k, rmse, sims in rows:
        deltas = [
            f"{(s - rd) / rd * 100:+.1f}%" for s, (_, rd, _, _) in zip(sims, replays, strict=False)
        ]
        marker = " ← best" if k == best_k else ""
        typer.echo(f"  K={k:6d}: RMSE={rmse:.3f}  [{', '.join(deltas)}]{marker}")

    typer.echo(f"\nBest K: {best_k} (RMSE={best_rmse:.4f})")

    # Partial (success is None — truncated/unverified) runs are included in
    # the headline RMSE above for continuity with existing ratified corpora,
    # but their ground-truth DTPS reflects only whatever fraction of the pull
    # got captured before the log cut off — surface a second, partial-free
    # figure so a bad fragment can't silently skew the number without anyone
    # noticing (see the 2026-07-12 RMSE-drift investigation).
    partial_flags = [replay.run.success is not True for replay, _rd, _label, _rc in replays]
    n_partial = sum(partial_flags)
    if n_partial:
        timed_only = [
            (s, rd)
            for s, rd, is_partial in zip(best_sims, real_dtps_values, partial_flags, strict=True)
            if not is_partial
        ]
        if timed_only:
            timed_sims, timed_rds = zip(*timed_only, strict=True)
            timed_rmse = _rmse(timed_sims, timed_rds)
            typer.echo(
                f"  ({n_partial}/{len(replays)} replays are partial/truncated runs; RMSE excluding "
                f"them at K={best_k}: {timed_rmse:.4f}, n={len(timed_only)})"
            )
        else:
            typer.echo(
                f"  ({n_partial}/{len(replays)} replays are partial/truncated runs; no non-partial "
                "replay is available to compare against)"
            )
    typer.echo(f"Update data/constants.yaml: k_constant: {best_k}")

    if f_measurements:
        _report_f_layer(replays=replays, rows=rows, canon_k=orig_k, f_measurements=f_measurements)

    if not skip_loo_cv:
        _run_loo_cv_fn = _load_run_loo_cv()
        if _run_loo_cv_fn is not None:
            labels = [label for _replay, _rd, label, _rc in replays]
            swept_ks = [k for k, _rmse, _sims in rows]
            table = {
                k: (
                    rmse,
                    [
                        (s - rd) / rd * 100
                        for s, (_replay, rd, _label, _rc) in zip(sims, replays, strict=True)
                    ],
                )
                for k, rmse, sims in rows
            }
            _run_loo_cv_fn(table, swept_ks, labels)

    if return_result:
        best_deltas = [
            (s - rd) / rd * 100
            for s, (_replay, rd, _label, _rc) in zip(best_sims, replays, strict=True)
        ]
        return {
            "best_k": best_k,
            "best_rmse": best_rmse,
            "deltas": best_deltas,
            "labels": [label for _replay, _rd, label, _rc in replays],
        }
    return None


def _report_f_layer(
    *,
    replays: list,
    rows: list[tuple[int, float, list[float]]],
    canon_k: int,
    f_measurements: dict[str, Any],
) -> None:
    """Diagnostic-only F-layer report, called from ``_run_k_sweep`` when the
    local-log path supplied ``f_measurements``. Never mutates constants.yaml,
    never changes ``calibration_tier``, never feeds back into the sweep
    itself — pure post-processing of numbers ``_run_k_sweep`` already
    computed (the per-K ``sims`` in ``rows``) plus each run's already-measured
    F (see ``scripts/measure_run_f.py`` and ``calibrate_k``'s docstring).

    Reports F-consistency ONLY — does this corpus's per-run F cluster
    tightly, the same ±0.05 band ``scripts/calibrate_spec_from_logs.py``
    already gates other specs' corpus admission on? This is the ONLY use of
    F that module's own reference implementation makes (see its
    ``cmd_calibrate``) — it never multiplies F into a sim/RMSE number.

    Deliberately does NOT compute a "sim × F"-style corrected residual.
    ``measure_run_f.py``'s own docstring is explicit that F is "relative to
    the armor+versatility model ONLY — it does not divide out any spec's
    own always-on flat DR (Defensive Stance, Thick Hide, etc.)". For
    Protection Warrior specifically, Defensive Stance's -15% all-schools cut
    (``mitigation.py`` step 5c, unconditional — no ``is_log_replay`` gate,
    unlike Demoralizing Shout/Phalanx) IS already inside every ``sim`` value
    in ``rows``. Multiplying ``sim`` by F would therefore double-count that
    same -15% (and any other always-on layer active in replay) a second
    time. This was verified empirically before being ruled out, not assumed:
    an earlier version of this function computed exactly that product across
    the ratified 16-log corpus and it flipped the corpus from +11.0%
    over-predicting to a -22.3% mean bias (RMSE 0.134 -> 0.228) — a 33pp
    swing in the wrong direction, which is itself the signature of a
    double-counted DR layer, not a genuine correction. See
    ``docs/validation/protwarrior_f_layer_measurement_2026_07_21.md`` for the
    full derivation and the correctly-scoped follow-up (a per-hit forensics
    pass, ``scripts/per_hit_mitigation_forensics.py``, dividing out the
    model's FULL always-on chain — armor + vers + Defensive Stance + any
    other replay-active layer — across the whole corpus, not just the 2 logs
    checked so far).
    """
    canon_row = next((row for row in rows if row[0] == canon_k), None)
    typer.echo(
        f"\n=== F-layer diagnostic (canonical K={canon_k}) — measurement only, not a fix ==="
    )
    if canon_row is None:
        typer.echo(
            f"  Skipped — K={canon_k} wasn't included in this sweep "
            f"(pass --k-min {canon_k} --k-max {canon_k} to include it)."
        )
        return
    _canon_k_value, _canon_rmse, canon_sims = canon_row

    measurable = [
        (label, rd, sim, fmeas)
        for (_replay, rd, label, _rc), sim in zip(replays, canon_sims, strict=True)
        for fmeas in (f_measurements.get(label),)
        if fmeas is not None
    ]
    n_total = len(replays)
    typer.echo(
        f"  F measurable on {len(measurable)}/{n_total} runs "
        "(rest: no per-hit live-armor data — ACL off, or a WCL import)"
    )
    if not measurable:
        typer.echo("  No F-consistency or F-corrected residual possible — 0 runs measurable.")
        return

    f_values = sorted(fmeas.median_f for _label, _rd, _sim, fmeas in measurable)
    n = len(f_values)
    mid = n // 2
    f_median = f_values[mid] if n % 2 else (f_values[mid - 1] + f_values[mid]) / 2
    band = 0.05  # same _F_CONSISTENCY_BAND scripts/calibrate_spec_from_logs.py uses
    n_in_band = sum(1 for v in f_values if abs(v - f_median) <= band)
    typer.echo(
        f"  F-consistency: median F={f_median:.3f}, {n_in_band}/{n} within ±{band:.2f} of the "
        "median (same band convention as the other-spec F-consistency gate)"
    )

    raw_deltas = []
    for label, rd, sim, fmeas in measurable:
        raw_delta = (sim - rd) / rd * 100
        raw_deltas.append(raw_delta)
        marker = "" if abs(fmeas.median_f - f_median) <= band else "  [OUT-OF-BAND]"
        typer.echo(
            f"  {label}: F={fmeas.median_f:.3f} n={fmeas.n_hits}  raw Δ={raw_delta:+.1f}%{marker}"
        )

    import math

    raw_rmse = math.sqrt(sum((d / 100) ** 2 for d in raw_deltas) / len(raw_deltas))
    raw_bias = sum(raw_deltas) / len(raw_deltas)
    typer.echo(
        f"\n  Raw (unweighted, F-measurable subset only, n={len(measurable)}): "
        f"RMSE={raw_rmse:.4f}, mean signed error={raw_bias:+.1f}%"
    )
    typer.echo(
        '  NOT computed here: a "sim × F" corrected residual. F above is relative to '
        "armor+versatility ONLY (measure_run_f.py's own docstring) — it does not divide out "
        "this spec's own always-on DR (e.g. Defensive Stance -15%, already inside every `sim` "
        "value above), so multiplying sim by F double-counts that layer. A valid wedge-corrected "
        "residual needs a per-hit forensics pass dividing out the model's FULL always-on chain "
        "(scripts/per_hit_mitigation_forensics.py) across the whole corpus — see "
        "docs/validation/protwarrior_f_layer_measurement_2026_07_21.md."
    )
    typer.echo(
        "  F is diagnostic ONLY — it is not baked into constants.yaml, does not change "
        "calibration_tier, and does not change the sim's damage math."
    )


@app.command(name="cd-plan")
def cd_plan(
    log_path: str = typer.Argument(..., help="Path to a WoWCombatLog-*.txt file"),
    character: str = typer.Option(
        str(DATA_DIR / "characters" / "brutoh.yaml"),
        help="Character YAML path",
    ),
    log_target: str = typer.Option(
        "Brutoh-Uldum-EU",
        help="Target name as it appears in the log",
    ),
    healing_profile: str = typer.Option("m+_high_key_healer"),
    run_index: int = typer.Option(-1, help="Which CHALLENGE_MODE run (-1 = last)"),
    search_iterations: int = typer.Option(
        100, help="Iterations per candidate plan (lower = faster)"
    ),
    top_n_spikes: int = typer.Option(6, help="Top-N damage spikes considered as anchor points"),
    max_candidates: int = typer.Option(24, help="Cap on Cartesian product of plans evaluated"),
    seed: int = typer.Option(42),
):
    """Find the best long-CD placement for a Mythic+ run.

    Replays the supplied log, searches CD placements anchored on the
    worst damage spikes, and prints the ranked candidates plus the best
    plan's press timeline.
    """
    from dataclasses import replace as dc_replace

    from .optimizer.cooldown_planner_optimizer import optimize_cooldown_plan

    char = load_character(character)
    heal = load_healing_profile(healing_profile)
    replay = load_replay(Path(log_path), log_target, run_index)
    heal = dc_replace(heal, baseline_hps_abs=replay.actual_dealt / replay.duration_s * 1.1)

    typer.echo(
        f"Replay: {replay.run.map_name} +{replay.run.key_level}, "
        f"{replay.event_count:,} events, "
        f"{int(replay.duration_s // 60)}:{int(replay.duration_s % 60):02d} duration"
    )
    typer.echo(
        f"Searching CD placements (iters/candidate={search_iterations}, "
        f"top spikes={top_n_spikes}, max candidates={max_candidates})..."
    )

    result = optimize_cooldown_plan(
        character=char,
        damage_profile=None,
        healing_profile=heal,
        events_override=replay.events,
        duration_override=replay.duration_s,
        search_iterations=search_iterations,
        top_n_spikes=top_n_spikes,
        max_candidates=max_candidates,
        seed=seed,
    )

    typer.echo(f"\nEvaluated {len(result.candidates)} candidate(s). Ranked best-first:\n")
    typer.echo(f"  {'death_rate':>10}  {'p99_5s':>10}  {'dtps':>10}  {'hrps':>10}   label")
    typer.echo(f"  {'-' * 10}  {'-' * 10}  {'-' * 10}  {'-' * 10}   {'-' * 40}")
    for c in result.candidates:
        typer.echo(
            f"  {c.death_rate * 100:9.2f}%  {c.p99_5s_window:10,.0f}  "
            f"{c.mean_dtps:10,.0f}  {c.mean_hrps:10,.0f}   {c.label}"
        )

    if not result.best.plan.presses:
        typer.echo("\nBest plan: no presses (heuristic-only is the strongest option here).")
        return

    typer.echo("\nBest plan press timeline:")
    for press in result.best.plan.presses:
        mm = int(press.time_s // 60)
        ss = int(press.time_s % 60)
        typer.echo(f"  {mm:02d}:{ss:02d}  →  {press.ability}")

    delta_dr_pp = (result.best.death_rate - result.baseline_no_plan.death_rate) * 100
    delta_p99 = result.best.p99_5s_window - result.baseline_no_plan.p99_5s_window
    typer.echo(
        f"\nvs no-plan baseline ({result.baseline_no_plan.death_rate * 100:.2f}% / "
        f"{result.baseline_no_plan.p99_5s_window:,.0f}): "
        f"death rate {delta_dr_pp:+.2f}pp, p99 5s spike {delta_p99:+,.0f}"
    )
    typer.echo(
        "Note: absolute death-rate numbers reflect the heuristic policy's CD usage "
        "(typically more pessimistic than a real player). The RANKING between plans "
        "is the trustworthy signal — pick a plan, then play to it."
    )


@app.command(name="compare")
def compare(
    char_a: str = typer.Option(
        str(DATA_DIR / "characters" / "brutoh.yaml"),
        "--char-a",
        help="Baseline character YAML (A side)",
    ),
    char_b: str | None = typer.Option(
        None,
        "--char-b",
        help="Alternative character YAML (B side). If omitted, use --char-a with --set / --talents overrides.",
    ),
    set_stats: list[str] = typer.Option(
        [],
        "--set",
        help="Override a stat on the B side: key=value (e.g. --set versatility_rating=2000). Repeatable.",
    ),
    talents: str | None = typer.Option(
        None,
        "--talents",
        help="Override the talent loadout name on the B side.",
    ),
    damage_profile: str = typer.Option("m+_pull_caster", help="Damage profile"),
    healing_profile: str = typer.Option("m+_high_key_healer", help="Healing profile"),
    iterations: int = typer.Option(2000, help="Iterations per sim"),
    seed: int = typer.Option(42),
):
    """Compare two character configs: survivability metrics + static stat deltas.

    \b
    Examples:
      simf compare --char-a brutoh.yaml --char-b alt.yaml
      simf compare --char-a brutoh.yaml --set versatility_rating=2000
      simf compare --char-a brutoh.yaml --talents archon-meta
    """
    character_a = load_character(char_a)

    if char_b is not None:
        character_b = load_character(char_b)
        label_b = character_b.name
    else:
        overrides: dict = {}
        for kv in set_stats:
            if "=" not in kv:
                typer.echo(f"--set must be key=value, got: {kv}", err=True)
                raise typer.Exit(1)
            k, v = kv.split("=", 1)
            try:
                overrides[k] = int(v)
            except ValueError:
                try:
                    overrides[k] = float(v)
                except ValueError:
                    overrides[k] = v
        if talents is not None:
            overrides["talents"] = talents
        if not overrides:
            typer.echo("Provide --char-b OR at least one --set / --talents override.", err=True)
            raise typer.Exit(1)
        character_b = replace(character_a, **overrides)
        parts = [f"{k}={v}" for k, v in overrides.items()]
        label_b = f"{character_a.name} +({', '.join(parts)})"

    label_a = character_a.name
    dmg = load_damage_profile(damage_profile)
    heal = load_healing_profile(healing_profile)

    result_a = run_simulation(character_a, dmg, heal, iterations=iterations, seed=seed)
    result_b = run_simulation(character_b, dmg, heal, iterations=iterations, seed=seed)

    render_summary(result_a, character_a, damage_profile, healing_profile)
    render_summary(result_b, character_b, damage_profile, healing_profile)
    render_ab(result_a, label_a, result_b, label_b, char_a=character_a, char_b=character_b)


@app.command(name="import-character")
def import_character(
    simc_path: str = typer.Argument(..., help="Path to a /simc export file"),
    stats: str | None = typer.Option(
        None, help="Optional YAML with character sheet stats to merge in"
    ),
    output: str | None = typer.Option(
        None, help="Output character YAML path; prints to stdout if omitted"
    ),
    talents: str = typer.Option("kiratank-defensive", help="Named talent loadout to assign"),
):
    """Convert a SimC export string into a simf character YAML.

    The /simc string contains gear and structural fields but not aggregated stats.
    Either pass a --stats YAML, or edit the output to fill in your character sheet
    values from the in-game Stats tab.
    """
    sim = load_simc_file(simc_path)

    stats_dict: dict | None = None
    if stats:
        with Path(stats).open() as f:
            stats_dict = yaml.safe_load(f)

    char_dict = simc_to_character_yaml(sim, stats_dict, talents_loadout=talents)

    yaml_text = yaml.dump(char_dict, sort_keys=False)
    if output:
        Path(output).write_text(yaml_text)
        typer.echo(f"Wrote {output}")
    else:
        typer.echo(yaml_text)


@app.command(name="fetch-gear")
def fetch_gear(
    name: str = typer.Argument(..., help="Character name"),
    realm: str = typer.Argument(..., help="Realm/server slug, e.g. 'uldum'"),
    region: str = typer.Option("eu", help="Region: eu | us | kr | tw"),
    source: str = typer.Option(
        "auto",
        help="Gear source: 'auto' (Blizzard if configured, else Raider.IO) | 'raiderio' | 'blizzard'.",
    ),
    output: str | None = typer.Option(
        None, help="Output character YAML path; prints to stdout if omitted"
    ),
):
    """Fetch a character's CURRENT equipped gear from an online source → simf character YAML.

    'auto' prefers the Blizzard Armory when client-credentials are configured
    (exact in-game stats), falling back to the zero-auth Raider.IO API.
    EQUIPPED gear only — the Great Vault and bags need a /simc paste (those are
    player-OAuth-protected). The gear is the character's *current* loadout,
    which may differ from the gear worn during any past logged fight.
    """
    if source not in ("auto", "raiderio", "blizzard"):
        typer.echo(f"Unknown source '{source}'. Use auto | raiderio | blizzard.")
        raise typer.Exit(1)
    from .io.gear_import import fetch_online_gear

    result, used = fetch_online_gear(name, realm, region, source)
    if result is None:
        typer.echo(
            f"Could not fetch {name}-{realm} ({region}) [source={source}] — "
            "not found, not a modeled tank spec, source not configured, or API error."
        )
        raise typer.Exit(1)
    typer.echo(f"[source: {used}] {result.summary}")
    yaml_text = yaml.dump(result.char_data, sort_keys=False)
    if output:
        Path(output).write_text(yaml_text)
        typer.echo(f"Wrote {output}")
    else:
        typer.echo(yaml_text)


@app.command(name="rankings-refresh")
def rankings_refresh(
    specs: str | None = typer.Option(
        None,
        help=(
            "Comma-separated spec slugs to refresh (e.g. "
            "'protection_warrior,blood_death_knight'). Default: all 6 tank specs."
        ),
    ),
    key_levels: str | None = typer.Option(
        None,
        help=(
            "Comma-separated key-level thresholds to refresh, one per spec in the "
            "same order as --specs. Default: each spec's `broad_completion_key` "
            "from world_ceilings.yaml."
        ),
    ),
):
    """Pre-warm the cross-encounter broad-completion cache.

    Hits WCL `characterRankings` for every season encounter × spec,
    deduplicates characters by `(name, server.name, server.region)`, and
    writes the union count to `~/.simf/wcl_rankings_cache.json`.

    The UI render path never triggers this — the cold refresh takes
    roughly 6-10 minutes for the full 6-spec set and would block the
    verdict surface. Run this once a day (or via cron); the UI reads
    the cached value at sub-millisecond cost.

    When the cache is empty / stale and a verdict surface renders, the
    UI falls back to the existing single-encounter count (which is
    itself a floor on the true cross-encounter union).
    """
    from .data.world_ceilings import load_world_ceilings
    from .io.wcl_rankings import refresh_cross_encounter_broad_counts

    ceilings = load_world_ceilings()
    all_specs = list(ceilings.keys())

    if specs:
        requested = [s.strip() for s in specs.split(",") if s.strip()]
        unknown = [s for s in requested if s not in ceilings]
        if unknown:
            typer.echo(f"Unknown spec(s): {', '.join(unknown)}", err=True)
            typer.echo(f"Known: {', '.join(all_specs)}", err=True)
            raise typer.Exit(code=2)
        spec_list = requested
    else:
        spec_list = all_specs

    if key_levels:
        levels = [int(x.strip()) for x in key_levels.split(",") if x.strip()]
        if len(levels) != len(spec_list):
            typer.echo(
                f"--key-levels must have one value per spec ({len(spec_list)} expected, "
                f"{len(levels)} given)",
                err=True,
            )
            raise typer.Exit(code=2)
        spec_threshold_map = dict(zip(spec_list, levels, strict=True))
    else:
        spec_threshold_map = {slug: ceilings[slug].broad_completion_key for slug in spec_list}

    typer.echo(
        f"Refreshing cross-encounter broad-completion counts for "
        f"{len(spec_threshold_map)} spec(s); this can take several minutes."
    )

    def _progress(spec_slug: str, idx: int, total: int) -> None:
        typer.echo(f"  {spec_slug}: encounter {idx}/{total}")

    fresh = refresh_cross_encounter_broad_counts(spec_threshold_map, progress=_progress)

    if not fresh:
        typer.echo(
            "No data refreshed — WCL credentials may be missing or every spec "
            "returned zero rows. Cache is unchanged.",
            err=True,
        )
        raise typer.Exit(code=1)

    typer.echo("Refresh complete:")
    for spec_slug, payload in fresh.items():
        count = payload["broad_count_cross"]
        cap_suffix = "+" if payload["broad_count_cross_capped"] else ""
        threshold = payload["broad_threshold"]
        world_max = payload.get("world_max_key_cross")
        world_max_suffix = f" (world max: +{world_max})" if world_max else ""
        typer.echo(
            f"  {spec_slug}: {count:,}{cap_suffix} distinct chars at +{threshold}+"
            f"{world_max_suffix}"
        )


@app.command(name="usage-report")
def usage_report(
    path: str | None = typer.Option(
        None,
        help="Override the usage-analytics JSONL path (default: ~/.simf/share_hits.jsonl, "
        "or $SIMF_SHARE_HITS_PATH).",
    ),
):
    """Summarize simf's public usage-analytics event log.

    Reads the JSONL written by `core.share_hits` (via
    `ui.helpers.usage_tracking`) and prints view/load-method/spec/WCL-outcome
    breakdowns plus key-level ceiling stats — the "how is simf actually being
    used" question the raw log can't answer by itself.
    """
    from .core.usage_report import build_usage_report

    report = build_usage_report(override_path=Path(path) if path else None)
    typer.echo(report.render())


def load_character(path_str: str) -> Character:
    path = Path(path_str)
    with path.open() as f:
        d = yaml.safe_load(f)
    return Character.from_dict(d)


if __name__ == "__main__":
    app()
