"""The baseline query runner -- the headless thing the eval actually scores.

The eval harness needs to run the vault's own *query* operation without an MCP
client and without a human brain in the loop, so it can grade the answers a
golden question set produces. :class:`MessagesApiRunner` is that headless
executor: given a topic and a question, it drives the clone's own editable
``query.md`` prompt through a deterministic **retrieve -> read -> synthesize**
loop and returns a :class:`Prediction` (answer + citations + exact token usage).

Two design rules hold this seam:

* **The vault's own query artifact is what gets scored.** The system prompt is
  built from :func:`knotica.core.prompts.resolve_prompt` (the topic's ``query.md``
  body, verbatim) and :func:`knotica.core.schema.resolve_schema` (the effective
  schema) -- so evaluating the baseline evaluates exactly the editable artifact
  DSPy/SIA later evolve, never a hardcoded copy.

* **Retrieval is deterministic code; the model only synthesizes.** Unlike the
  interactive query operation (where the client's LLM chooses what to search and
  read), the runner performs the search (:class:`~knotica.search.RipgrepBackend`)
  and page reads (:func:`~knotica.core.page.read_page`) in-process as plain code,
  then hands the retrieved pages to the model for a *single* synthesis call at
  ``temperature=0``. There is no agentic tool-calling loop -- that keeps the run
  reproducible and the token accounting exact.

:attr:`Prediction.usage` is taken verbatim from the model response (via the
:class:`~knotica.evals.llm.LLMClient` seam); token counts are never hand-converted
across models, because that ``usage`` is the ground truth for the scalar's cost
term.

:class:`BaselineRunner` is the swap point: Phase 2 uses :class:`MessagesApiRunner`;
a later compiled program can replace it behind the same protocol, driven by the
same devset and metric.

**Structured synthesis contract.** The model is instructed to answer as a single
JSON object ``{"answer": <text>, "citations": [<source-key>, ...]}`` where each
citation is the bare key of a stored source (the file ``sources/<topic>/<key>.md``
that :func:`knotica.evals.citations.integrity` resolves against). A response that
does not parse into that shape raises :class:`MalformedResponseError` -- a typed,
visible failure rather than a silently empty answer, so a broken run scores zero
for that example instead of masking the problem.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from knotica.core.page import Page, read_page
from knotica.core.prompts import resolve_prompt
from knotica.core.schema import resolve_schema
from knotica.evals.llm import Completion, LLMClient, Message, TokenUsage
from knotica.search import RipgrepBackend
from knotica.store import VaultStore

__all__ = [
    "BaselineRunner",
    "MalformedResponseError",
    "MessagesApiRunner",
    "Prediction",
]

#: The operation prompt the runner drives -- the vault's own editable ``query.md``.
_QUERY_OPERATION = "query"

#: Top-K search pointers whose pages the runner reads before synthesizing. Bounded
#: so the synthesis call stays within a predictable token budget; the deterministic
#: score-descending/path-ascending ranking makes "top K" reproducible.
DEFAULT_MAX_PAGES = 5

#: Packaged answer-token budget for the single synthesis call. A module default for
#: now; ``evals.config`` centralizes the packaged/overridable eval constants later.
DEFAULT_MAX_TOKENS = 1024

#: Keys of the JSON object the model returns (see the module docstring).
_ANSWER_KEY = "answer"
_CITATIONS_KEY = "citations"

#: The strict JSON schema the synthesis call enforces via the Messages API
#: structured-outputs surface (:meth:`~knotica.evals.llm.LLMClient.complete`'s
#: ``json_schema``): an object with a required string ``answer`` and a required
#: array-of-strings ``citations``, and no other properties. It mirrors exactly the
#: shape :func:`_parse_structured_answer` requires, so the model's response is
#: schema-valid JSON at the source -- making the malformed-response class
#: near-impossible. The tolerant parse + :class:`MalformedResponseError` below stay
#: as defense-in-depth: structured outputs can still be short-circuited by a
#: ``max_tokens`` truncation or a refusal, and the parser keeps that visible.
_SYNTHESIS_JSON_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        _ANSWER_KEY: {"type": "string"},
        _CITATIONS_KEY: {"type": "array", "items": {"type": "string"}},
    },
    "required": [_ANSWER_KEY, _CITATIONS_KEY],
    "additionalProperties": False,
}

_CODE_FENCE = "```"

#: The synthesis directive appended after the vault's ``query.md`` body and schema.
#: It pins the machine-readable output shape the runner parses; the ``query.md``
#: body above it still governs *how* to answer (citation discipline, hedging on
#: low-confidence/stale pages, refusing to answer from outside the wiki).
_SYNTHESIS_DIRECTIVE = (
    "## Headless eval synthesis format\n"
    "\n"
    "You are being run headlessly, with no tools: the vault pages relevant to the "
    "question have already been retrieved and are included in the user message. "
    "Answer using only those pages -- do not invent sources or draw on outside "
    "knowledge.\n"
    "\n"
    "Respond with a single JSON object and nothing else, of exactly this shape:\n"
    '{"answer": "<your answer as markdown text>", '
    '"citations": ["<source-citation-key>", ...]}\n'
    "\n"
    "Each citation is the bare key of a stored source you relied on -- the key such "
    "that the file `sources/<topic>/<key>.md` holds that source. Use an empty list "
    "when the pages you used cite no stored source. Do not wrap the JSON in code "
    "fences or add prose around it."
)


class MalformedResponseError(ValueError):
    """The model's synthesis response did not parse into the structured answer shape.

    Raised (never swallowed) when the completion is not a JSON object carrying a
    string ``answer`` and an array-of-strings ``citations``. Subclasses
    :class:`ValueError` to match the codebase's parse-error convention.
    """


@dataclass(frozen=True, slots=True)
class Prediction:
    """One baseline answer: the synthesized text, its citations, and exact usage.

    ``citations`` are bare stored-source keys (see the module docstring); the
    field is a ``list`` to match the DSPy prediction shape the program adapter
    wraps. ``usage`` is verbatim from the model response -- the cost term's
    ground truth, never hand-converted across models.
    """

    answer: str
    citations: list[str]
    usage: TokenUsage


@runtime_checkable
class BaselineRunner(Protocol):
    """Structural seam over the headless baseline query op -- the swap point.

    Phase 2's implementation is :class:`MessagesApiRunner`; a later compiled
    program can take its place behind this exact protocol, scored by the same
    devset and metric.
    """

    def run(self, store: VaultStore, topic: str, question: str) -> Prediction:
        """Answer ``question`` for ``topic`` against ``store``, returning a prediction."""
        ...


class MessagesApiRunner:
    """The default :class:`BaselineRunner`: deterministic retrieve/read + one synthesis call.

    Args:
        llm_client: The injected LLM seam. Production passes an
            :class:`~knotica.evals.llm.AnthropicClient`; tests inject a
            ``FakeLLMClient`` for a zero-network run.
        worker_snapshot: The exact dated model snapshot to synthesize with. Always
            an argument (never hardcoded) so the pinned default lives in
            ``evals.config``.
    """

    def __init__(self, llm_client: LLMClient, worker_snapshot: str) -> None:
        self._llm = llm_client
        self._worker_snapshot = worker_snapshot

    def run(self, store: VaultStore, topic: str, question: str) -> Prediction:
        """Drive the clone's ``query.md`` headlessly and return a :class:`Prediction`.

        Resolves the vault's own query prompt and schema, retrieves the top pages
        deterministically, synthesizes a cited answer in one ``temperature=0``
        call, and returns the answer + parsed citations + the call's exact usage.
        """
        prompt = resolve_prompt(store, _QUERY_OPERATION, topic)
        schema = resolve_schema(store, topic)
        pages = self._retrieve(store, topic, question)
        completion = self._synthesize(prompt.body, schema.merged, topic, question, pages)
        answer, citations = _parse_structured_answer(completion.text)
        return Prediction(answer=answer, citations=citations, usage=completion.usage)

    def _retrieve(self, store: VaultStore, topic: str, question: str) -> list[Page]:
        """Search the clone for the question, then read the top-K matching pages."""
        backend = RipgrepBackend(_store_root(store))
        page = backend.search(question, topic=topic, limit=DEFAULT_MAX_PAGES)
        return [read_page(store, topic, result.path) for result in page.results]

    def _synthesize(
        self,
        query_prompt: str,
        schema: str,
        topic: str,
        question: str,
        pages: list[Page],
    ) -> Completion:
        """Make the single synthesis call at ``temperature=0`` and return its completion.

        Passes :data:`_SYNTHESIS_JSON_SCHEMA` so the Messages API constrains the
        model to schema-valid JSON at the source; the tolerant parse downstream
        remains as defense-in-depth for the residual truncation/refusal cases.
        """
        return self._llm.complete(
            snapshot=self._worker_snapshot,
            system=_assemble_system(query_prompt, schema),
            messages=[Message(role="user", content=_assemble_user(topic, question, pages))],
            temperature=0.0,
            max_tokens=DEFAULT_MAX_TOKENS,
            json_schema=_SYNTHESIS_JSON_SCHEMA,
        )


def _store_root(store: VaultStore) -> Path:
    """The filesystem root behind a rooted vault store, for in-process search.

    The runner searches with :class:`~knotica.search.RipgrepBackend`, which scans a
    directory rather than the abstract store, so it needs the store's on-disk root
    (a ``LocalFSStore`` exposes ``.root``). A store without one cannot be searched
    in-process -- a typed error names the requirement rather than failing obscurely.
    """
    root = getattr(store, "root", None)
    if root is None:
        raise TypeError(
            "MessagesApiRunner needs a filesystem-rooted vault store (one exposing a "
            "`.root` path, like LocalFSStore) to run in-process search over the clone."
        )
    return Path(root)


def _assemble_system(query_prompt: str, schema: str) -> str:
    """Compose the system prompt: the vault's ``query.md`` body, schema, then format.

    The ``query.md`` body leads verbatim -- it is the artifact under evaluation --
    followed by the effective schema (entity types + field meanings) and the
    machine-readable output directive the runner parses.
    """
    return "\n\n".join(
        [
            query_prompt,
            "## Effective schema\n\n" + schema,
            _SYNTHESIS_DIRECTIVE,
        ]
    )


def _assemble_user(topic: str, question: str, pages: list[Page]) -> str:
    """Compose the user message: the question plus every retrieved page's full text."""
    header = f"Topic: {topic}\n\nQuestion: {question}"
    if not pages:
        return header + "\n\n## Retrieved pages\n\n(No matching pages were found.)"
    rendered = "\n\n".join(_format_page(page) for page in pages)
    return header + "\n\n## Retrieved pages\n\n" + rendered


def _format_page(page: Page) -> str:
    """Render one retrieved page as a labeled block: its path, then its raw content.

    The raw content includes the frontmatter block, so the model sees each page's
    declared ``sources``/``confidence``/``status`` -- the signals the query prompt
    asks it to weigh and flag.
    """
    return f"### {page.path}\n\n{page.raw.strip()}"


def _parse_structured_answer(text: str) -> tuple[str, list[str]]:
    """Parse the model's JSON response into ``(answer, citations)``, or raise.

    Tolerates a surrounding markdown code fence; then requires a JSON object with
    a string ``answer`` and a list-of-strings ``citations``. Any deviation raises
    :class:`MalformedResponseError` (typed and visible, never a silent empty answer).
    """
    payload = _load_json_object(_strip_code_fence(text))
    answer = payload.get(_ANSWER_KEY)
    if not isinstance(answer, str):
        raise MalformedResponseError(
            f"The baseline runner's response must contain a string {_ANSWER_KEY!r} field, "
            f"got {answer!r}."
        )
    citations = payload.get(_CITATIONS_KEY)
    if not isinstance(citations, list) or any(not isinstance(item, str) for item in citations):
        raise MalformedResponseError(
            f"The baseline runner's response {_CITATIONS_KEY!r} must be an array of "
            f"citation-key strings, got {citations!r}."
        )
    return answer, citations


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


def _load_json_object(text: str) -> dict[str, object]:
    """Parse ``text`` as a JSON object, raising :class:`MalformedResponseError` otherwise."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise MalformedResponseError(
            f"The baseline runner's response was not valid JSON: {error}."
        ) from error
    if not isinstance(payload, dict):
        raise MalformedResponseError(
            "The baseline runner's response must be a JSON object with "
            f"{_ANSWER_KEY!r} and {_CITATIONS_KEY!r} fields, got {type(payload).__name__}."
        )
    return payload
