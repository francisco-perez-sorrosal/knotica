# Knotica developer entry points.
#
# One instruction to go from a fresh clone to a working install:
#
#     make start
#
# Everything below is a thin, idempotent wrapper over uv — no state of its own.

SHELL := /bin/bash
.DEFAULT_GOAL := help

UV ?= uv
LAUNCHD_LABEL := com.knotica.loop
LOG_DIR := $(HOME)/Library/Logs/knotica

.PHONY: help start install verify doctor desktop clean-tool \
        test-group test-groups \
        daemon-install daemon-restart daemon-status daemon-uninstall daemon-logs

help:  ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-17s\033[0m %s\n", $$1, $$2}'

# `start` restarts the daemon but never *registers* it. Registration writes an
# OS unit that auto-starts at login and runs billed evals, so it stays a
# deliberate act: "install/uninstall never run automatically anywhere in this
# codebase ... a command the user runs themselves, when ready" (cli/service.py).
# `daemon-restart` reports when nothing is registered, so the gap is visible
# rather than silent.
start: install daemon-restart  ## Install everything, then pick up the new build (the one command)
	@echo
	@echo "Repo + CLI are on the current build."
	@echo "Claude Desktop launches its server from this repo, so finish with:"
	@echo "  fully quit Claude Desktop (Cmd-Q) and reopen it."
	@echo
	@echo "The autonomous loop daemon is opt-in (it runs billed evals):"
	@echo "  make daemon-install   register it   ·   make daemon-status   check it"

install:  ## Sync the venv and (re)install the knotica CLI with headless evals support
	$(UV) sync --extra evals
	$(UV) tool install --from '.[evals]' knotica --force
	@knotica --version

# `--extra evals` is explicit rather than incidental: the eval-facing tests should
# exercise the real anthropic/dspy packages, and a bare `uv run` leaves whether
# they are present up to whatever the venv happens to hold.
# The topology check runs first because it is the cheapest (filesystem stats,
# no imports) and because a drifted topology makes every scoped run below it
# untrustworthy -- a selector naming a deleted file silently shrinks a group.
# The three record checks are grouped ahead of the code checks for the same
# reason: each compares a committed claim against the tree using filesystem
# stats alone, and a stale record makes everything below it untrustworthy to
# read even when it is green.
verify:  ## Run the canonical checks: topology, ADRs, architecture, types, tests, lint
	$(UV) run --extra evals python scripts/test_group.py --check
	$(UV) run --extra evals python scripts/check_adr_health.py
	$(UV) run --extra evals python scripts/check_architecture_coverage.py
	$(UV) run --extra evals mypy src/knotica
	$(UV) run --extra evals pytest
	$(UV) run --extra evals ruff check .
	$(UV) run --extra evals ruff format --check .

# Scoped test runs derived from `.ai-state/TEST_TOPOLOGY.md`. These targets read
# the topology rather than restating group membership, so there is exactly one
# place a group is defined -- the same file sentinel audits and
# `/refresh-topology` regenerates. `--extra evals` matches `verify`: the
# eval-harness group imports anthropic/dspy for real.
test-groups:  ## List the test groups defined in .ai-state/TEST_TOPOLOGY.md
	@$(UV) run --extra evals python scripts/test_group.py --list

test-group:  ## Run one group: make test-group GROUP=<id> [ARGS="-x -q"]
	@if [ -z "$(GROUP)" ]; then \
	  echo "usage: make test-group GROUP=<id> [ARGS=\"-x -q\"]"; \
	  echo; \
	  $(UV) run --extra evals python scripts/test_group.py --list; \
	  exit 2; \
	fi
	$(UV) run --extra evals python scripts/test_group.py $(GROUP) $(ARGS)

doctor:  ## Report vault/config health for the active knowledge base
	$(UV) run --extra evals knotica doctor --quick

# --- loop daemon lifecycle --------------------------------------------------
#
# The daemon runs `python -m knotica.service` out of the uv-tool environment
# that `make install` rebuilds, so a restart is what actually puts new code in
# front of it. Registration itself is `knotica service install` (idempotent);
# only the restart is launchd-specific, because kickstart has no portable
# equivalent and the systemd path is untested.

daemon-install:  ## Register the loop service with the OS (idempotent)
	$(UV) run --extra evals knotica service install
	@echo "next: make daemon-status"

daemon-restart:  ## Restart the loop service so it runs the freshly installed code
	@if [ "$$(uname)" != "Darwin" ]; then \
	  echo "daemon-restart is launchd-only; on systemd use: systemctl --user restart knotica-loop"; \
	elif launchctl list | grep -q '$(LAUNCHD_LABEL)'; then \
	  launchctl kickstart -k gui/$$(id -u)/$(LAUNCHD_LABEL) && \
	  echo "restarted $(LAUNCHD_LABEL) — now running the freshly installed code"; \
	else \
	  echo "loop service not registered (nothing to restart) — run: make daemon-install"; \
	fi

daemon-status:  ## Report install state and per-topic runner liveness
	$(UV) run --extra evals knotica service status

daemon-uninstall:  ## Deregister and remove the loop service unit
	$(UV) run --extra evals knotica service uninstall

daemon-logs:  ## Tail the loop service logs (stdout + stderr)
	@echo "--- $(LOG_DIR)/loop.err.log (last 20) ---"
	@tail -n 20 $(LOG_DIR)/loop.err.log 2>/dev/null || echo "(no error log yet)"
	@echo "--- $(LOG_DIR)/loop.out.log (last 20) ---"
	@tail -n 20 $(LOG_DIR)/loop.out.log 2>/dev/null || echo "(no output log yet)"

# Deliberately NOT `knotica init --desktop`: the full wizard also scaffolds a
# vault and upserts it as the *default* in config.toml, so running it to fix a
# Desktop entry switches the active knowledge base. `desktop install` touches the
# Desktop config alone -- additive, backed up first, `env` block preserved.
desktop:  ## Point Claude Desktop at this repo (idempotent; no vault/config changes)
	$(UV) run --extra evals knotica desktop install
	@echo "Now fully quit Claude Desktop (Cmd-Q) and reopen it."

clean-tool:  ## Remove the globally installed knotica CLI
	-$(UV) tool uninstall knotica
