"""The generate stage: entity pages in, reviewable candidates out, nothing committed.

:func:`bootstrap` asks the injected LLM for one candidate QA pair per entity page
and writes them to the *uncommitted* review scratchpad for a human to edit. It never
writes ``golden.jsonl`` and never commits -- only the human-gated
:func:`~knotica.evals.golden.freeze` does that. Its signature carries no
``vault_root`` precisely because it must not commit.

The staging write is the one place the eval subsystem writes outside
:class:`~knotica.core.transaction.VaultTransaction`: the scratchpad is deliberately
un-committed and the ``VaultStore`` protocol offers no un-committed write, so
:func:`_write_staging` goes to the store's on-disk root directly -- and
:func:`_staging_abspath` re-implements the store's own path confinement for it,
because bypassing the store also bypasses that guard.

The synthesis prompt is a packaged constant -- code, not vault content -- so a
generation run is reproducible for a fixed page set + snapshot.
"""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from knotica.core.links import iter_page_paths
from knotica.core.page import Page, read_page, topic_relative_page_name
from knotica.evals.golden.candidates import (
    _ANSWER_KEY,
    _CITATIONS_KEY,
    _PAGES_KEY,
    _QUESTION_KEY,
    _optional_candidate_str_list,
    _required_candidate_str,
)
from knotica.evals.golden.contract import GoldenCandidateError, golden_staging_path
from knotica.evals.golden.support import _SUPPORT_KEY, _build_support
from knotica.evals.llm import LLMClient, Message
from knotica.store import VaultStore

#: Answer-token budget for one synthesis call. A module default here; the packaged
#: eval constants live in ``evals.config`` and the caller passes ``snapshot``.
_BOOTSTRAP_MAX_TOKENS = 1024

#: The topic's schema overlay -- structural, not an entity page, so it is excluded
#: from bootstrap generation (mirrors ``harness``'s content-page rule; kept a local
#: constant per the convention of not importing a sibling module's private symbol).
_SCHEMA_OVERLAY_FILENAME = "SCHEMA.md"

_CODE_FENCE = "```"

#: The packaged synthesis prompt -- code, not vault content, so it is stable and
#: hashable (a generation run is reproducible for a fixed page set + snapshot).
_BOOTSTRAP_SYSTEM_PROMPT = (
    "You are helping bootstrap a held-out evaluation set for a knowledge wiki.\n"
    "\n"
    "You will be given one wiki entity page (its frontmatter and body). Read it and "
    "write ONE high-quality question-and-answer pair that a correct answer to this "
    "wiki should get right:\n"
    "\n"
    "- The question must be answerable **only** from this page -- a specific, factual "
    "question about the entity, not a vague or yes/no one.\n"
    "- The reference answer must be grounded strictly in the page; do not add outside "
    "knowledge.\n"
    "- List the citation keys the answer relies on: the bare keys of the stored "
    "sources the page cites (its `sources` frontmatter values), such that "
    "`sources/<topic>/<key>.md` holds that source. Use an empty list if the page "
    "cites no stored source.\n"
    "- List 1 to 3 SHORT support quotes: verbatim excerpts copied "
    "character-for-character from the page above that the reference answer is "
    "grounded in. Copy each one exactly as it appears (do not paraphrase, "
    "summarize, re-wrap, or fix typos) and keep each to a single sentence or "
    "phrase.\n"
    "\n"
    "Respond with a single JSON object and nothing else, of exactly this shape:\n"
    '{"question": "<one question>", "reference_answer": "<grounded answer>", '
    '"citations": ["<source-key>", ...], '
    '"support_quotes": ["<verbatim excerpt>", ...]}\n'
    "\n"
    "Do not wrap the JSON in code fences or add any prose around it."
)


def bootstrap(
    store: VaultStore,
    topic: str,
    llm_client: LLMClient,
    snapshot: str,
    pages: Sequence[str] | None = None,
) -> list[dict[str, object]]:
    """Synthesize golden-set candidates from a topic's entity pages (the generate stage).

    For each of the topic's entity pages, asks the injected LLM (at
    ``temperature=0`` with ``snapshot``) to synthesize one candidate
    ``(question, reference_answer, citations)`` triple grounded in that page,
    plus verbatim support quotes located back to deterministic 1-based line
    ranges (an optional ``support`` list) so a reviewer can see and deep-link the
    evidence. The candidates are written to the *uncommitted* review staging file
    (:func:`~knotica.evals.golden.golden_staging_path`) for a human to edit and
    accept, and also returned so the caller can surface them. This never writes
    ``golden.jsonl`` and never commits -- only the human-gated
    :func:`~knotica.evals.golden.freeze` does that.

    Args:
        store: The vault storage backend (must expose an on-disk ``root``).
        topic: The topic whose entity pages seed the candidates.
        llm_client: The injected LLM seam; tests pass a ``FakeLLMClient`` for a
            zero-network run.
        snapshot: The exact dated model snapshot to synthesize with (the caller
            passes the pinned worker/strong snapshot from ``evals.config``).
        pages: Optionally restricts synthesis to a subset of the topic's entity
            pages, matched by vault-relative path and filtered in place
            (existing page order is preserved). ``None`` (the default)
            synthesizes from every entity page -- today's behavior,
            byte-identical. An explicit empty sequence selects zero pages and
            returns (and stages) an empty candidate list rather than falling
            back to "all pages".

    Returns:
        The generated candidate dicts, one per selected entity page, in page
        order.

    Raises:
        GoldenCandidateError: If a synthesis response does not parse into the
            candidate shape.
    """
    selected_pages = entity_pages(store, topic)
    if pages is not None:
        allowed_paths = set(pages)
        selected_pages = [page for page in selected_pages if page.path in allowed_paths]
    candidates = [
        _synthesize_candidate(llm_client, snapshot, topic, page) for page in selected_pages
    ]
    _write_staging(store, topic, candidates)
    return candidates


def entity_pages(store: VaultStore, topic: str) -> list[Page]:
    """Read the topic's entity pages -- every content page bar the schema overlay.

    Public: the golden bootstrap and the trainset cold-start both synthesize
    from this same page set, so the definition of "entity page" stays single.
    """
    overlay = f"{topic}/{_SCHEMA_OVERLAY_FILENAME}"
    return [
        read_page(store, topic, path) for path in iter_page_paths(store, topic) if path != overlay
    ]


def _synthesize_candidate(
    llm_client: LLMClient, snapshot: str, topic: str, page: Page
) -> dict[str, object]:
    """Make one ``temperature=0`` synthesis call for ``page`` and parse the candidate."""
    completion = llm_client.complete(
        snapshot=snapshot,
        system=_BOOTSTRAP_SYSTEM_PROMPT,
        messages=[Message(role="user", content=_render_page_prompt(topic, page))],
        temperature=0.0,
        max_tokens=_BOOTSTRAP_MAX_TOKENS,
    )
    return _parse_candidate(completion.text, _page_name(topic, page), page.raw)


def _render_page_prompt(topic: str, page: Page) -> str:
    """Compose the user message: the topic and the entity page's full raw text."""
    return f"Topic: {topic}\n\nEntity page ({_page_name(topic, page)}):\n\n{page.raw.strip()}"


def _page_name(topic: str, page: Page) -> str:
    """The topic-relative page name (``agentic-systems/react.md`` -> ``react``)."""
    return topic_relative_page_name(topic, page.path)


def _parse_candidate(text: str, page_name: str, page_raw: str) -> dict[str, object]:
    """Parse one synthesis response into a candidate dict, or raise a typed error.

    Adds ``pages_used`` deterministically (the entity page the candidate was
    generated from) -- the model supplies only the question/answer/citations and
    the raw support quotes. Each supplied quote is located in ``page_raw`` and
    turned into a support provenance entry with a deterministic, 1-based inclusive
    line range (model-supplied line numbers are never trusted); the key is omitted
    when the model returned no usable quote.
    """
    payload = _load_candidate_json(text)
    question = _required_candidate_str(payload, _QUESTION_KEY)
    reference_answer = _required_candidate_str(payload, _ANSWER_KEY)
    citations = _optional_candidate_str_list(payload, _CITATIONS_KEY)
    candidate: dict[str, object] = {
        _QUESTION_KEY: question,
        _ANSWER_KEY: reference_answer,
        _CITATIONS_KEY: citations,
        _PAGES_KEY: [page_name],
    }
    support = _build_support(payload, page_name, page_raw)
    if support:
        candidate[_SUPPORT_KEY] = support
    return candidate


def _load_candidate_json(text: str) -> dict[str, object]:
    """Parse the response text (tolerating a code fence) into a JSON object."""
    try:
        payload = json.loads(_strip_code_fence(text))
    except json.JSONDecodeError as error:
        raise GoldenCandidateError(
            f"the synthesis response was not valid JSON: {error}."
        ) from error
    if not isinstance(payload, dict):
        raise GoldenCandidateError(
            "the synthesis response must be a JSON object with "
            f"{_QUESTION_KEY!r}, {_ANSWER_KEY!r} and {_CITATIONS_KEY!r} fields, "
            f"got {type(payload).__name__}."
        )
    return payload


def _strip_code_fence(text: str) -> str:
    """Return ``text`` with a single surrounding markdown code fence removed, if present."""
    stripped = text.strip()
    if not stripped.startswith(_CODE_FENCE):
        return stripped
    lines = stripped.splitlines()
    body = lines[1:]  # drop the opening ``` (or ```json) line
    if body and body[-1].strip() == _CODE_FENCE:
        body = body[:-1]
    return "\n".join(body).strip()


def _write_staging(
    store: VaultStore, topic: str, candidates: Sequence[Mapping[str, object]]
) -> None:
    """Write the candidates to the uncommitted review staging file.

    A raw filesystem write to the store's on-disk root -- the one place the eval
    subsystem writes outside :class:`~knotica.core.transaction.VaultTransaction`,
    because the staging file is a deliberately un-committed review scratchpad and
    the ``VaultStore`` protocol offers no un-committed write.
    """
    body = "".join(json.dumps(candidate, ensure_ascii=False) + "\n" for candidate in candidates)
    path = _staging_abspath(store, topic)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _staging_abspath(store: VaultStore, topic: str) -> Path:
    """Resolve the staging file's absolute path, refusing any escape from the vault root.

    Replicates the store's path-confinement for this one raw write (the store's
    own confinement is bypassed here), so a malformed ``topic`` can never aim the
    write outside the vault.
    """
    root = getattr(store, "root", None)
    if root is None:
        raise TypeError(
            "bootstrap needs a filesystem-rooted vault store (one exposing a `.root` "
            "path, like LocalFSStore) to write the review staging file."
        )
    resolved_root = Path(root).resolve()
    candidate = (resolved_root / golden_staging_path(topic)).resolve()
    if not candidate.is_relative_to(resolved_root):
        raise ValueError(f"the golden staging path for topic {topic!r} escapes the vault root.")
    return candidate
