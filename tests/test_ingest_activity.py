"""Tests for ingest activity journal + MCP tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio

from knotica.core.ingest_activity import append_ingest_event, read_ingest_activity
from knotica.store import LocalFSStore

TOPIC = "agentic-systems"


def _build_server() -> Any:
    from knotica.mcp_server import server as server_mod

    return server_mod.build_server()


async def _call(server: Any, tool: str, args: dict[str, Any]) -> Any:
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(server) as session:
        await session.initialize()
        return await session.call_tool(tool, args)


def call_tool(tool: str, args: dict[str, Any]) -> Any:
    return anyio.run(_call, _build_server(), tool, args)


def payload_of(result: Any) -> Any:
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text is not None:
            return json.loads(text)
    raise AssertionError(f"no payload: {result!r}")


def test_append_and_read_activity_journal(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / ".knotica").mkdir(parents=True)
    store = LocalFSStore(vault)
    first = append_ingest_event(
        store,
        vault,
        topic=TOPIC,
        stage="fetch",
        title="Fetching source",
        status="started",
        run_id="ingest-demo",
    )
    assert first["run_id"] == "ingest-demo"
    append_ingest_event(
        store,
        vault,
        topic=TOPIC,
        stage="store_source",
        title="Stored wang2024",
        status="ok",
        citation_key="wang2024",
        path=f"sources/{TOPIC}/wang2024.md",
        source="server",
    )
    payload = read_ingest_activity(vault, topic=TOPIC)
    assert len(payload["events"]) == 2
    assert payload["active_run"] is not None
    assert payload["active_run"]["run_id"] == "ingest-demo"
    assert "fetch" in payload["pipeline_stages"]


def test_ingest_tools_registered_and_round_trip(vault_config: Path) -> None:
    del vault_config

    async def _list() -> list[str]:
        from mcp.shared.memory import create_connected_server_and_client_session

        async with create_connected_server_and_client_session(_build_server()) as session:
            await session.initialize()
            listed = await session.list_tools()
            return sorted(t.name for t in listed.tools)

    names = anyio.run(_list)
    assert "ingest_progress" in names
    assert "ingest_activity_read" in names

    progress = payload_of(
        call_tool(
            "ingest_progress",
            {
                "topic": TOPIC,
                "stage": "parse",
                "title": "Parsing markdown",
                "status": "ok",
            },
        )
    )
    assert "error" not in progress
    run_id = progress["run_id"]
    assert run_id

    activity = payload_of(call_tool("ingest_activity_read", {"topic": TOPIC, "run_id": run_id}))
    assert "error" not in activity
    assert any(event["title"] == "Parsing markdown" for event in activity["events"])


def test_store_source_auto_logs_activity(vault_config: Path, template_vault: Path) -> None:
    del vault_config
    result = payload_of(
        call_tool(
            "store_source",
            {
                "topic": TOPIC,
                "citation_key": "demo2026ingest",
                "title": "Demo ingest source",
                "content": "# Demo\n\nBody for ingest activity auto-log.\n",
                "source_url": "https://example.com/demo",
                "source_type": "markdown",
            },
        )
    )
    assert "error" not in result
    activity = read_ingest_activity(template_vault, topic=TOPIC)
    assert any(
        event.get("stage") == "store_source" and event.get("citation_key") == "demo2026ingest"
        for event in activity["events"]
    )


def test_curate_is_separate_terminal_workflow(tmp_path: Path) -> None:
    """Curation must not leave the ingest rail stuck in progress."""
    vault = tmp_path / "vault"
    (vault / ".knotica").mkdir(parents=True)
    store = LocalFSStore(vault)
    append_ingest_event(
        store,
        vault,
        topic=TOPIC,
        stage="write_page",
        title="Wrote pages",
        status="ok",
        run_id="ingest-paper",
        source="server",
    )
    append_ingest_event(
        store,
        vault,
        topic=TOPIC,
        stage="complete",
        title="Ingest done",
        status="ok",
        run_id="ingest-paper",
    )
    curate = append_ingest_event(
        store,
        vault,
        topic=TOPIC,
        stage="curate",
        title="Curated example (good)",
        status="ok",
        workflow="curate",
        source="server",
    )
    assert curate["workflow"] == "curate"
    assert curate["run_id"].startswith("curate-")
    append_ingest_event(
        store,
        vault,
        topic=TOPIC,
        stage="complete",
        title="Curation complete",
        status="ok",
        run_id=curate["run_id"],
        workflow="curate",
        source="server",
    )

    payload = read_ingest_activity(vault, topic=TOPIC)
    assert "curate" not in payload["pipeline_stages"]
    assert payload["curate_pipeline_stages"] == ["curate", "complete"]
    by_id = {row["run_id"]: row for row in payload["runs"]}
    assert by_id["ingest-paper"]["workflow"] == "ingest"
    assert by_id["ingest-paper"]["terminal"] is True
    assert by_id[curate["run_id"]]["workflow"] == "curate"
    assert by_id[curate["run_id"]]["terminal"] is True


def test_legacy_curate_on_ingest_run_does_not_stay_live(tmp_path: Path) -> None:
    """Old journals logged curate on the ingest run — treat as finished."""
    vault = tmp_path / "vault"
    (vault / ".knotica").mkdir(parents=True)
    store = LocalFSStore(vault)
    run_id = "ingest-legacy-curate"
    append_ingest_event(
        store,
        vault,
        topic=TOPIC,
        stage="write_page",
        title="Wrote pages",
        status="ok",
        run_id=run_id,
        source="server",
    )
    append_ingest_event(
        store,
        vault,
        topic=TOPIC,
        stage="curate",
        title="Curated example (good)",
        status="ok",
        run_id=run_id,
        source="server",
    )
    payload = read_ingest_activity(vault, topic=TOPIC, run_id=run_id)
    active = payload["active_run"]
    assert active is not None
    assert active["terminal"] is True
    assert active["current_stage"] == "complete"
    assert "curate" not in payload["pipeline_stages"]


def test_orphan_curate_under_ingest_run_id_is_terminal(tmp_path: Path) -> None:
    """Pre-workflow journals minted ingest-* for curate-only events — must not stay live."""
    vault = tmp_path / "vault"
    (vault / ".knotica").mkdir(parents=True)
    store = LocalFSStore(vault)
    # Mimic the legacy line shape (no workflow key) by writing JSONL directly.
    line = (
        '{"schema_version": 1, "ts": "2026-07-17T21:08:58Z", '
        '"run_id": "ingest-53230b5506", "topic": "agentic-systems", '
        '"stage": "curate", "status": "ok", "title": "Curated example (good)", '
        '"detail": "q", "citation_key": "", "path": "", "commit_sha": "", '
        '"source": "server"}\n'
    )
    (vault / ".knotica" / "ingest-activity.jsonl").write_text(line, encoding="utf-8")
    payload = read_ingest_activity(vault, topic=TOPIC)
    active = payload["active_run"]
    assert active is not None
    assert active["run_id"] == "ingest-53230b5506"
    assert active["workflow"] == "curate"
    assert active["terminal"] is True
    assert active["current_stage"] == "complete"
    assert active["current_title"] == "Curated example (good)"


def test_late_plan_marks_out_of_order_and_keeps_rail_forward(tmp_path: Path) -> None:
    """Clients sometimes emit plan after store_source — rail must not regress."""
    vault = tmp_path / "vault"
    (vault / ".knotica").mkdir(parents=True)
    store = LocalFSStore(vault)
    run_id = "ingest-late-plan"
    append_ingest_event(
        store,
        vault,
        topic=TOPIC,
        stage="fetch",
        title="Fetch",
        status="ok",
        run_id=run_id,
    )
    append_ingest_event(
        store,
        vault,
        topic=TOPIC,
        stage="store_source",
        title="Stored paper",
        status="ok",
        citation_key="late2026",
        run_id=run_id,
        source="server",
    )
    late = append_ingest_event(
        store,
        vault,
        topic=TOPIC,
        stage="plan",
        title="Planning pages (late)",
        status="ok",
        run_id=run_id,
    )
    assert late["out_of_order"] is True

    payload = read_ingest_activity(vault, topic=TOPIC, run_id=run_id)
    active = payload["active_run"]
    assert active is not None
    assert active["current_stage"] == "store_source"
    assert active["stage_index"] >= payload["pipeline_stages"].index("store_source")
    assert active["current_title"] == "Planning pages (late)"
