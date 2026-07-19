"""Trainset cold-start — seed ``qa.jsonl`` from the topic's own pages.

A fresh topic has an empty flywheel, so DSPy compile is unreachable until ~30
curated examples exist. This module bridges that cold start the data-driven
way: the injected LLM synthesizes query-style QA pairs **grounded in the
topic's entity pages** (never hardcoded content), written with
``source: seed_train`` so they are distinguishable from human curation forever.
The improvement ratchet lives downstream: demo selection prefers curated
records over seeded ones, so real usage progressively displaces the cold-start
scaffolding without any migration step.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePath

from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.operations.create_topic import qa_dataset_path
from knotica.core.records import QARecord, parse_qa_jsonl
from knotica.core.transaction import VaultTransaction
from knotica.core.page import Page
from knotica.evals.golden import (
    GoldenSetMissingError,
    entity_pages,
    load as load_golden,
)
from knotica.evals.llm import LLMClient, Message
from knotica.store import VaultStore

__all__ = ["SEED_SOURCE", "bootstrap_trainset"]

#: Machine-seeded cold-start records carry this ``qa.jsonl`` source value —
#: already in the frozen ``QA_SOURCES`` enum, so every parser accepts them.
SEED_SOURCE = "seed_train"

_DEFAULT_TARGET = 30
_DEFAULT_PER_PAGE = 5
_MAX_TOKENS = 2000

_SYNTH_SYSTEM = (
    "You write training examples for a wiki question-answering system. "
    "Given one wiki page, produce diverse question/answer pairs that are "
    "answerable strictly from that page. Respond with a JSON array only: "
    '[{"question": "...", "answer": "..."}, ...]. Questions must be phrased '
    "naturally and differ in angle (definitions, mechanisms, comparisons, "
    "implications). Answers are 1-3 sentences, grounded in the page text."
)


def bootstrap_trainset(
    store: VaultStore,
    vault_root: str | PurePath,
    topic: str,
    llm_client: LLMClient,
    snapshot: str,
    *,
    target_n: int = _DEFAULT_TARGET,
    per_page: int = _DEFAULT_PER_PAGE,
    pages: Sequence[str] | None = None,
    on_page: Callable[[int, int, str], None] | None = None,
) -> dict[str, object]:
    """Synthesize up to ``target_n`` seeded train records from the topic's pages.

    Every generated question is deduplicated against the existing trainset and
    excluded when it collides with the held-out golden set (contamination guard
    at generation time, not just read time). Records land in one
    ``VaultTransaction`` commit with ``source: seed_train`` and the generating
    ``snapshot`` recorded in ``model`` — auditable cold-start provenance.

    ``pages`` optionally restricts synthesis to a subset of the topic's entity
    pages, matched by vault-relative path and filtered in place (existing page
    order is preserved). ``None`` (the default) synthesizes from every entity
    page — today's behavior, byte-identical. An explicit empty sequence selects
    zero pages and returns with zero appended records rather than falling back
    to "all pages".
    """
    cleaned = topic.strip().strip("/")
    if not cleaned or "/" in cleaned or not store.exists(cleaned):
        raise KnoticaError(
            ErrorCode.TOPIC_NOT_FOUND,
            f"trainset bootstrap failed because topic {topic!r} does not exist.",
        )
    all_pages = entity_pages(store, cleaned)
    if not all_pages:
        raise KnoticaError(
            ErrorCode.NOT_CONFIGURED,
            f"trainset bootstrap failed because topic '{cleaned}' has no entity pages.",
            fix="Ingest at least one source into the topic first.",
        )
    selected_pages = all_pages if pages is None else _filter_pages(all_pages, pages)

    try:
        golden_questions = {record.query.strip() for record in load_golden(store, cleaned)}
    except GoldenSetMissingError:
        golden_questions = set()

    dataset_path = qa_dataset_path(cleaned)
    existing = parse_qa_jsonl(store.read_text(dataset_path)) if store.exists(dataset_path) else []
    seen = {record.query.strip() for record in existing} | golden_questions

    created = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_records: list[QARecord] = []
    for index, page in enumerate(selected_pages, start=1):
        if len(new_records) >= target_n:
            break
        if on_page is not None:
            try:
                on_page(index, len(selected_pages), page.path)
            except Exception:  # noqa: BLE001 — progress must never break the run
                pass
        for question, answer in _synthesize_pairs(llm_client, snapshot, page, per_page):
            if len(new_records) >= target_n:
                break
            if question.strip() in seen:
                continue
            seen.add(question.strip())
            new_records.append(
                QARecord(
                    id=f"coldstart-{len(new_records) + 1:04d}",
                    topic=cleaned,
                    created=created,
                    query=question,
                    pages_used=(page.path,),
                    answer=answer,
                    citations=tuple(
                        str(source) for source in ((page.frontmatter or {}).get("sources") or ())
                    ),
                    verdict="good",
                    corrected_answer=None,
                    source=SEED_SOURCE,
                    model=snapshot,
                )
            )

    if not new_records:
        if pages is not None and not selected_pages:
            # An explicit page filter matched nothing (including an explicit
            # empty `pages=[]`) — a deliberate zero-page request, not a
            # dedup-exhaustion failure, so it succeeds with nothing appended.
            return {
                "topic": cleaned,
                "appended": 0,
                "pages_read": 0,
                "path": dataset_path,
                "source": SEED_SOURCE,
                "snapshot": snapshot,
            }
        raise KnoticaError(
            ErrorCode.NOT_CONFIGURED,
            "trainset bootstrap produced no new records (all candidates duplicated "
            "existing trainset or golden questions).",
            fix="Ingest more pages, or grow the trainset via curate_example.",
        )

    body = "".join(record.to_json_line() + "\n" for record in existing + new_records)
    with VaultTransaction(
        store, Path(vault_root), SEED_SOURCE, cleaned, "bootstrap trainset from pages"
    ) as txn:
        txn.write(dataset_path, body)

    return {
        "topic": cleaned,
        "appended": len(new_records),
        "pages_read": len(selected_pages),
        "path": dataset_path,
        "source": SEED_SOURCE,
        "snapshot": snapshot,
    }


def _filter_pages(pages: list[Page], allowed: Sequence[str]) -> list[Page]:
    """Restrict ``pages`` to those whose path is in ``allowed``, preserving order."""
    allowed_paths = set(allowed)
    return [page for page in pages if page.path in allowed_paths]


def _synthesize_pairs(
    llm_client: LLMClient, snapshot: str, page: Page, per_page: int
) -> list[tuple[str, str]]:
    """One LLM call → up to ``per_page`` grounded (question, answer) pairs."""
    body = page.body
    title = page.path
    completion = llm_client.complete(
        snapshot=snapshot,
        system=_SYNTH_SYSTEM,
        messages=[
            Message(
                role="user",
                content=(
                    f"Wiki page `{title}`:\n\n{body}\n\n"
                    f"Produce exactly {per_page} question/answer pairs as a JSON array."
                ),
            )
        ],
        temperature=0.0,
        max_tokens=_MAX_TOKENS,
    )
    return _parse_pairs(completion.text, per_page)


def _parse_pairs(text: str, per_page: int) -> list[tuple[str, str]]:
    """Parse the JSON-array response; tolerate fenced output; bound the count."""
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[raw.find("[") :] if "[" in raw else raw
    try:
        payload = json.loads(raw)
    except ValueError as error:
        raise KnoticaError(
            ErrorCode.NOT_CONFIGURED,
            f"trainset bootstrap could not parse a synthesis response: {error}",
            fix="Rerun; persistent failures suggest the snapshot ignores the JSON contract.",
        ) from error
    pairs: list[tuple[str, str]] = []
    if isinstance(payload, list):
        for item in payload[:per_page]:
            if not isinstance(item, dict):
                continue
            question = str(item.get("question") or "").strip()
            answer = str(item.get("answer") or "").strip()
            if question and answer:
                pairs.append((question, answer))
    return pairs
