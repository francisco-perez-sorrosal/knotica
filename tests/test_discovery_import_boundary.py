"""Cold-start import-boundary fitness test, extended to ``discovery`` .

Mirrors the fresh-interpreter subprocess pattern already established at
``tests/test_evals_llm.py::test_importing_the_llm_module_does_not_import_the_anthropic_sdk``
and ``tests/test_evals_config.py::test_importing_the_config_module_imports_neither_dspy_nor_anthropic``:
a same-process check would false-positive if an earlier test in the suite
already imported ``discovery`` or ``httpx``, so every assertion here runs a
fresh child interpreter and inspects *its* ``sys.modules``.

Two properties are pinned (pre-mortem guard 5 -- a future edit to
``discovery/__init__.py`` that eagerly imports the heavy chain must be caught
automatically, not once and never re-run):

- importing ``knotica.mcp_server`` must never transitively pull in
  ``knotica.discovery`` -- the MCP cold-start path stays off this package
  entirely;
- importing ``knotica.discovery`` itself must succeed with only the base
  environment and must never construct the heavy ``httpx`` client at import
  time -- every adapter/enricher/http-wrapper module defers its ``import
  httpx`` into a constructor or method body, never the module top level.
"""

import subprocess
import sys


def test_importing_mcp_server_does_not_transitively_import_discovery() -> None:
    script = (
        "import sys\n"
        "import knotica.mcp_server\n"
        "leaked = sorted(\n"
        "    m for m in sys.modules\n"
        "    if m == 'knotica.discovery' or m.startswith('knotica.discovery.')\n"
        ")\n"
        "assert not leaked, leaked\n"
        "print('MCP_SERVER_DISCOVERY_ISOLATION_OK')\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        "importing knotica.mcp_server must not transitively import knotica.discovery; "
        f"child stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "MCP_SERVER_DISCOVERY_ISOLATION_OK" in result.stdout


def test_importing_discovery_itself_succeeds_with_only_the_base_environment_and_no_httpx() -> None:
    # A bare `import knotica.discovery` must not pull `httpx` onto the import
    # path -- every module that needs it (http.py, the adapters, the
    # enricher) defers `import httpx` into a constructor or method body, per
    # the module docstrings. `httpx` is installed in this interpreter (it is
    # a hard transitive dependency of `mcp`), so a leak would land in the
    # child's sys.modules and this assertion would catch it either way.
    script = (
        "import sys\n"
        "import knotica.discovery\n"
        "leaked = sorted(m for m in sys.modules if m == 'httpx' or m.startswith('httpx.'))\n"
        "assert not leaked, leaked\n"
        "print('DISCOVERY_HTTPX_ISOLATION_OK')\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        "importing knotica.discovery must not eagerly import httpx (heavy client "
        f"must stay lazy); child stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "DISCOVERY_HTTPX_ISOLATION_OK" in result.stdout
