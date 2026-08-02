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

.PHONY: help start install verify doctor restart-daemon desktop clean-tool

help:  ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

start: install restart-daemon  ## Install everything, then pick up the new build (the one command)
	@echo
	@echo "Repo + CLI are on the current build."
	@echo "Claude Desktop launches its server from this repo, so finish with:"
	@echo "  fully quit Claude Desktop (Cmd-Q) and reopen it."

install:  ## Sync the venv and (re)install the knotica CLI with headless evals support
	$(UV) sync --extra evals
	$(UV) tool install --from '.[evals]' knotica --force
	@knotica --version

# `--extra evals` is explicit rather than incidental: the eval-facing tests should
# exercise the real anthropic/dspy packages, and a bare `uv run` leaves whether
# they are present up to whatever the venv happens to hold.
verify:  ## Run the canonical checks, in order: types, tests, lint
	$(UV) run --extra evals mypy src/knotica
	$(UV) run --extra evals pytest
	$(UV) run --extra evals ruff check .
	$(UV) run --extra evals ruff format --check .

doctor:  ## Report vault/config health for the active knowledge base
	$(UV) run --extra evals knotica doctor --quick

restart-daemon:  ## Restart the loop service so it runs the freshly installed code
	@if launchctl list | grep -q '$(LAUNCHD_LABEL)'; then \
	  launchctl kickstart -k gui/$$(id -u)/$(LAUNCHD_LABEL) && \
	  echo "restarted $(LAUNCHD_LABEL)"; \
	else \
	  echo "loop service not installed (skip) — 'knotica service install' to add it"; \
	fi

desktop:  ## Register this repo as the knotica MCP server in Claude Desktop
	$(UV) run --extra evals knotica init --desktop --yes
	@echo "Now fully quit Claude Desktop (Cmd-Q) and reopen it."

clean-tool:  ## Remove the globally installed knotica CLI
	-$(UV) tool uninstall knotica
