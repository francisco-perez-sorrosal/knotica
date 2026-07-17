"""Activity journal — live progress for ingest and curation workflows.

Append-only JSONL at ``.knotica/ingest-activity.jsonl`` (gitignored). Events come
from two sources:

* **Client** — ``ingest_progress`` for cognitive stages (fetch, parse, plan, …)
  that never touch the vault.
* **Server** — automatic appends after successful mutating tools so the user sees
  store/write/curate checkpoints even if the model forgets to report them.

Ingest and curation are separate workflows: curation is optional follow-on work
(and can happen outside ingest), so it must not leave an ingest run stuck
"in progress" on a Curate rail step.

The journal is vault state, not server session state — honors client-as-brain +
stateless server. It is intentionally not git-committed (noisy mid-run noise).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from knotica.store import VaultStore

__all__ = [
    "ACTIVITY_PATH",
    "CURATE_STAGES",
    "INGEST_STAGES",
    "PIPELINE_STAGES",
    "TERMINAL_STAGES",
    "WORKFLOWS",
    "append_ingest_event",
    "read_ingest_activity",
]

#: Vault-relative journal path (gitignored under ``.knotica/``).
ACTIVITY_PATH = ".knotica/ingest-activity.jsonl"

WORKFLOWS = ("ingest", "curate")

#: Canonical ingest stages shown on the Ingest rail (order matters).
INGEST_STAGES = (
    "resolve_topic",
    "read_schema",
    "fetch",
    "parse",
    "plan",
    "store_source",
    "write_page",
    "complete",
)

#: Short curation workflow — one checkpoint, then done.
CURATE_STAGES = (
    "curate",
    "complete",
)

#: Backward-compatible alias — ingest pipeline only (curate is its own workflow).
PIPELINE_STAGES = INGEST_STAGES

TERMINAL_STAGES = frozenset({"complete", "error"})

_MAX_READ = 500
_DEFAULT_LIMIT = 120


def append_ingest_event(
    store: VaultStore,
    vault_path: Path,
    *,
    topic: str,
    stage: str,
    title: str,
    status: str = "info",
    detail: str = "",
    run_id: str = "",
    citation_key: str = "",
    path: str = "",
    commit_sha: str = "",
    source: str = "client",
    workflow: str = "",
) -> dict[str, Any]:
    """Append one activity event; return the written event (with resolved run_id)."""
    cleaned_topic = topic.strip().strip("/") or ""
    cleaned_stage = stage.strip() or "info"
    cleaned_title = title.strip() or cleaned_stage
    cleaned_status = status.strip() or "info"
    cleaned_workflow = _normalize_workflow(workflow, cleaned_stage)
    resolved_run = run_id.strip() or _infer_run_id(
        store,
        cleaned_topic,
        citation_key=citation_key,
        workflow=cleaned_workflow,
    )
    prior = [
        row for row in _read_events_via_store(store) if str(row.get("run_id") or "") == resolved_run
    ]
    event = {
        "schema_version": 1,
        "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_id": resolved_run,
        "workflow": cleaned_workflow,
        "topic": cleaned_topic,
        "stage": cleaned_stage,
        "status": cleaned_status,
        "title": cleaned_title,
        "detail": detail.strip(),
        "citation_key": citation_key.strip(),
        "path": path.strip(),
        "commit_sha": commit_sha.strip(),
        "source": source if source in {"client", "server"} else "client",
        "out_of_order": _is_out_of_order(prior, cleaned_stage, cleaned_workflow),
    }
    _append_line(vault_path, event)
    return event


def read_ingest_activity(
    vault_path: Path,
    *,
    topic: str = "",
    run_id: str = "",
    limit: int = _DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Read and filter the journal for MCP / dashboard consumption."""
    limit = max(1, min(int(limit), _MAX_READ))
    all_events = _read_events(vault_path)
    topic_f = topic.strip().strip("/")
    run_f = run_id.strip()
    filtered = [
        event
        for event in all_events
        if (not topic_f or event.get("topic") == topic_f)
        and (not run_f or event.get("run_id") == run_f)
    ]
    window = filtered[-limit:]
    active = _active_run(filtered)
    runs = _summarize_runs(filtered)
    return {
        "schema_version": 1,
        "activity_path": str(vault_path / ACTIVITY_PATH),
        "pipeline_stages": list(INGEST_STAGES),
        "curate_pipeline_stages": list(CURATE_STAGES),
        "events": window,
        "active_run": active,
        "runs": runs[:20],
        "has_more": len(filtered) > limit,
    }


def _normalize_workflow(workflow: str, stage: str) -> str:
    cleaned = workflow.strip().lower()
    if cleaned in WORKFLOWS:
        return cleaned
    if stage == "curate":
        return "curate"
    return "ingest"


def _infer_run_id(
    store: VaultStore,
    topic: str,
    *,
    citation_key: str,
    workflow: str,
) -> str:
    """Reuse the open run for this topic+workflow, else mint a prefixed id."""
    events = _read_events_via_store(store)
    for event in reversed(events):
        if topic and event.get("topic") not in {"", topic}:
            continue
        event_workflow = _event_workflow(event)
        if event_workflow != workflow:
            continue
        if event.get("stage") in TERMINAL_STAGES:
            break
        # A finished curate checkpoint is terminal even without an explicit complete.
        if workflow == "curate" and event.get("stage") == "curate" and event.get("status") == "ok":
            break
        existing = str(event.get("run_id") or "").strip()
        if existing:
            return existing
    prefix = "curate" if workflow == "curate" else "ingest"
    if citation_key.strip() and workflow == "ingest":
        return f"{prefix}-{citation_key.strip()}"
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _active_run(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not events:
        return None
    by_run: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        rid = str(event.get("run_id") or "")
        by_run.setdefault(rid, []).append(event)
    for rid, rows in sorted(
        by_run.items(), key=lambda item: item[1][-1].get("ts", ""), reverse=True
    ):
        if not rid:
            continue
        summary = _run_summary(rid, rows)
        if summary.get("terminal"):
            continue
        return summary
    rid, rows = max(by_run.items(), key=lambda item: item[1][-1].get("ts", ""))
    return _run_summary(rid, rows) if rid else None


def _summarize_runs(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_run: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        rid = str(event.get("run_id") or "")
        if not rid:
            continue
        by_run.setdefault(rid, []).append(event)
    summaries = [_run_summary(rid, rows) for rid, rows in by_run.items()]
    summaries.sort(key=lambda row: row.get("updated_at") or "", reverse=True)
    return summaries


def _run_summary(run_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize a run with a *monotonic* pipeline watermark.

    Clients sometimes emit cognitive stages late (e.g. ``plan`` after
    ``store_source``). The rail must never jump backward — ``current_stage`` /
    ``stage_index`` follow the furthest protocol stage reached in time order.
    ``current_title`` still reflects the latest event so the pulse stays live.
    """
    workflow = _infer_run_workflow(run_id, rows)
    stages = CURATE_STAGES if workflow == "curate" else INGEST_STAGES
    ranks = {stage: index for index, stage in enumerate(stages)}
    stages_seen: set[str] = set()
    watermark = -1
    watermark_stage = ""
    for row in rows:
        stage = str(row.get("stage") or "")
        if not stage:
            continue
        stages_seen.add(stage)
        # Legacy journals attached curate to ingest runs — ignore for ingest rail.
        if workflow == "ingest" and stage == "curate":
            continue
        if stage in TERMINAL_STAGES:
            continue
        rank = ranks.get(stage)
        if rank is not None and rank >= watermark:
            watermark = rank
            watermark_stage = stage

    last = rows[-1]
    last_stage = str(last.get("stage") or "")
    terminal = _is_terminal(workflow, last_stage, str(last.get("status") or ""), stages_seen)
    if terminal and "complete" in stages_seen:
        current = "complete"
    elif terminal and last_stage == "error":
        current = "error"
    elif terminal and workflow == "curate":
        current = "complete" if "complete" in stages_seen else "curate"
    elif workflow == "ingest" and last_stage == "curate":
        # Detached legacy curate event — show furthest ingest stage.
        current = watermark_stage or "write_page"
    else:
        current = last_stage if last_stage in TERMINAL_STAGES else (watermark_stage or last_stage)

    return {
        "run_id": run_id,
        "workflow": workflow,
        "topic": last.get("topic") or rows[0].get("topic") or "",
        "citation_key": next(
            (str(r.get("citation_key")) for r in reversed(rows) if r.get("citation_key")),
            "",
        ),
        "started_at": rows[0].get("ts"),
        "updated_at": last.get("ts"),
        "current_stage": current,
        "current_title": last.get("title") or current,
        "status": last.get("status") or "info",
        "terminal": terminal,
        "stage_index": ranks.get(current, watermark),
        "event_count": len(rows),
        "stages_seen": [stage for stage in stages if stage in stages_seen],
    }


def _is_terminal(
    workflow: str,
    last_stage: str,
    last_status: str,
    stages_seen: set[str],
) -> bool:
    if last_stage in TERMINAL_STAGES or "complete" in stages_seen or "error" in stages_seen:
        return True
    # A successful curate checkpoint finishes the curation workflow.
    if workflow == "curate" and last_stage == "curate" and last_status == "ok":
        return True
    # Legacy: curate was logged onto an ingest run — don't keep ingest "live".
    if workflow == "ingest" and last_stage == "curate":
        return "write_page" in stages_seen or "store_source" in stages_seen
    return False


def _infer_run_workflow(run_id: str, rows: list[dict[str, Any]]) -> str:
    if run_id.startswith("curate-"):
        return "curate"
    if run_id.startswith("ingest-"):
        return "ingest"
    votes = [_event_workflow(row) for row in rows]
    if votes.count("curate") > votes.count("ingest"):
        return "curate"
    return "ingest"


def _event_workflow(event: dict[str, Any]) -> str:
    return _normalize_workflow(str(event.get("workflow") or ""), str(event.get("stage") or ""))


def _is_out_of_order(prior: list[dict[str, Any]], stage: str, workflow: str) -> bool:
    """True when ``stage`` is earlier in the pipeline than stages already logged."""
    if stage in TERMINAL_STAGES or stage == "error":
        return False
    stages = CURATE_STAGES if workflow == "curate" else INGEST_STAGES
    ranks = {name: index for index, name in enumerate(stages)}
    stage_rank = ranks.get(stage)
    if stage_rank is None:
        return False
    watermark = -1
    for row in prior:
        prior_stage = str(row.get("stage") or "")
        if prior_stage in TERMINAL_STAGES:
            continue
        if workflow == "ingest" and prior_stage == "curate":
            continue
        rank = ranks.get(prior_stage)
        if rank is not None and rank > watermark:
            watermark = rank
    return stage_rank < watermark


def _append_line(vault_path: Path, event: dict[str, Any]) -> None:
    path = vault_path / ACTIVITY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def _read_events(vault_path: Path) -> list[dict[str, Any]]:
    path = vault_path / ACTIVITY_PATH
    if not path.is_file():
        return []
    return _parse_jsonl(path.read_text(encoding="utf-8"))


def _read_events_via_store(store: VaultStore) -> list[dict[str, Any]]:
    if not store.exists(ACTIVITY_PATH):
        return []
    return _parse_jsonl(store.read_text(ACTIVITY_PATH))


def _parse_jsonl(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows
