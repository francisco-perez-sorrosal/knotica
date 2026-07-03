"""CLI adapter -- the ``knotica`` console entry point.

Thin by design: parses arguments, applies output conventions (stdout = data,
stderr = messages), and delegates. Reads go through ``knotica.core`` read
functions; mutations go ONLY through ``knotica.core.operations.*`` -- this
package never imports ``core.vcs``/``core.lock`` and never writes the vault
directly (the single writer is ``core.transaction``; enforced by the
import-boundary fitness test).
"""

from importlib.metadata import version


def main() -> int:
    """Print the installed knotica version.

    Placeholder entry point; the CLI implementation step replaces this with the
    argparse subcommand dispatcher (``init``/``mcp``/``doctor``/``status``/
    ``migrate``/``prompt``).
    """
    print(f"knotica {version('knotica')}")
    return 0
