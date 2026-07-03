"""Behavioral contract tests for the frozen record shapes (``knotica.core.records``).

The contract source is ``vault-template/SCHEMA.md`` §Machine-record schemas (the
versioned constitution, as amended after the Phase-0 live-vault validation) — the
tests are derived from those five frozen formats, never from the implementation.
``metrics.jsonl`` is documented in the constitution but has no Phase-1 producer,
so no record kind is expected (or tested) for it yet; the four code-backed kinds
are:

- **qa.jsonl record** — one JSON object per line; twelve frozen fields;
  ``verdict`` in ``good | bad | corrected``; per-record ``schema_version``.
- **log entry** — ``## [YYYY-MM-DD] <op> | <topic> | <title>`` plus optional
  touched-page bullets (``- <path>``), versioned by the constitution.
- **commit message** — ``knotica(<op>): <topic> — <title>``, em-dash separator
  with surrounding spaces, versioned by the constitution.
- **source provenance** — nine frozen frontmatter fields over an immutable body;
  ``sha256`` is the digest of exactly the bytes after the frontmatter block's
  trailing blank line, trailing newline included.

Expected interface (probed, so the implementer keeps naming freedom): a parse
callable and a render callable per record kind, either as module functions or as
(class)methods — the probes in the helpers below list every accepted spelling
and fail with a diagnostic naming all attempts. Records are treated as opaque
wherever possible: field sets and values are asserted on the *serialized* form
(the frozen contract), cross-validated against the independent frozen-grammar
parsers in ``tests/support/vault.py``.

Real-corpus acceptance draws on every real instance that exists today: the
template ``log.md`` entries, the template demo source's provenance frontmatter,
and the live-vault commit subjects recorded during the Phase-0 validation.

Production imports are deferred into helpers so collection succeeds while the
module under test is still in flight.
"""

import hashlib
import json
from pathlib import Path

import pytest

from support.vault import KNOTICA_COMMIT_RE, LOG_ENTRY_RE, parse_frontmatter

# ---------------------------------------------------------------------------
# Frozen contract constants (vault-template/SCHEMA.md §Machine-record schemas)
# ---------------------------------------------------------------------------

QA_FIELDS = frozenset(
    {
        "id",
        "schema_version",
        "topic",
        "created",
        "query",
        "pages_used",
        "answer",
        "citations",
        "verdict",
        "corrected_answer",
        "source",
        "model",
    }
)

PROVENANCE_FIELDS = frozenset(
    {
        "schema_version",
        "type",
        "topic",
        "citation_key",
        "retrieved",
        "origin_url",
        "sha256",
        "source_type",
        "ingested_by",
    }
)

# Real corpus: the eleven live-vault commit subjects produced by the Phase-0
# manual ingest exercise (ReAct + Darwin Gödel Machine, 2026-07-03), copied
# verbatim from the validation record's `git log` inventory. They are the only
# knotica-grammar commits in existence today and pin the grammar against real
# titles (parentheses, commas, colons, an en-dash, arXiv ids).
LIVE_COMMIT_CORPUS = (
    ("knotica(store_source): agentic-systems — ReAct, arXiv 2210.03629", "store_source"),
    ("knotica(write_page): agentic-systems — ReAct (Yao et al., 2022)", "write_page"),
    ("knotica(write_page): agentic-systems — ReAct prompting", "write_page"),
    ("knotica(write_page): agentic-systems — Reasoning–acting synergy", "write_page"),
    (
        "knotica(write_page): agentic-systems — Agent Workflow Memory: link ReAct relation",
        "write_page",
    ),
    (
        "knotica(store_source): agentic-systems — Darwin Godel Machine, arXiv 2505.22954",
        "store_source",
    ),
    (
        "knotica(write_page): agentic-systems — Darwin Godel Machine (Zhang et al., 2025)",
        "write_page",
    ),
    ("knotica(write_page): agentic-systems — Self-improving agents", "write_page"),
    ("knotica(write_page): agentic-systems — Open-ended exploration", "write_page"),
    ("knotica(write_page): agentic-systems — SWE-bench", "write_page"),
    (
        "knotica(write_page): agentic-systems — Agent memory: link self-improving-agents relation",
        "write_page",
    ),
)

DEMO_SOURCE = "sources/agentic-systems/wang2024awm.md"


# ---------------------------------------------------------------------------
# Interface probes (parse + render per record kind; diagnostic on miss)
# ---------------------------------------------------------------------------


def _records_module():
    import knotica.core.records

    return knotica.core.records


def _public_names(mod) -> list[str]:
    return sorted(name for name in dir(mod) if not name.startswith("_"))


def _resolve_parse(fn_names: tuple[str, ...], class_specs: tuple[tuple[str, ...], ...]):
    """A parse callable: first matching module function, else class + classmethod."""
    mod = _records_module()
    for name in fn_names:
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn
    class_names, method_names = class_specs
    for cls_name in class_names:
        cls = getattr(mod, cls_name, None)
        if cls is None:
            continue
        for method in method_names:
            fn = getattr(cls, method, None)
            if callable(fn):
                return fn
    raise AttributeError(
        f"knotica.core.records exposes no parse entry point: tried functions {fn_names}, "
        f"classmethods {class_names} x {method_names}; module has {_public_names(mod)}"
    )


def _render_via(record, fn_names: tuple[str, ...], method_names: tuple[str, ...]) -> str:
    """Render a record to its frozen serialized form (module function or method)."""
    mod = _records_module()
    for name in fn_names:
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn(record)
    for method in method_names:
        bound = getattr(record, method, None)
        if callable(bound):
            return bound()
    raise AttributeError(
        f"no render entry point for {type(record).__name__}: tried module functions "
        f"{fn_names} and methods {method_names}; module has {_public_names(mod)}"
    )


def _parse_qa(line: str):
    return _resolve_parse(
        ("parse_qa_record", "parse_qa_line", "qa_record_from_line"),
        (
            ("QARecord", "QAExample", "QaRecord", "CuratedExample"),
            ("from_line", "from_json_line", "from_jsonl", "parse"),
        ),
    )(line)


def _render_qa(record) -> str:
    return _render_via(
        record,
        ("serialize_qa_record", "render_qa_record", "qa_record_to_line", "format_qa_record"),
        ("to_line", "to_json_line", "to_jsonl", "serialize", "render"),
    )


def _parse_log(block: str):
    parse = _resolve_parse(
        ("parse_log_entry", "parse_log_entries"),
        (("LogEntry",), ("parse", "from_text", "from_block", "from_markdown", "from_lines")),
    )
    result = parse(block)
    if isinstance(result, list):
        assert len(result) <= 1, f"a single entry block parsed into multiple entries: {result!r}"
        return result[0] if result else None
    return result


def _render_log(entry) -> str:
    return _render_via(
        entry,
        ("render_log_entry", "format_log_entry", "log_entry_to_markdown"),
        ("render", "to_markdown", "to_text", "serialize"),
    )


def _parse_commit(subject: str):
    return _resolve_parse(
        ("parse_commit_message", "parse_commit_subject", "parse_commit"),
        (("CommitMessage",), ("parse", "from_subject", "from_line")),
    )(subject)


def _render_commit(record) -> str:
    mod = _records_module()
    for name in ("render_commit_message", "format_commit_message", "format_commit_subject"):
        fn = getattr(mod, name, None)
        if not callable(fn):
            continue
        try:
            return fn(record)
        except TypeError:
            # The formatter takes the three grammar tokens rather than a record.
            return fn(
                _field(record, "op", "operation"),
                _field(record, "topic"),
                _field(record, "title"),
            )
    return _render_via(record, (), ("render", "to_subject", "to_line", "serialize"))


def _parse_provenance(text: str):
    return _resolve_parse(
        (
            "parse_source_provenance",
            "parse_provenance",
            "parse_source_document",
            "parse_source_file",
        ),
        (
            ("SourceProvenance", "ProvenanceRecord", "Provenance", "SourceRecord"),
            ("from_text", "from_file_text", "from_markdown", "parse"),
        ),
    )(text)


def _render_provenance(parsed) -> str:
    fn_names = ("render_source_provenance", "render_provenance", "render_source_document")
    method_names = ("render", "to_markdown", "to_text", "serialize")
    if isinstance(parsed, tuple):
        mod = _records_module()
        for name in fn_names:
            fn = getattr(mod, name, None)
            if callable(fn):
                return fn(*parsed)
        return _render_via(parsed[0], fn_names, method_names)
    return _render_via(parsed, fn_names, method_names)


def _field(record, *names: str):
    """A field value off a parsed record, whatever its concrete shape."""
    candidates = record[0] if isinstance(record, tuple) else record
    for name in names:
        if isinstance(candidates, dict) and name in candidates:
            return candidates[name]
        if hasattr(candidates, name):
            return getattr(candidates, name)
    raise AttributeError(
        f"parsed record {type(candidates).__name__} carries none of {names}: {candidates!r}"
    )


def _assert_rejected(parse, text: str, *, mentioning: str | None = None) -> None:
    """A malformed input must not come back as a parsed record.

    Accepts either failure style: raising (preferred for record formats — the
    error should name the offender) or returning ``None`` (the classifier style
    the commit/log line parsers may share with ``git log`` scanning).
    """
    try:
        result = parse(text)
    except Exception as err:  # noqa: BLE001 — any loud failure is a rejection
        if mentioning is not None:
            assert mentioning in str(err), (
                f"the rejection error should name {mentioning!r} so the failure is "
                f"actionable, got: {err!r}"
            )
        return
    assert result is None, f"malformed input was accepted as a record: {text!r} -> {result!r}"


# ---------------------------------------------------------------------------
# Canonical instances (hand-authored from the frozen formats — the spec bytes)
# ---------------------------------------------------------------------------


def _qa_payload(**overrides) -> dict:
    payload = {
        "id": "qa-2026-07-03-0001",
        "schema_version": 1,
        "topic": "agentic-systems",
        "created": "2026-07-03T10:00:00Z",
        "query": "What does Agent Workflow Memory induce from past trajectories?",
        "pages_used": ["agentic-systems/agent-workflow-memory.md"],
        "answer": "Reusable workflows induced from past action trajectories.",
        "citations": [
            "agentic-systems/agent-workflow-memory",
            "sources/agentic-systems/wang2024awm",
        ],
        "verdict": "good",
        "corrected_answer": None,
        "source": "curate_example",
        "model": "claude-fable-5",
    }
    payload.update(overrides)
    return payload


def _qa_line(**overrides) -> str:
    return json.dumps(_qa_payload(**overrides))


def _qa_line_missing(field: str) -> str:
    payload = _qa_payload()
    del payload[field]
    return json.dumps(payload)


LOG_BLOCK = (
    "## [2026-07-03] write_page | agentic-systems | Ingest ReAct paper\n"
    "- agentic-systems/react.md\n"
    "- index.md"
)

LOG_HEADING_ONLY = "## [2026-07-03] store_source | agentic-systems | ReAct, arXiv 2210.03629"

COMMIT_SUBJECT = "knotica(write_page): agentic-systems — Ingest ReAct paper"

PROVENANCE_BODY = "# Attention Is All You Need\n\nTransformers dispense with recurrence entirely.\n"


def _provenance_fields(body: str = PROVENANCE_BODY, **overrides) -> dict:
    fields = {
        "schema_version": 1,
        "type": "source",
        "topic": "agentic-systems",
        "citation_key": "vaswani2017attention",
        "retrieved": "2026-07-03T10:00:00Z",
        "origin_url": "https://arxiv.org/html/1706.03762",
        "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "source_type": "html",
        "ingested_by": "knotica-tests",
    }
    fields.update(overrides)
    return fields


def _provenance_text(body: str = PROVENANCE_BODY, *, drop: str | None = None, **overrides) -> str:
    fields = _provenance_fields(body, **overrides)
    if drop is not None:
        del fields[drop]
    lines = ["---", *[f"{key}: {value}" for key, value in fields.items()], "---", ""]
    return "\n".join(lines) + "\n" + body


def _body_after_frontmatter(raw: bytes) -> bytes:
    """The digest's coverage per the constitution: bytes after the frontmatter
    block's trailing blank line, trailing newline included."""
    head, sep, body = raw.partition(b"\n---\n\n")
    assert sep, "no '---' + blank-line frontmatter terminator found"
    return body


def _real_log_blocks(log_text: str) -> list[str]:
    """Every real entry block (H2 + its bullets) in a ``log.md`` body, skipping
    the fenced format examples in the header."""
    blocks: list[str] = []
    in_fence = False
    for line in log_text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.startswith("## ["):
            blocks.append(line)
        elif blocks and line.startswith("- "):
            blocks[-1] += "\n" + line
    return blocks


# ---------------------------------------------------------------------------
# qa.jsonl record
# ---------------------------------------------------------------------------


def test_qa_line_round_trips_with_identical_fields():
    line = _qa_line()

    rendered = _render_qa(_parse_qa(line))

    assert json.loads(rendered) == json.loads(line)
    assert _render_qa(_parse_qa(rendered)) == rendered, (
        "serialization must be a fixed point: parse(render(x)) must render back identically"
    )


def test_qa_line_carries_exactly_the_frozen_field_set():
    rendered = _render_qa(_parse_qa(_qa_line()))

    assert set(json.loads(rendered)) == QA_FIELDS, (
        "a v1 qa.jsonl line must carry exactly the twelve frozen fields — a missing key is "
        "a broken record, an extra key is undeclared schema drift"
    )


def test_qa_answer_with_embedded_newlines_stays_a_single_jsonl_line():
    multiline = "First finding.\n\nSecond finding with detail.\n"
    line = _qa_line(answer=multiline)

    rendered = _render_qa(_parse_qa(line))

    assert "\n" not in rendered.strip("\n"), (
        "a qa record must serialize to exactly one JSONL line — embedded newlines belong "
        "escaped inside the JSON string, never raw in the file"
    )
    assert json.loads(rendered)["answer"] == multiline


@pytest.mark.parametrize(
    ("verdict", "corrected_answer"),
    [
        ("good", None),
        ("bad", None),
        ("corrected", "Workflows are induced from agent trajectories, not hand-written."),
    ],
)
def test_each_verdict_round_trips_with_its_corrected_answer(verdict, corrected_answer):
    line = _qa_line(verdict=verdict, corrected_answer=corrected_answer)

    rendered = json.loads(_render_qa(_parse_qa(line)))

    assert rendered["verdict"] == verdict
    assert rendered["corrected_answer"] == corrected_answer


@pytest.mark.parametrize(
    ("line", "offender"),
    [
        pytest.param(_qa_line_missing("verdict"), "verdict", id="missing-verdict"),
        pytest.param(_qa_line_missing("query"), "query", id="missing-query"),
        pytest.param(
            _qa_line_missing("schema_version"), "schema_version", id="missing-schema-version"
        ),
        pytest.param(_qa_line(verdict="excellent"), "verdict", id="verdict-outside-domain"),
        pytest.param("not a json object {", None, id="not-json"),
        pytest.param("", None, id="empty-line"),
    ],
)
def test_malformed_qa_lines_are_rejected(line, offender):
    _assert_rejected(_parse_qa, line, mentioning=offender)


def test_future_qa_schema_version_with_unknown_fields_still_parses():
    # Additive-only evolution: a v2 record with a field this code has never
    # seen must load without error — old readers tolerate newer records.
    line = _qa_line(schema_version=2)
    payload = json.loads(line)
    payload["novel_field"] = "added by a future schema version"
    future_line = json.dumps(payload)

    record = _parse_qa(future_line)

    assert record is not None
    assert json.loads(_render_qa(record))["query"] == _qa_payload()["query"]


def test_appended_records_read_back_in_order_with_prior_bytes_untouched(tmp_path: Path):
    qa_file = tmp_path / "qa.jsonl"
    first = _render_qa(_parse_qa(_qa_line(id="qa-0001", query="first question?"))).strip("\n")
    second = _render_qa(_parse_qa(_qa_line(id="qa-0002", query="second question?"))).strip("\n")

    qa_file.write_text(first + "\n", encoding="utf-8")
    prior_bytes = qa_file.read_bytes()
    with qa_file.open("a", encoding="utf-8") as handle:
        handle.write(second + "\n")

    content = qa_file.read_bytes()
    assert content.startswith(prior_bytes), (
        "appending a record must leave every previously written byte untouched"
    )
    replayed = [json.loads(line)["id"] for line in content.decode("utf-8").splitlines()]
    assert replayed == ["qa-0001", "qa-0002"], "append order must be read-back order"


# ---------------------------------------------------------------------------
# Log entry
# ---------------------------------------------------------------------------


def test_log_entry_with_bullets_round_trips_byte_identically():
    assert _render_log(_parse_log(LOG_BLOCK)).strip() == LOG_BLOCK


def test_log_heading_without_bullets_round_trips():
    assert _render_log(_parse_log(LOG_HEADING_ONLY)).strip() == LOG_HEADING_ONLY


def test_rendered_log_heading_satisfies_the_independent_grammar():
    heading = _render_log(_parse_log(LOG_BLOCK)).strip().splitlines()[0]

    match = LOG_ENTRY_RE.match(heading)
    assert match, f"rendered heading does not satisfy the frozen H2 grammar: {heading!r}"
    assert match.groupdict() == {
        "date": "2026-07-03",
        "op": "write_page",
        "topic": "agentic-systems",
        "title": "Ingest ReAct paper",
    }


def test_template_log_corpus_re_renders_byte_identically(template_vault: Path):
    blocks = _real_log_blocks((template_vault / "log.md").read_text(encoding="utf-8"))
    assert len(blocks) >= 4, "the template demo ingest ships at least four real log entries"

    mismatches = [
        (block, _render_log(_parse_log(block)).strip())
        for block in blocks
        if _render_log(_parse_log(block)).strip() != block
    ]
    assert mismatches == [], f"real template log entries fail the round trip: {mismatches}"


@pytest.mark.parametrize(
    "heading",
    [
        pytest.param("## 2026-07-03 write_page | agentic-systems | title", id="no-brackets"),
        pytest.param("## [2026-7-3] write_page | agentic-systems | title", id="bad-date"),
        pytest.param("## [2026-07-03] write_page — agentic-systems — title", id="wrong-separator"),
        pytest.param("### [2026-07-03] write_page | agentic-systems | title", id="h3-not-h2"),
        pytest.param("## [2026-07-03] write_page | agentic-systems", id="missing-title"),
        pytest.param("", id="empty"),
    ],
)
def test_malformed_log_headings_are_rejected(heading):
    _assert_rejected(_parse_log, heading)


# ---------------------------------------------------------------------------
# Commit message
# ---------------------------------------------------------------------------


def test_commit_subject_round_trips_through_the_frozen_grammar():
    rendered = _render_commit(_parse_commit(COMMIT_SUBJECT)).strip()

    assert rendered == COMMIT_SUBJECT
    match = KNOTICA_COMMIT_RE.match(rendered)
    assert match, f"rendered subject does not satisfy the frozen grammar: {rendered!r}"
    assert match.groupdict() == {
        "op": "write_page",
        "topic": "agentic-systems",
        "title": "Ingest ReAct paper",
    }


def test_title_containing_the_separator_splits_on_the_first_occurrence():
    subject = "knotica(write_page): agentic-systems — Attention — a retrospective"

    record = _parse_commit(subject)

    assert _field(record, "topic") == "agentic-systems", (
        "the first ' — ' is the topic/title separator; later em-dashes belong to the title"
    )
    assert _field(record, "title") == "Attention — a retrospective"
    assert _render_commit(record).strip() == subject


@pytest.mark.parametrize(("subject", "expected_op"), LIVE_COMMIT_CORPUS)
def test_live_vault_commit_corpus_parses_with_the_expected_operation(subject, expected_op):
    record = _parse_commit(subject)

    assert _field(record, "op", "operation") == expected_op
    assert _field(record, "topic") == "agentic-systems"
    assert _render_commit(record).strip() == subject


@pytest.mark.parametrize(
    "subject",
    [
        pytest.param("vault: instantiate template", id="non-knotica-baseline-commit"),
        pytest.param("knotica(write_page): agentic-systems - title", id="hyphen-separator"),
        pytest.param("knotica(write_page): agentic-systems—title", id="em-dash-without-spaces"),
        pytest.param("knotica write_page: agentic-systems — title", id="missing-parentheses"),
        pytest.param("Knotica(write_page): agentic-systems — title", id="uppercase-prefix"),
    ],
)
def test_non_knotica_and_malformed_subjects_are_rejected(subject):
    _assert_rejected(_parse_commit, subject)


# ---------------------------------------------------------------------------
# Source provenance
# ---------------------------------------------------------------------------


def test_provenance_file_round_trips_fields_and_body_byte_exactly():
    text = _provenance_text()

    rendered = _render_provenance(_parse_provenance(text))

    fields, _ = parse_frontmatter(rendered)
    expected = _provenance_fields()
    assert {key: str(value) for key, value in fields.items()} == {
        key: str(value) for key, value in expected.items()
    }
    assert _body_after_frontmatter(rendered.encode("utf-8")) == PROVENANCE_BODY.encode("utf-8"), (
        "the body must survive the round trip byte-exactly (trailing newline included) — "
        "any mutation breaks the sha256 seal"
    )


def test_rendered_provenance_frontmatter_carries_exactly_the_frozen_field_set():
    rendered = _render_provenance(_parse_provenance(_provenance_text()))

    fields, _ = parse_frontmatter(rendered)
    assert set(fields) == PROVENANCE_FIELDS


def test_template_source_digest_covers_exactly_the_post_frontmatter_bytes(template_vault: Path):
    raw = (template_vault / DEMO_SOURCE).read_bytes()
    declared = str(_field(_parse_provenance(raw.decode("utf-8")), "sha256"))
    body = _body_after_frontmatter(raw)

    assert hashlib.sha256(body).hexdigest() == declared, (
        "the declared sha256 must be the digest of exactly the post-frontmatter bytes"
    )
    assert hashlib.sha256(raw).hexdigest() != declared, (
        "the digest must exclude the frontmatter (the frontmatter cannot hash itself)"
    )
    assert hashlib.sha256(body.rstrip(b"\n")).hexdigest() != declared, (
        "the digest includes the body's trailing newline — the stripped variant must differ"
    )


def test_constructed_provenance_preserves_a_valid_body_digest_through_round_trip():
    text = _provenance_text()

    rendered = _render_provenance(_parse_provenance(text)).encode("utf-8")

    declared = str(parse_frontmatter(rendered.decode("utf-8"))[0]["sha256"])
    assert hashlib.sha256(_body_after_frontmatter(rendered)).hexdigest() == declared, (
        "after a round trip the declared digest must still verify against the rendered body"
    )


@pytest.mark.parametrize(
    ("text", "offender"),
    [
        pytest.param(_provenance_text(drop="sha256"), "sha256", id="missing-sha256"),
        pytest.param(
            _provenance_text(drop="schema_version"), "schema_version", id="missing-schema-version"
        ),
        pytest.param(
            _provenance_text(drop="citation_key"), "citation_key", id="missing-citation-key"
        ),
        pytest.param(
            "# Just a markdown page\n\nNo frontmatter at all.\n", None, id="no-frontmatter"
        ),
    ],
)
def test_provenance_missing_required_fields_is_rejected(text, offender):
    _assert_rejected(_parse_provenance, text, mentioning=offender)


def test_future_provenance_schema_version_with_extra_fields_still_parses():
    text = _provenance_text(schema_version=2, license="cc-by-4.0")

    parsed = _parse_provenance(text)

    assert parsed is not None
    assert str(_field(parsed, "citation_key")) == "vaswani2017attention"
