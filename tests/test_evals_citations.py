"""Behavioral spec for deterministic citation integrity.

`citations.integrity(store, topic, prediction)` scores what fraction of a
baseline answer's citations resolve to a *stored* source under
``sources/<topic>/<key>.md`` -- reusing the vault's own citation-resolution
check (the ``CITATION_UNRESOLVED`` lint semantics). It is the deterministic,
zero-LLM half of citation validity: it measures whether an answer's citations
point at real evidence, not whether that evidence supports the claim
(faithfulness is a deferred, judge-based extension, out of scope for v1).

These tests run against a real vault fixture: a fresh ``template_vault`` clone
with a known stored source, plus extra sources planted per-test so each case
controls exactly which keys resolve. The score is a pure fraction over
``store.exists`` -- no network, no LLM.

--------------------------------------------------------------------------------
INTERFACE NOTES

- **`prediction` is duck-typed on ``.citations``.** ``integrity`` reads only the
  ``citations`` attribute (the shipped module formalizes this as a
  ``CitingPrediction`` Protocol), staying decoupled from the concrete runner
  ``Prediction`` -- a not-yet-built sibling. The test stand-in is a plain
  ``SimpleNamespace`` exposing ``citations``; that it satisfies the seam *is* part
  of the contract under test (any citations-bearing object works).

- **Empty citations score ``1.0`` (vacuous), mirroring the lint check.** A page
  or answer that cites nothing raises no unresolved-citation violation -- it makes
  no claim the vault cannot back -- so its integrity is vacuously perfect. The
  answer-quality leg of the per-example score independently penalizes an uncited
  answer, so this vacuous reading does not reward citation-dropping.

Written concurrently with the implementation (disjoint files). The integrity
interface converged mid-session (the implementer settled from a bare-sequence
draft to this duck-typed ``prediction`` seam with a ``1.0`` empty contract);
these tests are aligned to that settled shape and are GREEN against it.
--------------------------------------------------------------------------------
"""

import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from knotica.evals.citations import integrity
from knotica.store import LocalFSStore

#: The topic whose ``sources/`` tree the fixtures populate.
TOPIC = "agentic-systems"

#: A citation key the base ``template_vault`` already stores a source for.
STORED_SOURCE_KEY = "wang2024awm"


@pytest.fixture(autouse=True)
def _forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Citation integrity is a pure filesystem check -- any socket use is a defect.

    Replacing ``socket.socket`` makes an unexpected network touch fail loudly,
    actively enforcing the zero-network guarantee.
    """

    def _blocked(*args: object, **kwargs: object) -> object:
        raise RuntimeError("network access is forbidden in the citations test suite")

    monkeypatch.setattr(socket, "socket", _blocked)


def _store(vault_root: Path) -> LocalFSStore:
    return LocalFSStore(vault_root)


def _prediction(citations: list[str]) -> object:
    """A duck-typed baseline prediction exposing only ``.citations`` (see INTERFACE NOTES)."""
    return SimpleNamespace(
        answer="A cited answer drawn from the vault.",
        citations=list(citations),
        usage=None,
    )


def _plant_source(vault_root: Path, key: str) -> None:
    """Store a source under ``sources/<TOPIC>/<key>.md`` so that key resolves."""
    source_path = vault_root / "sources" / TOPIC / f"{key}.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(f"# {key}\n\nStored source body for {key}.\n", encoding="utf-8")


def _plant_sources(vault_root: Path, keys: list[str]) -> None:
    """Store a source for each key -- the planting loop lives here, not in a test body."""
    for key in keys:
        _plant_source(vault_root, key)


# ---------------------------------------------------------------------------
# Every citation resolving -> perfect integrity (1.0)
# ---------------------------------------------------------------------------


def test_all_citations_resolving_to_stored_sources_scores_one(template_vault: Path) -> None:
    _plant_source(template_vault, "smith2023alpha")
    prediction = _prediction([STORED_SOURCE_KEY, "smith2023alpha"])

    score = integrity(_store(template_vault), TOPIC, prediction)

    assert score == pytest.approx(1.0), (
        f"every citation resolves to a stored source, so integrity must be 1.0; got {score!r}"
    )


# ---------------------------------------------------------------------------
# No citation resolving -> zero integrity (0.0)
# ---------------------------------------------------------------------------


def test_no_citation_resolving_to_a_stored_source_scores_zero(template_vault: Path) -> None:
    # None of these keys have a stored source; each cites phantom evidence.
    prediction = _prediction(["ghost2099none", "phantom2088void"])

    score = integrity(_store(template_vault), TOPIC, prediction)

    assert score == pytest.approx(0.0), (
        f"no citation resolves to stored evidence, so integrity must be 0.0; got {score!r}"
    )


# ---------------------------------------------------------------------------
# A mix -> the exact resolved fraction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stored_keys, phantom_keys, expected",
    [
        pytest.param(["stored2020a"], ["missing2099x"], 0.5, id="one-of-two"),
        pytest.param(["stored2020a", "stored2020b"], ["missing2099x"], 2 / 3, id="two-of-three"),
        pytest.param(
            ["stored2020a"],
            ["missing2099x", "missing2099y", "missing2099z"],
            0.25,
            id="one-of-four",
        ),
        pytest.param(
            ["stored2020a", "stored2020b", "stored2020c"],
            ["missing2099x"],
            0.75,
            id="three-of-four",
        ),
    ],
)
def test_a_mixed_citation_list_scores_the_resolved_fraction(
    template_vault: Path,
    stored_keys: list[str],
    phantom_keys: list[str],
    expected: float,
) -> None:
    # Plant real sources only for the stored keys; the phantom keys name sources
    # that were never stored. integrity is the fraction that resolves.
    _plant_sources(template_vault, stored_keys)
    prediction = _prediction(stored_keys + phantom_keys)

    score = integrity(_store(template_vault), TOPIC, prediction)

    assert score == pytest.approx(expected), (
        f"{len(stored_keys)} of {len(stored_keys) + len(phantom_keys)} citations "
        f"resolve, so integrity must be {expected}; got {score!r}"
    )


# ---------------------------------------------------------------------------
# The empty-citations edge (pinned: vacuous 1.0, the implementer's settled contract)
# ---------------------------------------------------------------------------


def test_an_answer_with_no_citations_is_vacuously_perfect(template_vault: Path) -> None:
    # An answer that cites nothing makes no unresolvable claim, so -- mirroring the
    # CITATION_UNRESOLVED lint check it reuses -- its citation integrity is
    # vacuously perfect (1.0). Citation-dropping is not rewarded overall because
    # the answer-quality leg penalizes an uncited answer independently.
    prediction = _prediction([])

    score = integrity(_store(template_vault), TOPIC, prediction)

    assert score == pytest.approx(1.0), (
        "an answer citing nothing has no unresolved citation, so integrity is "
        f"vacuously 1.0 (mirroring the lint check); got {score!r}"
    )


# ---------------------------------------------------------------------------
# Determinism: same inputs -> same score, independent of citation order
# ---------------------------------------------------------------------------


def test_the_score_is_stable_across_repeated_calls(template_vault: Path) -> None:
    _plant_source(template_vault, "smith2023alpha")
    store = _store(template_vault)
    prediction = _prediction([STORED_SOURCE_KEY, "smith2023alpha", "ghost2099none"])

    first = integrity(store, TOPIC, prediction)
    second = integrity(store, TOPIC, prediction)

    assert first == second == pytest.approx(2 / 3), (
        "integrity must be deterministic across repeated calls on identical "
        f"inputs; got {first!r} then {second!r}"
    )


def test_the_score_is_independent_of_citation_order(template_vault: Path) -> None:
    _plant_source(template_vault, "smith2023alpha")
    store = _store(template_vault)
    forward = _prediction([STORED_SOURCE_KEY, "smith2023alpha", "ghost2099none"])
    reversed_order = _prediction(["ghost2099none", "smith2023alpha", STORED_SOURCE_KEY])

    assert integrity(store, TOPIC, forward) == pytest.approx(
        integrity(store, TOPIC, reversed_order)
    ), "reordering the citation list must not change the resolved fraction"


# ---------------------------------------------------------------------------
# The score is always a fraction in [0,1]
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stored_keys, phantom_keys",
    [
        pytest.param(["stored2020a", "stored2020b", "stored2020c"], [], id="all-resolve"),
        pytest.param([], ["missing2099x", "missing2099y", "missing2099z"], id="none-resolve"),
        pytest.param(
            ["stored2020a", "stored2020b"],
            ["missing2099v", "missing2099w", "missing2099x", "missing2099y", "missing2099z"],
            id="mostly-unresolved",
        ),
        pytest.param(
            ["stored2020a", "stored2020b", "stored2020c", "stored2020d", "stored2020e"],
            ["missing2099x"],
            id="mostly-resolved",
        ),
    ],
)
def test_integrity_is_always_bounded_to_the_unit_interval(
    template_vault: Path,
    stored_keys: list[str],
    phantom_keys: list[str],
) -> None:
    _plant_sources(template_vault, stored_keys)
    prediction = _prediction(stored_keys + phantom_keys)

    score = integrity(_store(template_vault), TOPIC, prediction)

    assert 0.0 <= score <= 1.0, f"citation integrity must be a fraction in [0,1]; got {score!r}"


# ---------------------------------------------------------------------------
# Purity: the module imports no heavy LLM dependencies
# ---------------------------------------------------------------------------


def test_importing_the_citations_module_pulls_in_no_llm_dependencies() -> None:
    # The citation core is pure and must stay cheap to import: no ``anthropic``,
    # no ``dspy`` (even transitively -- e.g. via an errant runner import). A fresh
    # interpreter is required; a same-process ``sys.modules`` check false-positives
    # once a sibling eval test has loaded ``dspy`` in the full-suite run.
    script = (
        "import sys\n"
        "import knotica.evals.citations\n"
        "import knotica.evals.scalar\n"
        "leaked = sorted(\n"
        "    m for m in sys.modules\n"
        "    if m in ('anthropic', 'dspy') or m.startswith(('anthropic.', 'dspy.'))\n"
        ")\n"
        "assert not leaked, leaked\n"
        "print('CITATION_CORE_PURE_OK')\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        "importing the citation core must not import anthropic or dspy; "
        f"child stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "CITATION_CORE_PURE_OK" in result.stdout
