"""``knotica gapfill discover`` CLI: on-demand drain trigger + live-demo entry.

Derived from the planned behavior contract -- never from the implementation. The subcommand builds the
real ``DiscoveryService`` via ``build_default_discovery_service()`` and calls
``refresh_suggestions_for_gaps``, printing a summary; with no API key it must
exit cleanly and write nothing (discovery degrades gracefully, never a hard
failure). Mirrors ``tests/test_cli_loop.py``'s ``main([...])`` harness.

RED-first: ``knotica.cli.gapfill`` does not exist yet when this file is
written (paired implementer step lands concurrently) -- production symbols
are resolved lazily inside test bodies so collection succeeds and the first
run fails with a dispatch/import error, not a collection error. Written
without reading the implementer's code.

Hermetic: ``isolated_home`` redirects HOME/XDG_CONFIG_HOME into ``tmp_path``
and clears KNOTICA_CONFIG, so a real ``~/.config/knotica/.env`` (or any real
provider key sitting in the actual environment) can never leak into a
"no key configured" assertion.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knotica.core.transaction import VaultTransaction

TOPIC = "agentic-systems"


def _gap_evidence(**overrides):
    from knotica.core.records import GapEvidence

    payload = {
        "quality_delta": -0.5,
        "qa_accuracy_delta": -0.5,
        "citation_validity_delta": 0.0,
        "retrieval_trace": (),
        "pages_added": (),
        "pages_removed": (),
        "prior_generation": 4,
    }
    payload.update(overrides)
    return GapEvidence(**payload)


def _gap_record(*, gap_id: str, qa_id: str, **overrides):
    from knotica.core.records import GapRecord

    payload: dict[str, object] = {
        "gap_id": gap_id,
        "topic": TOPIC,
        "qa_id": qa_id,
        "fault_class": "genuine_gap",
        "status": "open",
        "classifier_version": 1,
        "detected_generation": 5,
        "detected_at": "2026-07-18T23:01:00Z",
        "scalar_at_detection": 0.9493,
        "baseline_scalar": 0.96,
        "question": "What is the retrieval augmentation story for this topic?",
        "reference_pages": ("speculative-decoding",),
        "reference_pages_exist": False,
        "evidence": _gap_evidence(),
        "manifest_ref": "agentic-systems/.knotica/eval-runs/gen-5/manifest.json",
    }
    payload.update(overrides)
    return GapRecord(**payload)


def _seed_gaps(vault: Path, records) -> None:
    from knotica.core.gap_classifier import gaps_path
    from knotica.store import LocalFSStore

    store = LocalFSStore(vault)
    path = gaps_path(TOPIC)
    body = "\n".join(record.to_json_line() for record in records) + "\n"
    with VaultTransaction(store, vault, "test_seed", TOPIC, "seed gaps for test") as txn:
        txn.write(path, body)


def _candidate(**overrides):
    from knotica.discovery.records import SourceCandidate

    payload: dict[str, object] = {
        "url": "https://arxiv.org/abs/2302.01318",
        "title": "Accelerating LLM Inference with Speculative Decoding",
        "snippet": "We propose a novel decoding scheme...",
        "source_provider": "fake",
        "doi": None,
        "citation_count": 412,
    }
    payload.update(overrides)
    return SourceCandidate(**payload)


class _FakeDiscoveryService:
    def __init__(self, candidates) -> None:
        self._candidates = list(candidates)
        self.calls: list = []

    def discover(self, query):
        self.calls.append(query)
        return list(self._candidates)


# ---------------------------------------------------------------------------
# --help
# ---------------------------------------------------------------------------


def test_help_output_is_non_empty(capsys) -> None:
    from knotica.cli import main

    with pytest.raises(SystemExit) as excinfo:
        main(["gapfill", "discover", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert out.strip(), "gapfill discover --help must print usage, not exit silently"


# ---------------------------------------------------------------------------
# No API key -> clean no-op, exit 0, nothing written
# ---------------------------------------------------------------------------


def test_discover_with_no_api_key_writes_nothing_and_exits_cleanly(
    template_vault: Path,
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    del isolated_home  # HOME/.config redirected; no real .env can leak a key in
    from knotica.cli import main
    from knotica.core.gapfill import suggestions_path
    from knotica.store import LocalFSStore

    # Hermetic double-guard: even though isolated_home already redirects HOME,
    # also chdir away from the repo root so a stray ./.env can never be picked
    # up by any future cwd-relative fallback -- we got bitten by exactly this
    # before (a real provider key leaking into a "no key" assertion).
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KNOTICA_YOUCOM_API_KEY", raising=False)
    _seed_gaps(template_vault, [_gap_record(gap_id="gap-nokey", qa_id="golden-nokey")])

    exit_code = main(["gapfill", "discover", "--topic", TOPIC, "--vault", str(template_vault)])

    assert exit_code == 0
    store = LocalFSStore(template_vault)
    assert not store.exists(suggestions_path(TOPIC)), (
        "no key configured must be a clean no-op -- nothing written"
    )
    captured = capsys.readouterr()
    assert captured.out or captured.err, "a no-op run must still print a summary somewhere"


# ---------------------------------------------------------------------------
# Fake service -> stages the expected count, summarized
# ---------------------------------------------------------------------------


def test_discover_with_a_configured_service_stages_the_expected_count(
    template_vault: Path,
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    del isolated_home
    from knotica.cli import main
    from knotica.core.gapfill import suggestions_path
    from knotica.store import LocalFSStore

    fake_service = _FakeDiscoveryService([_candidate(url="https://a.example", doi=None)])
    monkeypatch.setattr(
        "knotica.core.gapfill.build_default_discovery_service",
        lambda **_kwargs: fake_service,
    )
    _seed_gaps(template_vault, [_gap_record(gap_id="gap-staged", qa_id="golden-staged")])

    exit_code = main(["gapfill", "discover", "--topic", TOPIC, "--vault", str(template_vault)])

    assert exit_code == 0
    store = LocalFSStore(template_vault)
    persisted = store.read_text(suggestions_path(TOPIC)).strip().splitlines()
    assert len(persisted) == 1, (
        "one open genuine_gap x one ranked candidate = one staged suggestion"
    )
    out = capsys.readouterr().out
    assert "1" in out, "the summary must name the staged count"
