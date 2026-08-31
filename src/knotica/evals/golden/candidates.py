"""The candidate-dict shape: its key names and the boundary parsers that read it.

A *candidate* is the loosely-typed dict that travels the whole write side -- emitted
by the model in :mod:`~knotica.evals.golden.synthesize`, written to the staging
file, hand-edited by a human, and read back by :mod:`~knotica.evals.golden.freeze`
to build a frozen record. Both ends of that journey must agree on the key names and
on what counts as a well-formed field, so both are declared once here.

The parsers are strict by design: a human-edited staging file is untrusted input,
and a candidate missing its question or carrying a non-string citation raises
:class:`~knotica.evals.golden.GoldenCandidateError` rather than freezing a
malformed record into the eval set.
"""

from collections.abc import Mapping

from knotica.evals.golden.contract import GoldenCandidateError

#: Candidate JSON keys (the shape both the LLM emits and ``freeze`` reads).
_QUESTION_KEY = "question"
_ANSWER_KEY = "reference_answer"
_CITATIONS_KEY = "citations"
_PAGES_KEY = "pages_used"


def _required_candidate_str(candidate: Mapping[str, object], key: str) -> str:
    """Return a required non-empty string candidate field, typed-error otherwise."""
    value = candidate.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GoldenCandidateError(
            f"a golden candidate must carry a non-empty string {key!r}, got {value!r}."
        )
    return value


def _optional_candidate_str_list(candidate: Mapping[str, object], key: str) -> list[str]:
    """Return an optional list-of-strings candidate field (default ``[]``), typed-error otherwise."""
    return list(_optional_candidate_str_tuple(candidate, key))


def _optional_candidate_str_tuple(candidate: Mapping[str, object], key: str) -> tuple[str, ...]:
    """Return an optional list-of-strings candidate field as a tuple (default ``()``)."""
    value = candidate.get(key, [])
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
        raise GoldenCandidateError(
            f"a golden candidate's {key!r} must be a list of strings, got {value!r}."
        )
    return tuple(value)
