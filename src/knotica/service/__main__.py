"""Daemon entry point: ``python -m knotica.service``.

The installed OS-service unit runs this module. It hands straight to
:func:`knotica.service.manager.supervise`, the one supervised process that
iterates every configured topic forever, resolving the topic set from config
fresh on each cycle. All state lives in the vault (git) and the config file --
this process holds none of its own.
"""

from knotica.service.manager import supervise


def main() -> None:
    """Run the supervision loop until interrupted (SIGTERM/SIGINT from the OS)."""
    try:
        supervise()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
