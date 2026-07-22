"""Daemon entry point: ``python -m knotica.service``.

The installed OS-service unit runs this module. It hands straight to
:func:`knotica.service.manager.supervise`, the one supervised process that
iterates every configured topic forever, resolving the topic set from config
fresh on each cycle. All state lives in the vault (git) and the config file --
this process holds none of its own.

Environment bootstrap: launchd/systemd start the daemon with a near-empty
process environment, but the eval-LLM credential is read from the process
environment only (never from files, by contract). Before supervision starts,
every ``KEY=VALUE`` in ``~/.config/knotica/.env`` is loaded into the
environment for keys not already set -- a real exported variable always wins,
secrets never land in the world-readable unit file, and values are never
logged. Only the canonical config location is read; the daemon's working
directory is the vault, so a vault-adjacent ``.env`` is deliberately not a
secret source here.
"""

import logging
import os
import sys
from pathlib import Path

from knotica.service.manager import supervise

#: The one .env location the daemon trusts (the canonical config dir).
DAEMON_DOTENV_PATH = "~/.config/knotica/.env"


def bootstrap_environment(
    dotenv_path: str | os.PathLike[str] = DAEMON_DOTENV_PATH,
    *,
    environ: dict[str, str] | None = None,
) -> None:
    """Load ``KEY=VALUE`` lines from ``dotenv_path`` into unset environ keys.

    Same minimal grammar as the discovery-key fallback reader: blank lines and
    ``#`` comments skipped, one optional ``export`` prefix tolerated,
    surrounding single/double quotes stripped. An already-set variable is never
    overridden; a missing or unreadable file is a silent no-op; values are
    never logged.
    """
    env = os.environ if environ is None else environ
    try:
        lines = Path(dotenv_path).expanduser().read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.removeprefix("export ").partition("=")
        name = name.strip()
        cleaned = value.strip().strip("'\"")
        if name and cleaned and name not in env:
            env[name] = cleaned


def _configure_logging() -> None:
    """Route supervision logs to stdout so the OS unit's log file captures them.

    launchd/systemd redirect the daemon's stdout/stderr to the unit's log
    paths; without an explicit handler Python drops INFO entirely and the
    daemon runs silent -- an incident then leaves no trail.
    """
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main() -> None:
    """Configure logging, bootstrap the environment, then supervise."""
    _configure_logging()
    bootstrap_environment()
    try:
        supervise()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
