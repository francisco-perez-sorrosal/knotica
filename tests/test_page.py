"""Behavioral contract of the page model: frontmatter, paths, core validation.

Derived from the vault constitution (``vault-template/SCHEMA.md``) and the
interface design, not from the implementation:

- every real template page's frontmatter survives a parse/serialize round
  trip, and the pages authored in the constitution's own style re-serialize
  byte-identically;
- anything outside the strict YAML subset is rejected -- but on the read path
  the failure travels as *data* on the page, never as a crashed read;
- core-field validation reports each missing/invalid field as data, while
  unknown extra fields are never a violation (overlays extend);
- page references normalize to ``.md`` paths, are topic-relative, and every
  traversal/hidden-path shape is rejected;
- ``read_page`` addresses topics explicitly and verbatim (stateless server
  groundwork) and a missing page carries nearest-match suggestions.
"""

import pytest
from knotica.core.page import (
    REQUIRED_FIELDS,
    FrontmatterParseError,
    PageNotFoundError,
    TopicNotFoundError,
    normalize_page_name,
    page_path,
    parse_frontmatter_block,
    parse_page,
    read_page,
    serialize_frontmatter,
    validate_frontmatter,
)
from knotica.store import LocalFSStore

#: Every template page that carries a frontmatter block.
FRONTMATTER_PAGES = (
    "SCHEMA.md",
    "agentic-systems/SCHEMA.md",
    "agentic-systems/agent-workflow-memory.md",
    "agentic-systems/workflow-induction.md",
    "agentic-systems/agent-memory.md",
    "sources/agentic-systems/wang2024awm.md",
)

#: Template pages authored without frontmatter (reserved structural pages).
BODY_ONLY_PAGES = ("index.md", "log.md")

#: Frontmatter pages whose authored style matches the serializer's output
#: exactly (plain scalars + flow lists). The source page is excluded: its
#: ISO timestamps and URL re-serialize quoted, which is a semantic -- not
#: byte-level -- round trip.
STYLE_STABLE_PAGES = (
    "SCHEMA.md",
    "agentic-systems/SCHEMA.md",
    "agentic-systems/agent-workflow-memory.md",
    "agentic-systems/workflow-induction.md",
    "agentic-systems/agent-memory.md",
)

#: The agentic-systems overlay's entity types (topic SCHEMA.md).
OVERLAY_ENTITY_TYPES = frozenset(
    {"paper", "method", "system", "benchmark", "concept", "person-or-lab"}
)


def _valid_frontmatter() -> dict[str, object]:
    """A minimal core-conformant frontmatter mapping (constitution field set)."""
    return {
        "type": "paper",
        "topic": "agentic-systems",
        "created": "2026-07-03",
        "updated": "2026-07-03",
        "confidence": "high",
        "sources": ["wang2024awm"],
        "status": "active",
        "tags": ["demo-sample"],
    }


# ---------------------------------------------------------------------------
# Frontmatter round trip over the real template inventory
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("page", FRONTMATTER_PAGES)
def test_every_template_frontmatter_survives_a_parse_serialize_round_trip(template_vault, page):
    raw = (template_vault / page).read_text(encoding="utf-8")
    frontmatter, error, body = parse_page(raw)
    assert error is None, f"{page}: template frontmatter failed to parse: {error}"
    assert frontmatter, f"{page}: expected a frontmatter block"
    reparsed, reparse_error, rebody = parse_page(serialize_frontmatter(frontmatter) + body)
    assert reparse_error is None
    assert reparsed == frontmatter
    assert rebody == body


@pytest.mark.parametrize("page", STYLE_STABLE_PAGES)
def test_constitution_style_frontmatter_reserializes_byte_identically(template_vault, page):
    raw = (template_vault / page).read_text(encoding="utf-8")
    frontmatter, _, _ = parse_page(raw)
    assert frontmatter is not None
    assert raw.startswith(serialize_frontmatter(frontmatter)), (
        f"{page}: re-serialized block diverges from the authored template style"
    )


@pytest.mark.parametrize("page", BODY_ONLY_PAGES)
def test_pages_without_frontmatter_parse_as_body_only(template_vault, page):
    raw = (template_vault / page).read_text(encoding="utf-8")
    frontmatter, error, body = parse_page(raw)
    assert frontmatter is None
    assert error is None, "absence of frontmatter is not an error"
    assert body == raw


def test_scalar_and_sequence_values_parse_typed_and_round_trip():
    block = (
        "count: -3\n"
        "flag: true\n"
        "off: false\n"
        "nothing: null\n"
        'name: "quoted: value"\n'
        "items: [a, 'b c']\n"
        "empty: []\n"
        "properties:\n"
        "  - one\n"
        "  - 2\n"
    )
    fields = parse_frontmatter_block(block)
    assert fields == {
        "count": -3,
        "flag": True,
        "off": False,
        "nothing": None,
        "name": "quoted: value",
        "items": ["a", "b c"],
        "empty": [],
        "properties": ["one", 2],
    }
    reparsed, error, _ = parse_page(serialize_frontmatter(fields) + "body\n")
    assert error is None
    assert reparsed == fields


def test_unclosed_opening_fence_is_body_not_frontmatter():
    text = "---\ntype: paper\nno closing fence follows\n"
    frontmatter, error, body = parse_page(text)
    assert frontmatter is None
    assert error is None
    assert body == text


# ---------------------------------------------------------------------------
# Strict subset: rejection as exception at the block level, as data on read
# ---------------------------------------------------------------------------

MALFORMED_BLOCKS = (
    pytest.param("key: [a, [b]]", id="nested-collection"),
    pytest.param("key: {a: 1}", id="flow-mapping"),
    pytest.param("dup: 1\ndup: 2", id="duplicate-key"),
    pytest.param("- item", id="list-item-without-key"),
    pytest.param("key: scalar\n- item", id="list-item-after-scalar"),
    pytest.param("key: [a, b", id="unterminated-flow-sequence"),
    pytest.param('key: "unbalanced', id="unbalanced-quote"),
    pytest.param("summary: |\n  a folded paragraph", id="multi-line-scalar"),
    pytest.param("just some prose", id="not-a-key-value-entry"),
)


@pytest.mark.parametrize("block", MALFORMED_BLOCKS)
def test_constructs_outside_the_strict_subset_are_rejected(block):
    with pytest.raises(FrontmatterParseError):
        parse_frontmatter_block(block)


@pytest.mark.parametrize("block", MALFORMED_BLOCKS)
def test_malformed_frontmatter_travels_as_data_never_a_crashed_read(block):
    frontmatter, error, body = parse_page(f"---\n{block}\n---\nBody remains readable.\n")
    assert frontmatter is None
    assert error is not None
    assert body == "Body remains readable.\n"


# ---------------------------------------------------------------------------
# Core-field validation: findings as data
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "page",
    (
        "agent-workflow-memory",
        "workflow-induction",
        "agent-memory",
    ),
)
def test_demo_page_frontmatter_conforms_to_the_core_field_set(template_vault, page):
    store = LocalFSStore(template_vault)
    parsed = read_page(store, "agentic-systems", page)
    assert parsed.frontmatter is not None
    assert validate_frontmatter(parsed.frontmatter) == []
    assert validate_frontmatter(parsed.frontmatter, allowed_types=OVERLAY_ENTITY_TYPES) == []


def test_every_missing_required_field_is_reported():
    problems = validate_frontmatter({})
    assert {problem.field for problem in problems} == set(REQUIRED_FIELDS)
    assert all(problem.problem == "missing required field" for problem in problems)


def test_unknown_extra_fields_are_never_a_violation():
    frontmatter = _valid_frontmatter() | {"overlay_field": "anything", "review_round": 2}
    assert validate_frontmatter(frontmatter) == []


@pytest.mark.parametrize(
    ("field", "bad_value", "expected_fragment"),
    (
        pytest.param("created", "03/07/2026", "YYYY-MM-DD", id="created-wrong-format"),
        pytest.param("updated", 20260703, "YYYY-MM-DD", id="updated-not-a-string"),
        pytest.param("confidence", "certain", "high|low|medium", id="confidence-outside-enum"),
        pytest.param("status", "archived", "active|stale", id="status-outside-enum"),
        pytest.param("sources", "wang2024awm", "list of strings", id="sources-bare-scalar"),
        pytest.param("tags", ["ok", 3], "list of strings", id="tags-non-string-item"),
        pytest.param("topic", "", "non-empty string", id="topic-empty"),
        pytest.param("type", "", "non-empty string", id="type-empty"),
    ),
)
def test_invalid_field_values_are_reported_naming_the_field(field, bad_value, expected_fragment):
    frontmatter = _valid_frontmatter() | {field: bad_value}
    problems = validate_frontmatter(frontmatter)
    assert [problem.field for problem in problems] == [field]
    assert expected_fragment in problems[0].problem


def test_allowed_types_constrains_the_type_field_to_the_overlay():
    frontmatter = _valid_frontmatter() | {"type": "poem"}
    problems = validate_frontmatter(frontmatter, allowed_types={"paper", "method"})
    assert [problem.field for problem in problems] == ["type"]
    assert "method|paper" in problems[0].problem


def test_optional_fields_may_be_null_but_not_empty():
    assert validate_frontmatter(_valid_frontmatter() | {"supersedes": None}) == []
    problems = validate_frontmatter(_valid_frontmatter() | {"supersedes": ""})
    assert [problem.field for problem in problems] == ["supersedes"]


# ---------------------------------------------------------------------------
# Path model: normalization, topic scoping, traversal rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reference", "expected"),
    (
        pytest.param("react", "react.md", id="extension-added"),
        pytest.param("react.md", "react.md", id="extension-kept"),
        pytest.param("methods/react", "methods/react.md", id="nested-path"),
        pytest.param("  react  ", "react.md", id="whitespace-stripped"),
    ),
)
def test_page_references_normalize_to_md_paths(reference, expected):
    assert normalize_page_name(reference) == expected


@pytest.mark.parametrize(
    "reference",
    (
        pytest.param("", id="empty"),
        pytest.param("   ", id="whitespace-only"),
        pytest.param("/etc/passwd", id="absolute"),
        pytest.param("../escape", id="parent-traversal"),
        pytest.param("a/../b", id="embedded-traversal"),
        pytest.param("a//b", id="empty-segment"),
        pytest.param(".hidden", id="hidden-file"),
        pytest.param("a/.hidden/b", id="hidden-segment"),
        pytest.param("a\\b", id="backslash"),
    ),
)
def test_traversal_and_hidden_page_references_are_rejected(reference):
    with pytest.raises(ValueError):
        normalize_page_name(reference)


def test_page_path_joins_topic_and_normalized_page():
    assert page_path("agentic-systems", "react") == "agentic-systems/react.md"
    assert page_path(" agentic-systems ", "react.md") == "agentic-systems/react.md"


@pytest.mark.parametrize(
    "topic",
    (
        pytest.param("", id="empty"),
        pytest.param("   ", id="whitespace-only"),
        pytest.param("a/b", id="nested"),
        pytest.param(".knotica", id="dot-folder"),
    ),
)
def test_topic_must_be_a_bare_top_level_directory_name(topic):
    with pytest.raises(ValueError):
        page_path(topic, "react")


# ---------------------------------------------------------------------------
# read_page: explicit topic addressing, raw+body, not-found envelopes
# ---------------------------------------------------------------------------


def test_read_page_resolves_vault_relative_source_path(template_vault):
    store = LocalFSStore(template_vault)
    page = read_page(
        store,
        "agentic-systems",
        "sources/agentic-systems/wang2024awm",
    )
    assert page.path == "sources/agentic-systems/wang2024awm.md"
    assert page.topic == "agentic-systems"
    assert page.frontmatter is not None
    assert page.frontmatter["type"] == "source"


def test_read_page_rewrites_wrong_source_topic_segment(template_vault):
    store = LocalFSStore(template_vault)
    page = read_page(
        store,
        "agentic-systems",
        "sources/agentic-system/wang2024awm",
    )
    assert page.path == "sources/agentic-systems/wang2024awm.md"


def test_read_page_reads_bare_citation_key_from_sources(template_vault):
    store = LocalFSStore(template_vault)
    page = read_page(store, "agentic-systems", "wang2024awm")
    assert page.path == "sources/agentic-systems/wang2024awm.md"


def test_read_page_returns_raw_and_body_with_parsed_frontmatter(template_vault):
    store = LocalFSStore(template_vault)
    page = read_page(store, "agentic-systems", "agent-workflow-memory")
    assert page.topic == "agentic-systems"
    assert page.path == "agentic-systems/agent-workflow-memory.md"
    assert page.frontmatter is not None
    assert page.frontmatter["type"] == "paper"
    assert page.frontmatter_error is None
    assert page.raw == serialize_frontmatter(page.frontmatter) + page.body


def test_read_page_distinguishes_absent_from_malformed_frontmatter(template_vault):
    store = LocalFSStore(template_vault)
    (template_vault / "agentic-systems" / "plain.md").write_text(
        "Just a body, no frontmatter.\n", encoding="utf-8"
    )
    (template_vault / "agentic-systems" / "broken.md").write_text(
        "---\nkey: [a, [b]]\n---\nStill readable body.\n", encoding="utf-8"
    )
    plain = read_page(store, "agentic-systems", "plain")
    assert plain.frontmatter is None
    assert plain.frontmatter_error is None
    broken = read_page(store, "agentic-systems", "broken")
    assert broken.frontmatter is None
    assert broken.frontmatter_error is not None, "malformed block must surface as data"
    assert broken.body == "Still readable body.\n"


def test_topic_argument_is_addressed_explicitly_and_verbatim(template_vault):
    store = LocalFSStore(template_vault)
    (template_vault / "other-topic").mkdir()
    (template_vault / "other-topic" / "agent-memory.md").write_text(
        "The other topic's page of the same name.\n", encoding="utf-8"
    )
    seed_page = read_page(store, "agentic-systems", "agent-memory")
    other_page = read_page(store, "other-topic", "agent-memory")
    assert seed_page.path == "agentic-systems/agent-memory.md"
    assert other_page.path == "other-topic/agent-memory.md"
    assert other_page.raw == "The other topic's page of the same name.\n"
    assert seed_page.raw != other_page.raw


def test_missing_topic_raises_topic_not_found(template_vault):
    store = LocalFSStore(template_vault)
    with pytest.raises(TopicNotFoundError) as excinfo:
        read_page(store, "no-such-topic", "anything")
    assert excinfo.value.topic == "no-such-topic"


def test_missing_page_carries_nearest_match_suggestions(template_vault):
    store = LocalFSStore(template_vault)
    with pytest.raises(PageNotFoundError) as excinfo:
        read_page(store, "agentic-systems", "agent-memroy")
    assert excinfo.value.page == "agent-memroy"
    assert "agent-memory" in excinfo.value.suggestions
    assert "Nearest matches" in str(excinfo.value)


def test_missing_page_with_no_similar_names_suggests_nothing(template_vault):
    store = LocalFSStore(template_vault)
    with pytest.raises(PageNotFoundError) as excinfo:
        read_page(store, "agentic-systems", "zzzz-quantum-chromodynamics")
    assert excinfo.value.suggestions == ()
    assert "Nearest matches" not in str(excinfo.value)
