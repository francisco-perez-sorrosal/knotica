"""The vault side of discovery dedup -- stored sources close the loop.

A field report found fourteen approved suggestions all pointing at one SEP
entry the vault had ingested weeks earlier: discovery deduped against the
suggestion queue but never against what ``store_source`` had already
persisted. ``core.source_inventory.stored_source_url_keys`` is the vault's
half of that handshake -- the normalized URL identity of every stored source
under ``sources/<topic>/`` -- and ``refresh_suggestions_for_gaps`` drops (and
counts) candidates whose identity it already holds.

Identity is URL-only by design: stored provenance records no DOI, so the
comparison runs URL-to-URL through the same ``discovery.normalize`` rule the
service dedups with -- which is what lets an *archive-edition* candidate match
a *canonically*-recorded ingest of the same entry.

Zero network: the drain is driven by a canned fake service, and the inventory
is pure vault I/O.
"""

from __future__ import annotations

from pathlib import Path

from knotica.core import gapfill
from knotica.core.operations.store_source import store_source
from knotica.core.records import GapEvidence, GapRecord
from knotica.core.source_inventory import stored_source_url_keys
from knotica.core.transaction import VaultTransaction
from knotica.store import LocalFSStore

TOPIC = "agentic-systems"

_SEP_CANONICAL = "https://plato.stanford.edu/entries/bounded-rationality/"
_SEP_EDITION = "https://plato.stanford.edu/archIves/win2024/entries/bounded-rationality/"


def _gap_record(*, gap_id: str) -> GapRecord:
    return GapRecord(
        gap_id=gap_id,
        topic=TOPIC,
        qa_id=f"golden-{gap_id}",
        fault_class="genuine_gap",
        status="open",
        classifier_version=1,
        detected_generation=5,
        detected_at="2026-07-18T23:01:00Z",
        scalar_at_detection=0.9493,
        baseline_scalar=0.96,
        question=f"What does {gap_id} leave unanswered?",
        reference_pages=("speculative-decoding",),
        reference_pages_exist=False,
        evidence=GapEvidence(
            quality_delta=-0.12,
            qa_accuracy_delta=-0.12,
            citation_validity_delta=0.0,
            retrieval_trace=(),
            pages_added=(),
            pages_removed=(),
            prior_generation=4,
        ),
        manifest_ref=f"{TOPIC}/.knotica/eval-runs/gen-5/manifest.json",
    )


def _candidate(url: str):
    from knotica.discovery.records import SourceCandidate

    return SourceCandidate(
        url=url,
        title="Bounded Rationality",
        snippet="The Stanford Encyclopedia entry.",
        source_provider="fake",
    )


class _FakeDiscoveryService:
    """Replays one canned candidate list for every ``discover`` call."""

    def __init__(self, candidates) -> None:
        self._candidates = list(candidates)
        self.calls = 0

    def discover(self, query):
        self.calls += 1
        return list(self._candidates)


def _store_sep_source(store: LocalFSStore, vault: Path, *, key: str = "wheeler-sep") -> None:
    store_source(
        store,
        vault,
        TOPIC,
        key,
        "Bounded Rationality (SEP)",
        "Herbert Simon proposed satisficing...",
        _SEP_CANONICAL,
    )


def _seed_raw_source(store: LocalFSStore, vault: Path, name: str, body: str) -> None:
    with VaultTransaction(store, vault, "test_seed", TOPIC, "seed raw source") as txn:
        txn.write(f"sources/{TOPIC}/{name}", body)


# ---------------------------------------------------------------------------
# stored_source_url_keys -- what the vault declares it already holds
# ---------------------------------------------------------------------------


def test_a_stored_sources_origin_url_appears_in_the_inventory(template_vault: Path) -> None:
    # Delta against the template vault's own shipped sources, not an absolute
    # set -- the fixture is a live template and may grow.
    store = LocalFSStore(template_vault)
    baseline = stored_source_url_keys(store, TOPIC)

    _store_sep_source(store, template_vault)

    keys = stored_source_url_keys(store, TOPIC)
    assert keys - baseline == frozenset({"https://plato.stanford.edu/entries/bounded-rationality"})


def test_a_topic_with_no_stored_sources_reports_an_empty_inventory(template_vault: Path) -> None:
    assert stored_source_url_keys(LocalFSStore(template_vault), "no-sources-topic") == frozenset()


def test_chunked_ingests_sharing_one_origin_collapse_to_one_identity(
    template_vault: Path,
) -> None:
    store = LocalFSStore(template_vault)
    baseline = stored_source_url_keys(store, TOPIC)
    _store_sep_source(store, template_vault, key="wheeler-sep-1")
    _store_sep_source(store, template_vault, key="wheeler-sep-2")

    assert len(stored_source_url_keys(store, TOPIC)) == len(baseline) + 1


def test_a_schemeless_resource_frontmatter_keys_as_https(template_vault: Path) -> None:
    """A hand-edited ``resource:`` without a scheme still guards its source."""
    store = LocalFSStore(template_vault)
    _seed_raw_source(
        store,
        template_vault,
        "hand-edited.md",
        "---\nresource: plato.stanford.edu/entries/frame-problem/\n---\nBody.\n",
    )

    keys = stored_source_url_keys(store, TOPIC)

    assert "https://plato.stanford.edu/entries/frame-problem" in keys


def test_a_malformed_stored_source_is_skipped_never_fatal(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    _seed_raw_source(store, template_vault, "broken.md", "no frontmatter at all\n")
    _store_sep_source(store, template_vault)

    keys = stored_source_url_keys(store, TOPIC)

    assert "https://plato.stanford.edu/entries/bounded-rationality" in keys


# ---------------------------------------------------------------------------
# The drain -- discovery never re-proposes what an ingest already holds
# ---------------------------------------------------------------------------


def test_the_drain_skips_a_candidate_whose_source_the_vault_already_stores(
    template_vault: Path,
) -> None:
    """The field-report shape end to end: the vault holds the SEP entry, the
    provider returns an *archive edition* of it (broken-case segment included)
    plus a genuinely new source. Only the new source may stage, and the skip
    is counted, never silent."""
    store = LocalFSStore(template_vault)
    _store_sep_source(store, template_vault)
    with VaultTransaction(store, template_vault, "test_seed", TOPIC, "seed gaps") as txn:
        txn.write(
            f"{TOPIC}/.knotica/gaps/gaps.jsonl",
            _gap_record(gap_id="gap-sep").to_json_line() + "\n",
        )
    service = _FakeDiscoveryService(
        [_candidate(_SEP_EDITION), _candidate("https://example.com/novel-paper")]
    )

    result = gapfill.refresh_suggestions_for_gaps(store, template_vault, TOPIC, service=service)

    staged = store.read_text(gapfill.suggestions_path(TOPIC)).strip().splitlines()
    assert len(staged) == 1, "only the source the vault does not hold may stage"
    assert "novel-paper" in staged[0]
    assert result.candidates_already_in_vault == 1
    assert result.suggestions_written == 1


def test_a_drain_with_nothing_in_the_vault_counts_zero_skips(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    with VaultTransaction(store, template_vault, "test_seed", TOPIC, "seed gaps") as txn:
        txn.write(
            f"{TOPIC}/.knotica/gaps/gaps.jsonl",
            _gap_record(gap_id="gap-clean").to_json_line() + "\n",
        )
    service = _FakeDiscoveryService([_candidate("https://example.com/novel-paper")])

    result = gapfill.refresh_suggestions_for_gaps(store, template_vault, TOPIC, service=service)

    assert result.candidates_already_in_vault == 0
    assert result.suggestions_written == 1
