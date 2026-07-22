"""Golden-set review load/save — the human gate between bootstrap and freeze.

Pure vault I/O over one topic's ``golden.staging.jsonl`` (or a previously saved
``golden.staging.reviewed.jsonl``). The dashboard Golden pane and the standalone
``scripts/review_golden.py`` both call here so review logic stays single-sourced.
"""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path
from typing import Any

from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.page import TopicNotFoundError
from knotica.core.transaction import VaultTransaction
from knotica.store import VaultStore

__all__ = [
    "FLOOR",
    "REVIEWED_NAME",
    "STAGING_NAME",
    "TARGET_HIGH",
    "load_golden_review",
    "save_golden_review",
    "staging_relative_path",
    "reviewed_relative_path",
]

STAGING_NAME = "golden.staging.jsonl"
REVIEWED_NAME = "golden.staging.reviewed.jsonl"
FLOOR = 20
TARGET_HIGH = 30
_CANDIDATE_KEYS = ("question", "reference_answer", "citations", "pages_used")


def staging_relative_path(topic: str) -> str:
    """Vault-relative path to the bootstrap staging file."""
    return f"{topic}/.knotica/datasets/{STAGING_NAME}"


def reviewed_relative_path(topic: str) -> str:
    """Vault-relative path to the human-reviewed output freeze consumes."""
    return f"{topic}/.knotica/datasets/{REVIEWED_NAME}"


def load_golden_review(
    store: VaultStore,
    vault_path: Path,
    topic: str,
    *,
    vault_name: str = "",
) -> dict[str, Any]:
    """Load staging/reviewed candidates plus citation/page enrichment for the UI."""
    cleaned = _require_topic(store, topic)
    staging = staging_relative_path(cleaned)
    reviewed = reviewed_relative_path(cleaned)
    source = reviewed if store.exists(reviewed) else staging
    if not store.exists(source):
        raise KnoticaError(
            code=ErrorCode.PAGE_NOT_FOUND,
            message=(
                f"no golden staging file at {staging} — run "
                f"`knotica eval --bootstrap --topic {cleaned}` first"
            ),
            fix="Bootstrap the golden set for this topic, then reopen the Golden pane.",
        )

    candidates = _read_jsonl(store.read_text(source))
    _enrich_support_offsets(store, vault_path, cleaned, candidates)
    sources_dir = vault_path / "sources" / cleaned
    source_keys = sorted(p.stem for p in sources_dir.glob("*.md")) if sources_dir.is_dir() else []
    display_name = vault_name or vault_path.name
    return {
        "topic": cleaned,
        "vault_name": display_name,
        "vault_path": str(vault_path),
        "candidates": candidates,
        "pages": _page_provenance(store, vault_path, cleaned, candidates),
        "citation_links": {
            key: "obsidian://open?path="
            + urllib.parse.quote(str(sources_dir / f"{key}.md"), safe="")
            for key in source_keys
        },
        "source_keys": source_keys,
        "qa_questions": sorted(_qa_questions(store, cleaned)),
        "floor": FLOOR,
        "target_high": TARGET_HIGH,
        "resumed": source == reviewed,
        "loaded_from": str(vault_path / source),
        "reviewed_path": str(vault_path / reviewed),
        # Decision-envelope (additive): what's newly available in the fresh
        # staging pool vs. what would be displaced from the last frozen
        # reviewed set -- shown before golden(action=save)/freeze commits.
        "diff": _candidate_diff(store, staging, reviewed),
    }


def save_golden_review(
    store: VaultStore,
    vault_path: Path,
    topic: str,
    accepted: list[dict[str, Any]],
) -> dict[str, Any]:
    """Normalize kept candidates and commit them as ``golden.staging.reviewed.jsonl``."""
    cleaned = _require_topic(store, topic)
    rows = [_normalized_candidate(row) for row in accepted]
    payload = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    relative = reviewed_relative_path(cleaned)
    with VaultTransaction(
        store,
        vault_path,
        "golden_review",
        cleaned,
        f"save {len(rows)} reviewed golden candidates",
    ) as txn:
        txn.write(relative, payload)
    result = txn.result
    assert result is not None
    payload = {
        "written": str(vault_path / relative),
        "count": len(rows),
        "commit_sha": result.commit_sha,
    }
    if len(rows) >= FLOOR:
        from knotica.core.baseline_probe import maybe_auto_baseline_probe

        probe = maybe_auto_baseline_probe(store, vault_path, cleaned)
        if probe is not None:
            payload["baseline_probe"] = probe.render()
    return payload


def _require_topic(store: VaultStore, topic: str) -> str:
    cleaned = topic.strip().strip("/")
    if not cleaned or "/" in cleaned:
        raise TopicNotFoundError(topic or "(empty)")
    if not store.exists(cleaned):
        raise TopicNotFoundError(cleaned)
    try:
        store.list_dir(cleaned)
    except (NotADirectoryError, FileNotFoundError) as exc:
        raise TopicNotFoundError(cleaned) from exc
    return cleaned


def _read_jsonl(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise KnoticaError(
                code=ErrorCode.INVALID_FRONTMATTER,
                message=f"golden staging line {number} is not valid JSON: {exc}",
                fix="Fix or re-bootstrap the staging file.",
            ) from exc
        if not isinstance(row, dict):
            raise KnoticaError(
                code=ErrorCode.INVALID_FRONTMATTER,
                message=f"golden staging line {number} is not a JSON object",
                fix="Fix or re-bootstrap the staging file.",
            )
        rows.append(row)
    return rows


def _candidate_diff(store: VaultStore, staging: str, reviewed: str) -> dict[str, Any]:
    """Decision-envelope ``diff``: staging-pool questions not yet frozen, and
    frozen questions that dropped out of the fresh staging pool.

    Read-only and independent of which file :func:`load_golden_review` picked
    as its working ``candidates`` -- an absent file on either side degrades to
    an empty set rather than erroring.
    """
    staging_questions = _candidate_questions(store, staging)
    reviewed_questions = _candidate_questions(store, reviewed)
    added = sorted(staging_questions - reviewed_questions)
    displaced = sorted(reviewed_questions - staging_questions)
    return {
        "added": added,
        "displaced": displaced,
        "diff_summary": f"{len(added)} added, {len(displaced)} displaced since last freeze",
    }


def _candidate_questions(store: VaultStore, path: str) -> set[str]:
    """The distinct ``question`` values in one candidate JSONL file (empty if absent)."""
    if not store.exists(path):
        return set()
    return {
        str(row["question"]) for row in _read_jsonl(store.read_text(path)) if row.get("question")
    }


def _qa_questions(store: VaultStore, topic: str) -> set[str]:
    path = f"{topic}/.knotica/datasets/qa.jsonl"
    if not store.exists(path):
        return set()
    questions: set[str] = set()
    for row in _read_jsonl(store.read_text(path)):
        query = str(row.get("query", "")).strip().lower()
        if query:
            questions.add(query)
    return questions


def _normalized_candidate(row: dict[str, Any]) -> dict[str, Any]:
    missing = [key for key in _CANDIDATE_KEYS if key not in row]
    if missing:
        raise KnoticaError(
            code=ErrorCode.INVALID_FRONTMATTER,
            message=f"candidate is missing {missing}",
            fix="Keep question, reference_answer, citations, and pages_used on every candidate.",
        )
    question = str(row["question"]).strip()
    answer = str(row["reference_answer"]).strip()
    if not question or not answer:
        raise KnoticaError(
            code=ErrorCode.INVALID_FRONTMATTER,
            message="candidate question and reference_answer must be non-empty",
            fix="Fill both fields or discard the candidate.",
        )
    citations = [str(key).strip() for key in row["citations"] if str(key).strip()]
    pages = [str(page).strip() for page in row["pages_used"] if str(page).strip()]
    normalized: dict[str, Any] = {
        "question": question,
        "reference_answer": answer,
        "citations": citations,
        "pages_used": pages,
    }
    support = row.get("support")
    if isinstance(support, list) and support:
        normalized["support"] = support
    return normalized


def _page_provenance(
    store: VaultStore,
    vault_path: Path,
    topic: str,
    candidates: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    pages: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        for page in candidate.get("pages_used", []):
            name = str(page).strip()
            if not name or name in pages:
                continue
            pages[name] = _resolve_page(store, vault_path, topic, name)
    return pages


def _resolve_page(store: VaultStore, vault_path: Path, topic: str, name: str) -> dict[str, Any]:
    relative = name if name.endswith(".md") else f"{name}.md"
    topic_rel = f"{topic}/{relative}" if not relative.startswith(f"{topic}/") else relative
    for candidate in (topic_rel, relative):
        if store.exists(candidate):
            abs_path = vault_path / candidate
            return {
                "exists": True,
                "relative": candidate,
                "obsidian_uri": "obsidian://open?path="
                + urllib.parse.quote(str(abs_path), safe=""),
            }
    abs_path = vault_path / topic_rel
    return {
        "exists": False,
        "relative": topic_rel,
        "obsidian_uri": "obsidian://open?path=" + urllib.parse.quote(str(abs_path), safe=""),
    }


def _enrich_support_offsets(
    store: VaultStore,
    vault_path: Path,
    topic: str,
    candidates: list[dict[str, Any]],
) -> None:
    raw_cache: dict[str, str | None] = {}
    for candidate in candidates:
        for entry in candidate.get("support", []) or []:
            if not isinstance(entry, dict):
                continue
            page = str(entry.get("page", "")).strip()
            quote = str(entry.get("quote", ""))
            if not page or not quote:
                continue
            if page not in raw_cache:
                info = _resolve_page(store, vault_path, topic, page)
                if info["exists"]:
                    raw_cache[page] = store.read_text(info["relative"])
                else:
                    raw_cache[page] = None
            raw = raw_cache[page]
            located = _locate_quote(raw, quote) if raw is not None else None
            if located:
                entry["current"] = located


def _locate_quote(raw: str, quote: str) -> dict[str, int] | None:
    start = raw.find(quote)
    end = start + len(quote)
    if start == -1:
        normalized_raw, offset_map = _normalize_with_offsets(raw)
        normalized_quote, _ = _normalize_with_offsets(quote)
        if not normalized_quote:
            return None
        hit = normalized_raw.find(normalized_quote)
        if hit == -1:
            return None
        start = offset_map[hit]
        end = offset_map[hit + len(normalized_quote) - 1] + 1
    return {
        "char_start": start,
        "char_end": end,
        "line_start": raw.count("\n", 0, start) + 1,
        "line_end": raw.count("\n", 0, max(start, end - 1)) + 1,
    }


def _normalize_with_offsets(text: str) -> tuple[str, list[int]]:
    chars: list[str] = []
    offsets: list[int] = []
    in_space = False
    for index, char in enumerate(text):
        if char.isspace():
            if chars and not in_space:
                chars.append(" ")
                offsets.append(index)
            in_space = True
        else:
            chars.append(char)
            offsets.append(index)
            in_space = False
    if chars and chars[-1] == " ":
        chars.pop()
        offsets.pop()
    return "".join(chars), offsets
