#!/usr/bin/env python3
"""Compatibility shim — the loop runner is now ``knotica loop``.

Kept so existing invocations (docs, demos, muscle memory) keep working; all
flags are forwarded unchanged. Prefer::

    knotica loop --topic agentic-systems            # watch (observe + gate + heal)
    knotica loop --topic agentic-systems --once     # one tick
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    from knotica.cli import main as knotica_main

    args = list(sys.argv[1:] if argv is None else argv)
    print("note: scripts/loop_runner.py is now `knotica loop`; forwarding.", file=sys.stderr)
    return knotica_main(["loop", *args])


if __name__ == "__main__":
    raise SystemExit(main())
