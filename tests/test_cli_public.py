"""`simf ui --public` flags (Phase 5).

The WebSocket/CORS/XSRF flags are what make Streamlit work behind a reverse-
proxy tunnel (without them the app renders blank — the #1 go-live failure), so
they must not silently drift.
"""

from __future__ import annotations

from pathlib import Path

from simf.cli import _public_streamlit_flags

REPO_ROOT = Path(__file__).resolve().parents[1]


def _pairs(flags: list[str]) -> dict[str, str]:
    return {flags[i]: flags[i + 1] for i in range(0, len(flags) - 1, 2)}


def test_public_flags_carry_the_tunnel_websocket_trio():
    """Required behind a tunnel — Streamlit renders blank without them."""
    p = _pairs(_public_streamlit_flags())
    assert p["--server.enableCORS"] == "false"
    assert p["--server.enableXsrfProtection"] == "false"
    assert p["--server.enableWebsocketCompression"] == "false"


def test_public_flags_carry_the_hardening():
    p = _pairs(_public_streamlit_flags())
    assert p["--server.maxUploadSize"] == "25"  # below the owner's 500MB; OOM guard
    assert p["--client.showErrorDetails"] == "false"  # no filesystem leak in tracebacks
    assert p["--client.toolbarMode"] == "minimal"  # hides the Deploy button
    assert p["--server.address"] == "127.0.0.1"  # tunnel-only; no LAN bypass of the edge


def test_public_flags_default_bind_is_loopback():
    """Default bind is loopback-only — the box is reachable solely via the
    tunnel, never the LAN, unless the operator opts in via SIMF_PUBLIC_BIND."""
    assert _pairs(_public_streamlit_flags())["--server.address"] == "127.0.0.1"


def test_public_flags_bind_override_for_lan():
    """SIMF_PUBLIC_BIND=0.0.0.0 → serve the LAN too (one process: localhost +
    LAN + tunnel). Must thread through to --server.address."""
    assert _pairs(_public_streamlit_flags(bind="0.0.0.0"))["--server.address"] == "0.0.0.0"


def test_public_bind_refuses_nonloopback_ipv6():
    """`::` would bind the box's GLOBAL IPv6 (bindv6only=0) → directly
    internet-exposed, bypassing the tunnel. Must fail closed to loopback."""
    assert _pairs(_public_streamlit_flags(bind="::"))["--server.address"] == "127.0.0.1"


def test_public_bind_allows_ipv6_loopback():
    assert _pairs(_public_streamlit_flags(bind="::1"))["--server.address"] == "::1"


def test_public_bind_empty_or_garbage_falls_back_to_loopback():
    assert _pairs(_public_streamlit_flags(bind=""))["--server.address"] == "127.0.0.1"
    assert _pairs(_public_streamlit_flags(bind="not-an-ip"))["--server.address"] == "127.0.0.1"


def test_public_flags_set_server_address_only_when_host_given():
    assert "--browser.serverAddress" not in _public_streamlit_flags()
    assert "--browser.serverAddress" not in _public_streamlit_flags(host="")
    flags = _public_streamlit_flags(host="simf.example.com")
    assert _pairs(flags)["--browser.serverAddress"] == "simf.example.com"


def _capture_ui_cmd(monkeypatch, **kwargs) -> list[str]:
    """Call `cli.ui` with `subprocess.run` patched; return the streamlit argv.

    Params are passed explicitly — typer's OptionInfo defaults are truthy when
    the command function is called directly (not via the CLI parser), so an
    omitted bool would take the wrong branch.
    """
    import subprocess

    from simf import cli

    captured: dict[str, list[str]] = {}

    def fake_run(cmd, env=None):  # type: ignore[no-untyped-def]
        captured["cmd"] = cmd

        class R:
            returncode = 0

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    defaults = {"port": 8501, "headless": True, "public": False, "host": "", "reload": False}
    defaults.update(kwargs)
    cli.ui(**defaults)
    return captured["cmd"]


def _watcher(cmd: list[str]) -> str:
    return cmd[cmd.index("--server.fileWatcherType") + 1]


def test_ui_reload_uses_poll_watcher(monkeypatch):
    """`simf ui --reload` → dev hot-reload via the POLL watcher (NOT watchdog:
    its inotify observer needs _ctypes, missing on this box — commit ddcf845)."""
    assert _watcher(_capture_ui_cmd(monkeypatch, reload=True)) == "poll"


def test_ui_without_reload_disables_watcher(monkeypatch):
    """Default (no --reload) keeps the watcher off — the long-standing behavior
    that dodges the inotify/_ctypes crash for normal/deployed use."""
    assert _watcher(_capture_ui_cmd(monkeypatch, reload=False)) == "none"


def test_ui_public_never_reloads(monkeypatch):
    """A --public deploy never hot-reloads even if --reload is passed: nothing
    changes under a read-only public instance, and a watcher is pure overhead."""
    assert _watcher(_capture_ui_cmd(monkeypatch, public=True, reload=True)) == "none"


def test_make_ui_target_enables_reload():
    """`make ui` passes --reload so the dev bounce hot-reloads."""
    makefile = (REPO_ROOT / "Makefile").read_text()
    assert "ui --reload" in makefile  # dev target opts in


def test_dev_target_launches_checkout_on_8502_without_systemd():
    """The dev/verification instance is on-demand now (2026-07-18): `make dev`
    runs the full app straight from THIS checkout on :8502, no systemd unit, no
    reconcile hook. The old simf-owner.service + install-owner-service/restart-owner
    scripts + the post-merge auto-reconcile hook were removed as needless
    complexity."""
    makefile = (REPO_ROOT / "Makefile").read_text()
    assert "\ndev:" in makefile
    assert "ui --port 8502" in makefile
    assert "dev" in makefile.split(".PHONY:")[1].split("\n")[0]
    # The removed systemd/dev-server machinery must stay gone.
    assert "restart-owner" not in makefile
    assert "install-owner-service" not in makefile
    assert not (REPO_ROOT / "scripts" / "restart-owner.sh").exists()
    assert not (REPO_ROOT / "scripts" / "install-owner-service.sh").exists()
    assert not (REPO_ROOT / "scripts" / "hooks" / "post-merge").exists()
