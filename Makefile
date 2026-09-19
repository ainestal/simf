# Resolve venv: prefer a local .venv (parent clone). When invoked from
# a worktree without its own .venv, fall back to the parent clone's
# .venv discovered by walking out of `.worktrees/<id>/`. The
# Python helper avoids portability quirks with case/;; inside $(shell).
VENV_DIR := $(shell python3 scripts/detect_venv.py 2>/dev/null || echo "$$(pwd)/.venv")
PYTHON   := $(VENV_DIR)/bin/python
PIP      := $(VENV_DIR)/bin/pip
SIMF     := $(VENV_DIR)/bin/simf
PYTEST   := $(VENV_DIR)/bin/pytest
RUFF     := $(VENV_DIR)/bin/ruff
MYPY     := $(VENV_DIR)/bin/mypy
VULTURE  := $(VENV_DIR)/bin/vulture
DEPTRY   := $(VENV_DIR)/bin/deptry
PIP_AUDIT := $(VENV_DIR)/bin/pip-audit
DIFF_COVER := $(VENV_DIR)/bin/diff-cover
PYENV_PYTHON := $(HOME)/.pyenv/versions/3.13.13/bin/python
# Fall back to system Python if pyenv build lacks _ctypes (missing libffi-dev).
# System Python 3.12 has ctypes working; pyenv 3.13 does not on this machine.
VENV_PYTHON  := $(shell \
	$(PYENV_PYTHON) -c "import _ctypes" 2>/dev/null \
	&& echo $(PYENV_PYTHON) \
	|| echo /usr/bin/python3)

# Worktree awareness: when running from a `.worktrees/` checkout the
# parent .venv's editable install still serves the parent's src/simf,
# so UI changes in the worktree are invisible. Prepend the worktree's
# src/ to PYTHONPATH for any command that imports simf. Resolves to
# empty in the parent clone (no-op). See scripts/detect_worktree_src.py.
WT_SRC := $(shell $(VENV_PYTHON) scripts/detect_worktree_src.py 2>/dev/null)
ifneq ($(WT_SRC),)
export PYTHONPATH := $(WT_SRC)$(if $(PYTHONPATH),:$(PYTHONPATH))
endif

.PHONY: install install-hooks install-browser run ui ui-stop ui-restart dev test test-serial lint fmt typecheck cov check dead screenshot stat-weights replay-log analyze-log profile clean

install: ## Create .venv and install all deps (uses pyenv 3.13 if ctypes ok, else system 3.12)
	$(VENV_PYTHON) -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev,ui]"
	@$(MAKE) --no-print-directory install-hooks

install-hooks: ## Install dispatcher hooks into the shared .git/hooks/ dir
	@# Use the shared common-dir (resolves to the parent clone's `.git` from a
	@# worktree) so a single install reaches all current + future worktrees.
	@# The wrapper dispatches to the CURRENT checkout's tracked hook via
	@# `git rev-parse --show-toplevel`, so worktrees self-heal — no clone
	@# can pin the others to a stale `scripts/hooks/*` content.
	@HOOKS_DIR="$$(git rev-parse --git-common-dir)/hooks"; \
	mkdir -p "$$HOOKS_DIR"; \
	for hook in pre-commit pre-push; do \
	  printf '%s\n' '#!/usr/bin/env bash' \
	    '# Auto-installed dispatcher (make install-hooks). Calls the tracked' \
	    '# hook in whatever checkout (worktree or parent) triggered the event,' \
	    '# so no single clone can pin the others to a stale script.' \
	    'set -e' \
	    'TOPLEVEL="$$(git rev-parse --show-toplevel)"' \
	    'TRACKED="$$TOPLEVEL/scripts/hooks/'"$$hook"'"' \
	    'if [[ ! -f "$$TRACKED" ]]; then' \
	    '  echo "'"$$hook"': tracked hook missing at $$TRACKED — skipping." >&2' \
	    '  exit 0' \
	    'fi' \
	    'exec bash "$$TRACKED" "$$@"' \
	    > "$$HOOKS_DIR/$$hook"; \
	  chmod +x "$$HOOKS_DIR/$$hook"; \
	done; \
	echo "git hooks installed at $$HOOKS_DIR: pre-commit (ruff), pre-push (pytest)"

install-browser: ## Download Playwright's Firefox + Chromium runtimes (~200 MB; one-shot)
	$(PYTHON) -m playwright install firefox chromium

run: ## Run default simulation (example_warrior, m+_pull_caster, high-key healer)
	$(SIMF) run

test: ## Run the test suite (parallel via pytest-xdist)
	$(PYTEST)

test-serial: ## Run the test suite single-threaded (debugging)
	$(PYTEST) -n0

lint: ## Lint with ruff
	$(RUFF) check src tests

fmt: ## Format with ruff
	$(RUFF) format src tests
	$(RUFF) check --fix src tests

fmt-check: ## Check formatting without mutating (what CI's "Ruff format check" step runs)
	$(RUFF) format --check src tests

typecheck: ## Type-check engine (core / io / optimizer) — ui/ skipped on purpose
	$(MYPY)

cov: ## Run tests with coverage; HTML report at .coverage_html/index.html
	$(PYTEST) --cov --cov-report=term-missing --cov-report=html
	@echo "HTML coverage: .coverage_html/index.html"

dead: ## Find unused functions/classes (run before decommissioning)
	$(VULTURE)

deps: ## Check for unused/missing/transitive dependencies
	$(DEPTRY) .

audit: ## Check installed dependencies for known CVEs
	$(PIP_AUDIT)

secrets: ## Scan full git history for leaked credentials (not a pip dep — see README/CI for install)
	@if command -v gitleaks >/dev/null 2>&1; then \
		gitleaks git --log-opts="--all"; \
	else \
		echo "gitleaks not found locally — skipping (CI always runs this gate)."; \
		echo "Install: https://github.com/gitleaks/gitleaks#installing"; \
	fi

diffcov: ## Run tests with coverage, then gate only new/changed lines vs master (not a global % chase)
	$(PYTEST) --cov=src/simf --cov-report=xml
	$(DIFF_COVER) coverage.xml --compare-branch=master --fail-under=80

check: lint fmt-check typecheck deps audit secrets diffcov ## CI gate: lint + format-check + type-check + dep-hygiene + CVE audit + secrets + coverage-gated tests

ui: ## Launch Streamlit web UI (http://localhost:8501) with dev hot-reload
	$(SIMF) ui --reload

ui-stop: ## Kill anything bound to :8501 (and stray streamlit/simf-ui procs)
	-@fuser -k 8501/tcp 2>/dev/null || true
	-@pkill -f "[s]treamlit run" 2>/dev/null || true
	-@pkill -f "[s]imf ui"       2>/dev/null || true
	@echo "stopped UI on :8501 (if any)"

ui-restart: ui-stop ui ## Bounce the UI: stop the running server, then launch a fresh one

dev: ## Launch the full dev app from THIS checkout on :8502 (on-demand; Ctrl-C to stop). No systemd.
	$(SIMF) ui --port 8502

screenshot: ## Snap the running Streamlit app to /tmp/simf.png (needs `make install-browser` once)
	$(PYTHON) scripts/screenshot.py /tmp/simf.png $(SCREENSHOT_ARGS)
	@echo "wrote /tmp/simf.png"

stat-weights: ## Compute stat weights for the default character
	$(SIMF) stat-weights

replay-log: ## Replay last M+ run from the most recent combat log
	$(SIMF) replay-log examples/WoWCombatLog-050626_172823.txt

analyze-log: ## Analyze damage taken from the most recent combat log
	$(SIMF) analyze-log examples/WoWCombatLog-050626_172823.txt

profile: ## Profile run_simulation + cd-plan; writes profiles/*.prof and docs/profiling/phase_5_2_baseline.md (~15 min on Pi)
	$(PYTHON) scripts/profile_sim.py

clean: ## Remove the virtual environment
	rm -rf .venv
