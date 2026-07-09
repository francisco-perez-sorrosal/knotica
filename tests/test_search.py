"""Behavioral contract of vault search pagination.

Derived from the search tool contract, not from the implementation:

- results are POINTERS (topic, path, snippet, score, kind) inside the stable
  envelope ``{results, next_cursor, has_more, total_count}`` -- never bodies;
- the cursor is an opaque, self-contained token: a full page walk covers the
  result set exactly once (no duplicates, no gaps), ``has_more`` flips exactly
  on the last page, and ``total_count`` is stable across pages;
- ordering is deterministic -- score descending, ties broken by path
  ascending -- so two identical walks return identical pages;
- anything malformed or stale (garbage base64, truncated JSON, wrong shape,
  negative offset, a token minted for a different query or sort contract)
  raises ``InvalidCursorError`` -- never a crash, never a silently wrong page;
- page size defaults to 10 and is clamped into 1..50, never an error;
- ``topic=""`` searches all topics; a named topic scopes to that topic's
  pages AND its stored sources; dot-folders (``.knotica/``, ``.git/``, ...),
  dot-files, and non-markdown files are never searched;
- the engine is hidden behind the protocol: ripgrep and the pure-Python
  fallback must produce identical envelopes on the same corpus, so every
  scan-behavior test runs against both engines (the ripgrep half is
  skip-marked when ``rg`` is not on PATH).

The shipped vault template is the golden fixture for classification: the
query "memory" hits six files with hand-counted, pairwise-distinct scores,
spanning a stored source, topic pages, and vault-root pages.
"""

import base64
import shutil
from collections.abc import Callable
from pathlib import Path, PurePosixPath

import pytest
from knotica.search import (
    DEFAULT_PAGE_SIZE,
    SORT_SCORE_DESC_PATH_ASC,
    Cursor,
    InvalidCursorError,
    RipgrepBackend,
    SearchBackend,
    SearchPage,
    SearchResult,
    clamp_limit,
    decode_cursor,
    encode_cursor,
    paginate,
    resolve_offset,
)

RG_AVAILABLE = shutil.which("rg") is not None

#: A token that appears nowhere in the vault template -- planted corpora own it.
SEARCH_TOKEN = "zyzzyva"

#: Full-vault ranking of the planted corpus for SEARCH_TOKEN: score descending,
#: ties broken by path ascending. The occurrence counts double as the planted
#: ground truth the fixture writes. Deliberate features: the stored source
#: outranks lexically-earlier paths (score dominates path), the six-file tie
#: group pins the path-ascending tie-break, and ``othertopic/`` gives topic
#: scoping something to exclude.
PLANTED_RANKING = (
    ("paging/apex.md", 9),
    ("sources/paging/zdoc.md", 7),
    ("paging/upper.md", 5),
    ("paging/tie-a.md", 3),
    ("paging/tie-b.md", 3),
    ("paging/tie-c.md", 3),
    ("paging/tie-d.md", 3),
    ("paging/tie-e.md", 3),
    ("paging/tie-f.md", 3),
    ("othertopic/other.md", 2),
    ("paging/low-a.md", 2),
    ("paging/low-b.md", 2),
    ("paging/floor-a.md", 1),
    ("paging/floor-b.md", 1),
)

#: What ``topic="paging"`` must return: the topic's pages plus its stored
#: sources, and nothing from any other topic.
PAGING_SCOPED_RANKING = tuple(
    row for row in PLANTED_RANKING if not row[0].startswith("othertopic/")
)

#: Files planted with the SAME token that must never be returned: dot-folders
#: (including a fake ``.git``), a nested dot-folder, a dot-file, and a
#: non-markdown file.
PLANTED_EXCLUDED = (
    ".knotica/decoy.md",
    ".git/decoy.md",
    "paging/.knotica/decoy.md",
    "paging/.decoy.md",
    "paging/decoy.txt",
)

#: Hand-derived golden for the shipped template, query "memory" (occurrences
#: counted case-insensitively, non-overlapping, independently of the engine).
#: Covers all three path classes: stored source, topic page, vault-root page
#: (root pages carry ``topic=""``). The template's ``.knotica/prompts/`` also
#: contains "memory" -- its absence below is the dot-folder exclusion working.
GOLDEN_MEMORY_RANKING = (
    ("sources/agentic-systems/wang2024awm.md", "agentic-systems", "source", 35),
    ("agentic-systems/agent-memory.md", "agentic-systems", "page", 14),
    ("agentic-systems/agent-workflow-memory.md", "agentic-systems", "page", 10),
    ("agentic-systems/workflow-induction.md", "agentic-systems", "page", 6),
    ("log.md", "", "page", 5),
    ("index.md", "", "page", 4),
    ("SCHEMA.md", "", "page", 2),
)

GOLDEN_MEMORY_TOPIC_RANKING = (
    ("sources/agentic-systems/wang2024awm.md", "agentic-systems", "source", 35),
    ("agentic-systems/agent-memory.md", "agentic-systems", "page", 14),
    ("agentic-systems/agent-workflow-memory.md", "agentic-systems", "page", 10),
    ("agentic-systems/workflow-induction.md", "agentic-systems", "page", 6),
)


# ---------------------------------------------------------------------------
# Helpers and fixtures
# ---------------------------------------------------------------------------


def _plant_text(vault: Path, rel_path: str, text: str) -> None:
    """Write one corpus file, creating parents."""
    file_path = vault / rel_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(text, encoding="utf-8")


def _plant(vault: Path, rel_path: str, occurrences: int) -> None:
    """Write a file containing SEARCH_TOKEN exactly ``occurrences`` times (one per line)."""
    lines = [f"filler line {i} with {SEARCH_TOKEN} inside" for i in range(occurrences)]
    _plant_text(vault, rel_path, "\n".join(lines) + "\n")


def _b64(payload: str) -> str:
    """Encode raw text as a URL-safe base64 token (for crafting bad cursors)."""
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def _walk(backend: SearchBackend, query: str, *, topic: str = "", limit: int) -> list[SearchPage]:
    """Follow ``next_cursor`` from the first page to the last page.

    Hard-capped so a broken ``has_more`` fails loudly instead of hanging.
    """
    pages: list[SearchPage] = []
    cursor = ""
    for _ in range(100):
        page = backend.search(query, topic=topic, cursor=cursor, limit=limit)
        pages.append(page)
        if not page.has_more:
            return pages
        cursor = page.next_cursor
    raise AssertionError("pagination walk did not terminate within 100 pages")


def _walked_paths(pages: list[SearchPage]) -> list[str]:
    return [result.path for page in pages for result in page.results]


@pytest.fixture(params=["ripgrep", "python-fallback"])
def engine(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """Which scan engine the backend under test runs.

    The protocol hides the engine, so every scan-behavior test asserts against
    both. The fallback is forced by making ``rg`` undiscoverable before the
    backend is constructed; the ripgrep half skips when ``rg`` is not on PATH.
    """
    if request.param == "ripgrep":
        if not RG_AVAILABLE:
            pytest.skip("ripgrep is not on PATH; install rg to exercise the ripgrep engine")
    else:
        monkeypatch.setattr("knotica.search.ripgrep.shutil.which", lambda cmd: None)
    return request.param


@pytest.fixture
def make_backend(engine: str) -> Callable[[Path], RipgrepBackend]:
    """Backend factory pinned to the parametrized engine.

    The private ``_rg_path`` probe is a deliberate fixture-only peek: without
    it, the two-engine parametrization could silently collapse onto one engine
    and the whole dual-engine suite (parity included) would pass vacuously.
    """

    def _make(root: Path) -> RipgrepBackend:
        backend = RipgrepBackend(root)
        selected = "python-fallback" if backend._rg_path is None else "ripgrep"
        assert selected == engine, f"engine parametrization is vacuous: wanted {engine}"
        return backend

    return _make


@pytest.fixture
def planted_vault(tmp_path: Path) -> Path:
    """A synthetic vault realizing PLANTED_RANKING plus the excluded decoys."""
    vault = tmp_path / "planted-vault"
    for rel_path, occurrences in PLANTED_RANKING:
        _plant(vault, rel_path, occurrences)
    for rel_path in PLANTED_EXCLUDED:
        _plant(vault, rel_path, 1)
    return vault


@pytest.fixture
def dense_vault(tmp_path: Path) -> Path:
    """55 single-occurrence pages -- enough to prove the 50-results-per-page cap."""
    vault = tmp_path / "dense-vault"
    for i in range(55):
        _plant(vault, f"dense/page-{i:02d}.md", 1)
    return vault


# ---------------------------------------------------------------------------
# Envelope and pointer shape
# ---------------------------------------------------------------------------


def test_envelope_and_pointer_render_shapes_match_the_tool_contract(planted_vault: Path):
    page = RipgrepBackend(planted_vault).search(SEARCH_TOKEN, limit=3)

    rendered = page.render()
    assert set(rendered) == {"results", "next_cursor", "has_more", "total_count"}
    assert len(rendered["results"]) == 3
    pointer_keys = [set(result) for result in rendered["results"]]
    assert pointer_keys == [{"topic", "path", "snippet", "score", "kind"}] * 3
    assert [type(result["score"]) for result in rendered["results"]] == [int] * 3


def test_default_page_size_is_ten(planted_vault: Path):
    page = RipgrepBackend(planted_vault).search(SEARCH_TOKEN)

    assert len(page.results) == DEFAULT_PAGE_SIZE == 10
    assert page.has_more is True
    assert page.total_count == len(PLANTED_RANKING)


# ---------------------------------------------------------------------------
# Cursor walk: completeness, flags, determinism, ranking
# ---------------------------------------------------------------------------


def test_cursor_walk_covers_the_whole_corpus_without_duplicates_or_gaps(
    make_backend: Callable[[Path], RipgrepBackend], planted_vault: Path
):
    backend = make_backend(planted_vault)

    pages = _walk(backend, SEARCH_TOKEN, limit=3)

    walked = [(result.path, result.score) for page in pages for result in page.results]
    assert walked == list(PLANTED_RANKING)
    assert len({path for path, _ in walked}) == len(PLANTED_RANKING)


def test_walk_envelope_flags_flip_exactly_at_the_end(
    make_backend: Callable[[Path], RipgrepBackend], planted_vault: Path
):
    backend = make_backend(planted_vault)

    pages = _walk(backend, SEARCH_TOKEN, limit=3)  # 14 results -> 3+3+3+3+2

    assert [len(page.results) for page in pages] == [3, 3, 3, 3, 2]
    assert [page.has_more for page in pages] == [True, True, True, True, False]
    assert {page.total_count for page in pages} == {len(PLANTED_RANKING)}
    assert all(page.next_cursor for page in pages[:-1]), "intermediate pages must mint a cursor"
    assert pages[-1].next_cursor == ""


def test_two_identical_walks_return_identical_pages(
    make_backend: Callable[[Path], RipgrepBackend], planted_vault: Path
):
    backend = make_backend(planted_vault)

    first = [page.render() for page in _walk(backend, SEARCH_TOKEN, limit=4)]
    second = [page.render() for page in _walk(backend, SEARCH_TOKEN, limit=4)]

    assert first == second


def test_ranking_is_score_descending_with_path_ascending_ties(
    make_backend: Callable[[Path], RipgrepBackend], planted_vault: Path
):
    page = make_backend(planted_vault).search(SEARCH_TOKEN, limit=50)

    assert [(result.path, result.score) for result in page.results] == list(PLANTED_RANKING)


# ---------------------------------------------------------------------------
# Match semantics: scoring, case, multi-term OR, snippets
# ---------------------------------------------------------------------------


def test_score_counts_every_occurrence_not_matching_lines(
    make_backend: Callable[[Path], RipgrepBackend], tmp_path: Path
):
    vault = tmp_path / "vault"
    _plant_text(
        vault,
        "topicx/page.md",
        f"{SEARCH_TOKEN} and {SEARCH_TOKEN} twice on one line\nplus {SEARCH_TOKEN} once\nquiet\n",
    )

    page = make_backend(vault).search(SEARCH_TOKEN)

    assert [(result.path, result.score) for result in page.results] == [("topicx/page.md", 3)]


def test_matching_is_case_insensitive_in_both_directions(
    make_backend: Callable[[Path], RipgrepBackend], tmp_path: Path
):
    vault = tmp_path / "vault"
    _plant_text(vault, "topicx/case.md", "ZyZZyVa shouts\nzyzzyva whispers\n")
    backend = make_backend(vault)

    lower = backend.search("zyzzyva")
    upper = backend.search("ZYZZYVA")

    assert [(result.path, result.score) for result in lower.results] == [("topicx/case.md", 2)]
    assert [(result.path, result.score) for result in upper.results] == [("topicx/case.md", 2)]


def test_multi_term_query_ors_terms_and_sums_their_occurrences(
    make_backend: Callable[[Path], RipgrepBackend], tmp_path: Path
):
    vault = tmp_path / "vault"
    _plant_text(vault, "topicx/both.md", "alphaterm and betaterm\nbetaterm again\n")
    _plant_text(vault, "topicx/one.md", "alphaterm only\n")
    _plant_text(vault, "topicx/neither.md", "gammaterm\n")

    page = make_backend(vault).search("alphaterm betaterm")

    assert [(result.path, result.score) for result in page.results] == [
        ("topicx/both.md", 3),
        ("topicx/one.md", 1),
    ]


def test_snippet_is_the_first_matching_line_stripped(
    make_backend: Callable[[Path], RipgrepBackend], tmp_path: Path
):
    vault = tmp_path / "vault"
    _plant_text(
        vault,
        "topicx/snip.md",
        f"no match on the first line\n  the {SEARCH_TOKEN} line, padded  \n{SEARCH_TOKEN} later\n",
    )

    page = make_backend(vault).search(SEARCH_TOKEN)

    assert page.results[0].snippet == f"the {SEARCH_TOKEN} line, padded"


def test_snippet_stays_short_even_for_a_very_long_matching_line(
    make_backend: Callable[[Path], RipgrepBackend], tmp_path: Path
):
    vault = tmp_path / "vault"
    long_line = ("x" * 240) + f" {SEARCH_TOKEN}"
    _plant_text(vault, "topicx/long.md", long_line + "\n")

    page = make_backend(vault).search(SEARCH_TOKEN)

    snippet = page.results[0].snippet
    assert len(snippet) <= 210, "snippets are decision material, not payloads"
    assert snippet.startswith("x" * 100)


# ---------------------------------------------------------------------------
# Golden template corpus: classification (kind, topic), scoping, walk
# ---------------------------------------------------------------------------


def test_template_ranking_matches_the_hand_derived_golden(
    make_backend: Callable[[Path], RipgrepBackend], template_vault: Path
):
    page = make_backend(template_vault).search("memory", limit=50)

    ranked = [(r.path, r.topic, r.kind, r.score) for r in page.results]
    assert ranked == list(GOLDEN_MEMORY_RANKING)
    assert page.total_count == len(GOLDEN_MEMORY_RANKING)


def test_named_topic_scopes_to_its_pages_and_its_stored_sources(
    make_backend: Callable[[Path], RipgrepBackend], template_vault: Path
):
    page = make_backend(template_vault).search("memory", topic="agentic-systems", limit=50)

    ranked = [(r.path, r.topic, r.kind, r.score) for r in page.results]
    assert ranked == list(GOLDEN_MEMORY_TOPIC_RANKING)
    assert page.total_count == 4, "root-level pages must drop out of a topic-scoped search"


def test_template_walk_pages_through_the_golden_order(
    make_backend: Callable[[Path], RipgrepBackend], template_vault: Path
):
    backend = make_backend(template_vault)

    pages = _walk(backend, "memory", limit=2)

    assert [len(page.results) for page in pages] == [2, 2, 2, 1]
    assert _walked_paths(pages) == [path for path, _, _, _ in GOLDEN_MEMORY_RANKING]
    assert {page.total_count for page in pages} == {len(GOLDEN_MEMORY_RANKING)}


# ---------------------------------------------------------------------------
# Topic scoping on the planted corpus
# ---------------------------------------------------------------------------


def test_empty_topic_searches_all_topics(
    make_backend: Callable[[Path], RipgrepBackend], planted_vault: Path
):
    page = make_backend(planted_vault).search(SEARCH_TOKEN, topic="", limit=50)

    assert "othertopic/other.md" in [result.path for result in page.results]
    assert page.total_count == len(PLANTED_RANKING)


def test_named_topic_excludes_every_other_topic(
    make_backend: Callable[[Path], RipgrepBackend], planted_vault: Path
):
    page = make_backend(planted_vault).search(SEARCH_TOKEN, topic="paging", limit=50)

    assert [(result.path, result.score) for result in page.results] == list(PAGING_SCOPED_RANKING)
    assert page.total_count == len(PAGING_SCOPED_RANKING)


def test_source_hits_carry_kind_source_and_their_owning_topic(
    make_backend: Callable[[Path], RipgrepBackend], planted_vault: Path
):
    page = make_backend(planted_vault).search(SEARCH_TOKEN, limit=50)

    by_path = {result.path: result for result in page.results}
    assert (by_path["sources/paging/zdoc.md"].kind, by_path["sources/paging/zdoc.md"].topic) == (
        "source",
        "paging",
    )
    assert (by_path["paging/apex.md"].kind, by_path["paging/apex.md"].topic) == ("page", "paging")


def test_unknown_topic_yields_an_empty_envelope_not_an_error(planted_vault: Path):
    page = RipgrepBackend(planted_vault).search(SEARCH_TOKEN, topic="ghost-topic")

    assert page.results == ()
    assert (page.total_count, page.has_more, page.next_cursor) == (0, False, "")


@pytest.mark.parametrize("topic", ["a/b", "../escape", ".knotica", "/absolute"])
def test_malformed_topic_names_are_refused(planted_vault: Path, topic: str):
    with pytest.raises(ValueError, match="[Tt]opic"):
        RipgrepBackend(planted_vault).search(SEARCH_TOKEN, topic=topic)


# ---------------------------------------------------------------------------
# Exclusions: dot-folders, dot-files, non-markdown
# ---------------------------------------------------------------------------


def test_dot_folders_dot_files_and_non_markdown_are_never_returned(
    make_backend: Callable[[Path], RipgrepBackend], planted_vault: Path
):
    pages = _walk(make_backend(planted_vault), SEARCH_TOKEN, limit=50)

    paths = _walked_paths(pages)
    assert "paging/apex.md" in paths, "non-vacuity: the same token IS found outside dot folders"
    assert not set(paths) & set(PLANTED_EXCLUDED)
    dotted = [p for p in paths if any(part.startswith(".") for part in PurePosixPath(p).parts)]
    assert dotted == []


# ---------------------------------------------------------------------------
# Limit bounds: clamping, never an error
# ---------------------------------------------------------------------------


def test_limit_above_the_maximum_is_clamped_to_fifty(dense_vault: Path):
    backend = RipgrepBackend(dense_vault)

    first = backend.search(SEARCH_TOKEN, limit=999)
    second = backend.search(SEARCH_TOKEN, cursor=first.next_cursor, limit=999)

    assert (len(first.results), first.has_more, first.total_count) == (50, True, 55)
    assert (len(second.results), second.has_more, second.next_cursor) == (5, False, "")


@pytest.mark.parametrize("limit", [0, -3])
def test_zero_and_negative_limits_clamp_to_one_result(planted_vault: Path, limit: int):
    page = RipgrepBackend(planted_vault).search(SEARCH_TOKEN, limit=limit)

    assert [(result.path, result.score) for result in page.results] == [("paging/apex.md", 9)]
    assert page.has_more is True


# ---------------------------------------------------------------------------
# Degenerate queries
# ---------------------------------------------------------------------------


def test_whitespace_only_query_returns_an_empty_envelope(planted_vault: Path):
    page = RipgrepBackend(planted_vault).search("   \t  ")

    assert page.results == ()
    assert (page.total_count, page.has_more, page.next_cursor) == (0, False, "")


def test_query_with_no_matches_returns_an_empty_envelope(planted_vault: Path):
    page = RipgrepBackend(planted_vault).search("nothing-contains-this-term")

    assert page.results == ()
    assert (page.total_count, page.has_more, page.next_cursor) == (0, False, "")


# ---------------------------------------------------------------------------
# Cursor abuse: malformed, tampered, stale -- typed rejection, never a wrong page
# ---------------------------------------------------------------------------

_SORT = SORT_SCORE_DESC_PATH_ASC

GARBAGE_CURSORS = (
    pytest.param("!!!not-base64!!!", id="not-base64"),
    # '+' and '/' are the STANDARD alphabet; the URL-safe decoder must fail
    # closed instead of silently discarding non-alphabet bytes.
    pytest.param("ab+/cd==", id="standard-alphabet-chars"),
    pytest.param("   ", id="whitespace-token"),
    pytest.param(_b64("Ceci n'est pas du JSON"), id="not-json"),
    pytest.param(_b64('{"query": "q", "sort'), id="truncated-json"),
    pytest.param(_b64("[1, 2, 3]"), id="json-array-not-object"),
    pytest.param(_b64('{"query": "q", "offset": 0}'), id="missing-sort-key"),
    pytest.param(_b64(f'{{"query": "q", "sort": "{_SORT}", "offset": 0, "x": 1}}'), id="extra-key"),
    pytest.param(_b64(f'{{"query": "q", "sort": "{_SORT}", "offset": -1}}'), id="negative-offset"),
    pytest.param(_b64(f'{{"query": "q", "sort": "{_SORT}", "offset": "5"}}'), id="string-offset"),
    pytest.param(_b64(f'{{"query": "q", "sort": "{_SORT}", "offset": 1.5}}'), id="float-offset"),
    pytest.param(_b64(f'{{"query": "q", "sort": "{_SORT}", "offset": true}}'), id="bool-offset"),
    pytest.param(_b64(f'{{"query": 7, "sort": "{_SORT}", "offset": 0}}'), id="non-string-query"),
    pytest.param(_b64('{"query": "q", "sort": "path-asc", "offset": 0}'), id="foreign-sort"),
)


@pytest.mark.parametrize("token", GARBAGE_CURSORS)
def test_garbage_cursors_are_rejected_with_the_typed_error(tmp_path: Path, token: str):
    backend = RipgrepBackend(tmp_path)

    with pytest.raises(InvalidCursorError):
        backend.search("q", cursor=token)


def test_a_cursor_minted_for_a_different_query_is_stale(planted_vault: Path):
    backend = RipgrepBackend(planted_vault)
    first = backend.search(SEARCH_TOKEN, limit=3)
    assert first.next_cursor, "precondition: the walk must have a next page"

    with pytest.raises(InvalidCursorError):
        backend.search("a completely different query", cursor=first.next_cursor)


def test_invalid_cursor_is_a_value_error_so_adapters_can_map_it():
    # The adapter layer owns the wire-level error envelope; the seam contract
    # is a dedicated ValueError subclass it can catch and translate.
    assert issubclass(InvalidCursorError, ValueError)


def test_an_offset_past_the_end_is_an_empty_final_page_not_an_error(planted_vault: Path):
    # A result set that shrank underneath an outstanding cursor ends the walk
    # gracefully instead of erroring.
    stale = encode_cursor(Cursor(query=SEARCH_TOKEN, sort=SORT_SCORE_DESC_PATH_ASC, offset=9999))

    page = RipgrepBackend(planted_vault).search(SEARCH_TOKEN, cursor=stale)

    assert page.results == ()
    assert (page.has_more, page.next_cursor) == (False, "")
    assert page.total_count == len(PLANTED_RANKING)


def test_cursor_tokens_round_trip_their_pagination_state():
    cursor = Cursor(query="q with spaces", sort=SORT_SCORE_DESC_PATH_ASC, offset=30)

    assert decode_cursor(encode_cursor(cursor)) == cursor
    assert resolve_offset("", "anything") == 0


# ---------------------------------------------------------------------------
# Protocol seam: any backend, no filesystem, same pagination contract
# ---------------------------------------------------------------------------


class CannedBackend:
    """Minimal in-memory backend: the pagination contract is engine-free."""

    def __init__(self, ranked: tuple[SearchResult, ...]) -> None:
        self._ranked = ranked

    def search(
        self,
        query: str,
        *,
        topic: str = "",
        cursor: str = "",
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> SearchPage:
        offset = resolve_offset(cursor, query)
        return paginate(self._ranked, query, offset, clamp_limit(limit))


def _canned_results(count: int) -> tuple[SearchResult, ...]:
    return tuple(
        SearchResult(
            topic="t", path=f"t/page-{i:02d}.md", snippet="snippet", score=count - i, kind="page"
        )
        for i in range(count)
    )


def test_backends_satisfy_the_runtime_checkable_protocol(tmp_path: Path):
    assert isinstance(RipgrepBackend(tmp_path), SearchBackend)
    assert isinstance(CannedBackend(()), SearchBackend)


def test_the_pagination_contract_holds_for_a_non_filesystem_backend():
    backend = CannedBackend(_canned_results(7))

    pages = _walk(backend, "q", limit=3)

    assert [len(page.results) for page in pages] == [3, 3, 1]
    assert _walked_paths(pages) == [f"t/page-{i:02d}.md" for i in range(7)]
    assert {page.total_count for page in pages} == {7}


def test_cursor_validation_guards_every_backend_not_just_ripgrep():
    backend = CannedBackend(_canned_results(3))

    with pytest.raises(InvalidCursorError):
        backend.search("q", cursor="!!!garbage!!!")


# ---------------------------------------------------------------------------
# Engine parity: ripgrep and the fallback are indistinguishable
# ---------------------------------------------------------------------------


def _fallback_backend(root: Path) -> RipgrepBackend:
    """Construct a backend with ``rg`` undiscoverable, then verify the engine."""
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr("knotica.search.ripgrep.shutil.which", lambda cmd: None)
        backend = RipgrepBackend(root)
    assert backend._rg_path is None, "fallback construction is vacuous: rg was still found"
    return backend


@pytest.mark.skipif(not RG_AVAILABLE, reason="ripgrep is not on PATH; parity needs both engines")
def test_both_engines_produce_identical_envelopes_on_the_planted_corpus(planted_vault: Path):
    rg_backend = RipgrepBackend(planted_vault)
    assert rg_backend._rg_path is not None

    rg_pages = [page.render() for page in _walk(rg_backend, SEARCH_TOKEN, limit=3)]
    fallback_pages = [
        page.render() for page in _walk(_fallback_backend(planted_vault), SEARCH_TOKEN, limit=3)
    ]

    assert rg_pages == fallback_pages


@pytest.mark.skipif(not RG_AVAILABLE, reason="ripgrep is not on PATH; parity needs both engines")
def test_both_engines_produce_identical_envelopes_on_the_template(template_vault: Path):
    rg_backend = RipgrepBackend(template_vault)
    assert rg_backend._rg_path is not None

    rg_pages = [page.render() for page in _walk(rg_backend, "memory", limit=2)]
    fallback_pages = [
        page.render() for page in _walk(_fallback_backend(template_vault), "memory", limit=2)
    ]

    assert rg_pages == fallback_pages
