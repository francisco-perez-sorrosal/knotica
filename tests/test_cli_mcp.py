"""Wire-contract tests for ``knotica mcp`` — the stdio serve command.

``knotica mcp`` turns the process into an MCP stdio server: stdout becomes the
JSON-RPC channel and NOTHING else may land there. A single stray ``print`` — a
banner, a progress line, a log record — corrupts the protocol and dead-ends any
real client. These tests therefore launch the command as a *subprocess* (the
only faithful way to observe the raw stdout bytes a client would parse), drive
one ``initialize`` handshake by hand over newline-delimited JSON-RPC, and assert:

- every byte the server writes to stdout is a well-formed JSON-RPC message
  (purity — the primary contract);
- the server's diagnostic/startup output is routed to stderr, never stdout
  (the complementary half: diagnostics exist, and they stay off the wire).

The handshake is framed manually rather than through the SDK client so a leaked
non-protocol line surfaces as a parse failure we can assert on, instead of being
silently tolerated. Subprocess env inherits the test's already-redirected
``os.environ`` (the config fixtures point HOME/``KNOTICA_CONFIG`` at a tmp vault),
so no real user config is ever read.

RED until the CLI serve command lands: the placeholder entry point ignores the
``mcp`` argument and prints a version banner to stdout — which is exactly the
purity violation these tests catch.
"""

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

_HANDSHAKE_TIMEOUT_S = 25.0
_PROTOCOL_VERSION = "2025-06-18"


def _cli(*args: str) -> list[str]:
    """Invoke the installed ``knotica`` console script, falling back to an
    in-interpreter bootstrap so the command is the same one a user runs."""
    console = Path(sys.executable).with_name("knotica")
    if console.exists():
        return [str(console), *args]
    return [
        sys.executable,
        "-c",
        "import sys; from knotica.cli import main; sys.exit(main())",
        *args,
    ]


def _subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env["NO_COLOR"] = "1"
    return env


def _pump(stream, sink: "queue.Queue[str | None]") -> None:
    for line in stream:
        sink.put(line)
    sink.put(None)


def _assert_jsonrpc_line(line: str) -> dict:
    """Every stdout line must parse as a JSON-RPC 2.0 message — this is the
    purity contract. A banner/log line lands here and fails loudly."""
    stripped = line.strip()
    try:
        message = json.loads(stripped)
    except json.JSONDecodeError as exc:  # pragma: no cover - failure path
        raise AssertionError(
            f"non-JSON-RPC bytes leaked onto the mcp stdout channel: {line!r}"
        ) from exc
    assert isinstance(message, dict) and message.get("jsonrpc") == "2.0", (
        f"stdout carried a non-JSON-RPC payload: {line!r}"
    )
    return message


def _handshake(env: dict[str, str]) -> tuple[dict, str]:
    """Drive one initialize handshake against ``knotica mcp``; return the
    parsed initialize *response* and the full stderr text.

    Asserts along the way that every stdout line is valid JSON-RPC.
    """
    proc = subprocess.Popen(
        _cli("mcp"),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        bufsize=1,
    )
    lines: "queue.Queue[str | None]" = queue.Queue()
    reader = threading.Thread(target=_pump, args=(proc.stdout, lines), daemon=True)
    reader.start()

    try:
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "knotica-tests", "version": "0"},
            },
        }
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(initialize) + "\n")
        proc.stdin.flush()

        response = None
        while response is None:
            try:
                line = lines.get(timeout=_HANDSHAKE_TIMEOUT_S)
            except queue.Empty:  # pragma: no cover - hang guard
                raise AssertionError("timed out waiting for the initialize response")
            if line is None:
                raise AssertionError(
                    "mcp stdout closed before answering initialize "
                    "(the command likely never entered the serve loop)"
                )
            message = _assert_jsonrpc_line(line)
            if message.get("id") == 1:
                response = message

        proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        )
        proc.stdin.flush()
        proc.stdin.close()

        # Drain any trailing stdout — it must remain pure JSON-RPC too.
        while True:
            try:
                line = lines.get(timeout=2.0)
            except queue.Empty:
                break
            if line is None:
                break
            _assert_jsonrpc_line(line)

        try:
            proc.wait(timeout=_HANDSHAKE_TIMEOUT_S)
        except subprocess.TimeoutExpired:  # pragma: no cover - hang guard
            proc.kill()
        stderr_text = proc.stderr.read() if proc.stderr else ""
        return response, stderr_text
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5.0)


def test_mcp_stdout_carries_only_jsonrpc_during_a_handshake(vault_config: Path):
    """The core purity guarantee: across a full initialize handshake, every
    byte the server writes to stdout is a well-formed JSON-RPC message, and the
    initialize response is a proper JSON-RPC result (no error envelope)."""
    response, _ = _handshake(_subprocess_env())

    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 1
    assert "result" in response, f"initialize returned an error instead of a result: {response!r}"
    assert "error" not in response
    assert "protocolVersion" in response["result"]


def test_mcp_routes_diagnostics_to_stderr_not_stdout(vault_config: Path):
    """The complementary half of purity: the server's startup/diagnostic output
    is written to stderr. stdout stays a clean JSON-RPC channel (proven by the
    handshake helper); the diagnostics have to go *somewhere*, and that is
    stderr — never the protocol stream."""
    _response, stderr_text = _handshake(_subprocess_env())

    assert stderr_text.strip() != "", (
        "expected the serve command to emit a startup/diagnostic line on stderr; "
        "an entirely silent server that instead logged to stdout would corrupt "
        "the JSON-RPC channel"
    )


def test_mcp_boots_over_stdio_even_when_the_vault_is_unconfigured(unconfigured_env: Path):
    """Graceful boot at the transport layer: the serve command must complete the
    initialize handshake with a pure-stdout channel even with no config anywhere
    — config resolves per tool call, so serving must never require it up front."""
    response, _ = _handshake(_subprocess_env())

    assert "result" in response
    assert "error" not in response
