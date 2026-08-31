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


# ---------------------------------------------------------------------------
# Queue healing -- every drain converges the existing queue, no manual sweeps
# ---------------------------------------------------------------------------


def _staged_suggestion(suggestion_id: str, url: str, *, status: str, rank: int = 1):
    from knotica.core.records import SuggestionRecord

    return SuggestionRecord(
        suggestion_id=suggestion_id,
        topic=TOPIC,
        gap_id="gap-editions",
        qa_id="golden-gap-editions",
        fault_class="genuine_gap",
        question="What is bounded rationality?",
        reference_pages=(),
        rank=rank,
        query_text="What is bounded rationality?",
        candidate={"url": url, "title": "Bounded Rationality"},
        status=status,
        proposed_at=f"2026-08-0{rank}T00:00:00Z",
        decided_at=None,
        decided_reason=None,
        ingested_at=None,
        detected_generation=5,
        gap_origin="measured",
    )


def _seed_queue(store: LocalFSStore, vault: Path, records) -> None:
    with VaultTransaction(store, vault, "test_seed", TOPIC, "seed suggestions") as txn:
        txn.write(
            gapfill.suggestions_path(TOPIC),
            "".join(r.to_json_line() + "\n" for r in records),
        )


def test_a_drain_collapses_per_gap_editions_of_one_source_keeping_the_human_decision(
    template_vault: Path,
) -> None:
    """The field report's queue: many approved archive editions of one entry.
    The canonical identity sees them as one source; each drain keeps a single
    winner (a human decision outranks the undecided, then the better rank) and
    closes the rest naming the winner."""
    store = LocalFSStore(template_vault)
    with VaultTransaction(store, template_vault, "test_seed", TOPIC, "seed gaps") as txn:
        txn.write(
            f"{TOPIC}/.knotica/gaps/gaps.jsonl",
            _gap_record(gap_id="gap-editions").to_json_line() + "\n",
        )
    base = "https://plato.stanford.edu"
    _seed_queue(
        store,
        template_vault,
        [
            _staged_suggestion(
                "sug-2018",
                f"{base}/archives/win2018/entries/bounded-rationality/",
                status="pending",
                rank=1,
            ),
            _staged_suggestion(
                "sug-2024",
                f"{base}/archIves/win2024/entries/bounded-rationality/",
                status="approved",
                rank=3,
            ),
            _staged_suggestion(
                "sug-live", f"{base}/entries/bounded-rationality/", status="pending", rank=2
            ),
        ],
    )

    result = gapfill.refresh_suggestions_for_gaps(
        store, template_vault, TOPIC, service=_FakeDiscoveryService([])
    )

    from knotica.core.records import parse_suggestions_jsonl

    parsed = {
        r.suggestion_id: r
        for r in parse_suggestions_jsonl(store.read_text(gapfill.suggestions_path(TOPIC)))
    }
    assert parsed["sug-2024"].status == "approved", "the human-approved edition survives"
    assert parsed["sug-2018"].status == "rejected"
    assert parsed["sug-live"].status == "rejected"
    assert "duplicate of sug-2024" in (parsed["sug-2018"].decided_reason or "")
    assert result.stale_suggestions_closed == 2
    assert parsed["sug-2024"].candidate["url"] == _SEP_CANONICAL, (
        "the survivor's STORED url must be canonicalized too -- keeping the broken-case "
        "archIves edition while closing the correct living entry as its duplicate hands "
        "the operator a URL SEP serves 404 for"
    )


def test_a_drain_heals_a_topic_with_no_open_gaps_and_no_provider(template_vault: Path) -> None:
    """Healing is local work: it needs neither a discovery provider nor an open
    gap. The state it exists to converge -- stale approvals left behind after
    the last gap resolved -- is exactly the state with no open gap in it."""
    store = LocalFSStore(template_vault)
    _store_sep_source(store, template_vault)
    _seed_queue(
        store, template_vault, [_staged_suggestion("sug-orphan", _SEP_EDITION, status="approved")]
    )

    result = gapfill.refresh_suggestions_for_gaps(store, template_vault, TOPIC, service=None)

    from knotica.core.records import parse_suggestions_jsonl

    parsed = parse_suggestions_jsonl(store.read_text(gapfill.suggestions_path(TOPIC)))
    assert parsed[0].status == "rejected"
    assert result.stale_suggestions_closed == 1
    assert result.service_available is False


def test_a_drain_reports_which_gap_the_vault_already_answers(template_vault: Path) -> None:
    """A gap whose whole candidate yield is already stored can never resolve
    (only a merged source or a dismissal closes one) yet costs a search every
    drain. The topic-level counter cannot say WHICH gap; this names it."""
    store = LocalFSStore(template_vault)
    _store_sep_source(store, template_vault)
    with VaultTransaction(store, template_vault, "test_seed", TOPIC, "seed gaps") as txn:
        txn.write(
            f"{TOPIC}/.knotica/gaps/gaps.jsonl",
            _gap_record(gap_id="gap-inert").to_json_line() + "\n",
        )

    result = gapfill.refresh_suggestions_for_gaps(
        store, template_vault, TOPIC, service=_FakeDiscoveryService([_candidate(_SEP_EDITION)])
    )

    assert result.candidates_already_in_vault == 1
    assert result.gaps_fully_in_vault == ("gap-inert",)


def test_a_drain_closes_open_records_whose_source_the_vault_now_stores(
    template_vault: Path,
) -> None:
    store = LocalFSStore(template_vault)
    _store_sep_source(store, template_vault)
    with VaultTransaction(store, template_vault, "test_seed", TOPIC, "seed gaps") as txn:
        txn.write(
            f"{TOPIC}/.knotica/gaps/gaps.jsonl",
            _gap_record(gap_id="gap-editions").to_json_line() + "\n",
        )
    _seed_queue(
        store,
        template_vault,
        [
            _staged_suggestion("sug-stale", _SEP_EDITION, status="approved"),
            _staged_suggestion(
                "sug-other", "https://example.com/unrelated", status="pending", rank=2
            ),
        ],
    )

    result = gapfill.refresh_suggestions_for_gaps(
        store, template_vault, TOPIC, service=_FakeDiscoveryService([])
    )

    from knotica.core.records import parse_suggestions_jsonl

    parsed = {
        r.suggestion_id: r
        for r in parse_suggestions_jsonl(store.read_text(gapfill.suggestions_path(TOPIC)))
    }
    assert parsed["sug-stale"].status == "rejected"
    assert parsed["sug-stale"].decided_reason == "source already stored in the vault"
    assert parsed["sug-other"].status == "pending", "an unrelated open record is untouched"
    assert result.stale_suggestions_closed == 1


def test_healing_never_touches_a_terminal_record(template_vault: Path) -> None:
    """`ingested` is history and `rejected` already carries a decision; the
    healing pass may close only what is still waiting on a human."""
    store = LocalFSStore(template_vault)
    _store_sep_source(store, template_vault)
    with VaultTransaction(store, template_vault, "test_seed", TOPIC, "seed gaps") as txn:
        txn.write(
            f"{TOPIC}/.knotica/gaps/gaps.jsonl",
            _gap_record(gap_id="gap-editions").to_json_line() + "\n",
        )
    ingested = _staged_suggestion("sug-history", _SEP_CANONICAL, status="ingested")
    _seed_queue(store, template_vault, [ingested])

    result = gapfill.refresh_suggestions_for_gaps(
        store, template_vault, TOPIC, service=_FakeDiscoveryService([])
    )

    from knotica.core.records import parse_suggestions_jsonl

    parsed = parse_suggestions_jsonl(store.read_text(gapfill.suggestions_path(TOPIC)))
    assert parsed[0].status == "ingested"
    assert result.stale_suggestions_closed == 0
