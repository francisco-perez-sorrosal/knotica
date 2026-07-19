"""Cold-start import-boundary fitness test, extended to ``discovery`` .

Mirrors the fresh-interpreter subprocess pattern already established at
``tests/test_evals_llm.py::test_importing_the_llm_module_does_not_import_the_anthropic_sdk``
and ``tests/test_evals_config.py::test_importing_the_config_module_imports_neither_dspy_nor_anthropic``:
a same-process check would false-positive if an earlier test in the suite
already imported ``discovery`` or ``httpx``, so every assertion here runs a
fresh child interpreter and inspects *its* ``sys.modules``.

Three properties are pinned (pre-mortem guard 5 -- a future edit to
``discovery/__init__.py`` that eagerly imports the heavy chain must be caught
automatically, not once and never re-run):

- importing ``knotica.mcp_server`` must never transitively pull in
  ``knotica.discovery`` -- the MCP cold-start path stays off this package
  entirely;
- importing ``knotica.discovery`` itself must succeed with only the base
  environment and must never construct the heavy ``httpx`` client at import
  time -- every adapter/enricher/http-wrapper module defers its ``import
  httpx`` into a constructor or method body, never the module top level;
- importing ``knotica.core.records`` must never transitively pull in
  ``knotica.discovery`` -- P3's ``SuggestionRecord`` embeds a discovered
  candidate as an opaque ``dict``, not a typed ``SourceCandidate``, precisely
  so this leaf-of-``core`` module (itself imported at MCP cold start) gains
  zero edge into ``discovery/`` (Decision B).
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


def test_importing_core_records_does_not_transitively_import_discovery() -> None:
    # SuggestionRecord.candidate is a verbatim SourceCandidate.to_record() dict
    # (Decision B) -- typing it as knotica.discovery.SourceCandidate would drag
    # the whole discovery package onto the MCP cold-start path via core.records.
    script = (
        "import sys\n"
        "import knotica.core.records\n"
        "leaked = sorted(\n"
        "    m for m in sys.modules\n"
        "    if m == 'knotica.discovery' or m.startswith('knotica.discovery.')\n"
        ")\n"
        "assert not leaked, leaked\n"
        "print('CORE_RECORDS_DISCOVERY_ISOLATION_OK')\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        "importing knotica.core.records must not transitively import knotica.discovery; "
        f"child stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "CORE_RECORDS_DISCOVERY_ISOLATION_OK" in result.stdout


def test_importing_core_status_does_not_transitively_import_discovery() -> None:
    # wiki_status's new suggestions/gapfill count block reads suggestions.jsonl
    # read-only via core.records -- core.status must gain zero edge into
    # discovery/, exactly like core.records itself (Decision B applied to the
    # status-gathering module).
    script = (
        "import sys\n"
        "import knotica.core.status\n"
        "leaked = sorted(\n"
        "    m for m in sys.modules\n"
        "    if m == 'knotica.discovery' or m.startswith('knotica.discovery.')\n"
        ")\n"
        "assert not leaked, leaked\n"
        "print('CORE_STATUS_DISCOVERY_ISOLATION_OK')\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        "importing knotica.core.status must not transitively import knotica.discovery; "
        f"child stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "CORE_STATUS_DISCOVERY_ISOLATION_OK" in result.stdout


def test_importing_mcp_server_does_not_transitively_import_discovery_via_suggestions_tools() -> (
    None
):
    # Regression guard now that mcp_server/tools_suggestions.py exists on the
    # cold-start path (Group C): suggestions_read/suggestions_review must
    # delegate to core.gapfill's discovery-free surfaces only.
    script = (
        "import sys\n"
        "import knotica.mcp_server.tools_suggestions\n"
        "leaked = sorted(\n"
        "    m for m in sys.modules\n"
        "    if m == 'knotica.discovery' or m.startswith('knotica.discovery.')\n"
        ")\n"
        "assert not leaked, leaked\n"
        "print('TOOLS_SUGGESTIONS_DISCOVERY_ISOLATION_OK')\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        "importing knotica.mcp_server.tools_suggestions must not transitively import "
        f"knotica.discovery; child stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "TOOLS_SUGGESTIONS_DISCOVERY_ISOLATION_OK" in result.stdout


def test_importing_cli_gapfill_module_does_not_transitively_import_discovery() -> None:
    # Group E: knotica.cli.gapfill must lazy-import discovery/ inside the
    # command body (build_default_discovery_service), never at module load --
    # importing the module alone (before argparse dispatches into run()) must
    # not pull discovery onto the CLI's own cold-start path.
    script = (
        "import sys\n"
        "import knotica.cli.gapfill\n"
        "leaked = sorted(\n"
        "    m for m in sys.modules\n"
        "    if m == 'knotica.discovery' or m.startswith('knotica.discovery.')\n"
        ")\n"
        "assert not leaked, leaked\n"
        "print('CLI_GAPFILL_DISCOVERY_ISOLATION_OK')\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        "importing knotica.cli.gapfill must not transitively import knotica.discovery at "
        f"module load; child stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "CLI_GAPFILL_DISCOVERY_ISOLATION_OK" in result.stdout
