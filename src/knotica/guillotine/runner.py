"""Orchestrate a full Memory Guillotine trial (read-only pipeline)."""

from __future__ import annotations

from pathlib import Path

from knotica.guillotine.classify import classify_passages
from knotica.guillotine.models import GuillotineResult, Verdict
from knotica.guillotine.patch import propose_patches, render_diff
from knotica.guillotine.score import build_evidence_graph, score_and_recommend
from knotica.guillotine.search import (
    extract_context_windows,
    find_candidate_mentions,
    normalize_claim,
    resolve_search_scope,
)
from knotica.store import VaultStore

_VERDICT_ALIASES = {
    "keep": Verdict.KEEP,
    "qualify": Verdict.QUALIFY,
    "demote": Verdict.DEMOTE,
    "dispute": Verdict.DISPUTE,
    "retract": Verdict.RETRACT,
    "quarantine_source": Verdict.QUARANTINE_SOURCE,
    "delete_unsupported_synthesis": Verdict.DELETE_UNSUPPORTED_SYNTHESIS,
}


class ClaimNotFoundError(LookupError):
    """Raised when no candidate mentions match the claim in scope."""


class PatchGenerationError(RuntimeError):
    """Raised when patch generation fails."""


def parse_verdict_override(value: str | None) -> Verdict | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized not in _VERDICT_ALIASES:
        allowed = ", ".join(sorted(_VERDICT_ALIASES))
        raise ValueError(f"Invalid verdict {value!r}; expected one of: {allowed}")
    return _VERDICT_ALIASES[normalized]


def run_guillotine(
    store: VaultStore,
    vault_root: str | Path,
    claim: str,
    *,
    topic: str,
    verdict: str | None = None,
    include_sources: bool = True,
    include_reports: bool = False,
    max_results: int = 50,
) -> tuple[GuillotineResult, str]:
    """Execute the guillotine pipeline and return the trial result plus diff text."""
    root = Path(vault_root).resolve()
    cleaned_claim = claim.strip()
    if not cleaned_claim:
        raise ValueError("claim must not be empty")
    if not topic:
        raise ValueError("topic is required")

    try:
        scan_dirs = resolve_search_scope(
            root, topic, include_sources=include_sources, include_reports=include_reports
        )
    except FileNotFoundError as error:
        raise ClaimNotFoundError(str(error)) from error
    hits = find_candidate_mentions(
        cleaned_claim,
        root,
        scan_dirs,
        max_results=max_results,
        include_reports=include_reports,
    )
    if not hits:
        raise ClaimNotFoundError(f"No mentions of the claim found in topic '{topic}'.")

    windows = extract_context_windows(hits, root)
    passages = classify_passages(cleaned_claim, windows)
    passages = _filter_relevant_passages(cleaned_claim, passages)
    if not passages:
        raise ClaimNotFoundError(f"No relevant mentions of the claim found in topic '{topic}'.")
    evidence = build_evidence_graph(passages)

    result = GuillotineResult(
        claim=cleaned_claim,
        normalized_claim=normalize_claim(cleaned_claim),
        topic=topic,
        recommendation=Verdict.KEEP,
        risk_score=0,
        summary="",
        passages=passages,
        evidence=evidence,
    )
    score_and_recommend(result, passages, evidence)

    override = parse_verdict_override(verdict)
    if override is not None:
        result.recommendation = override

    file_contents = _load_file_contents(store, passages)
    patches = propose_patches(cleaned_claim, passages, result.recommendation, file_contents)
    if (
        result.recommendation != Verdict.KEEP
        and not patches
        and _has_actionable_assertions(passages)
    ):
        raise PatchGenerationError("Patch generation produced no edits for actionable assertions.")
    result.patches = patches
    diff_text = render_diff(patches, file_contents)
    return result, diff_text


def _load_file_contents(store: VaultStore, passages: list) -> dict[str, str]:
    paths = sorted({passage.path for passage in passages})
    return {path: store.read_text(path) for path in paths}


def _has_actionable_assertions(passages: list) -> bool:
    from knotica.guillotine.models import PassageRole

    return any(
        passage.role in {PassageRole.ASSERTS, PassageRole.DEPENDS_ON} and not passage.is_source
        for passage in passages
    )


def _filter_relevant_passages(claim: str, passages: list) -> list:
    """Drop weak lexical overlaps that are not about the target claim."""
    from knotica.guillotine.models import Passage, PassageRole

    kept: list[Passage] = []
    for passage in passages:
        if passage.role != PassageRole.IRRELEVANT:
            kept.append(passage)
            continue
        if _claim_overlap(claim, passage.text) >= 0.5:
            kept.append(passage)
    return kept


def _claim_overlap(claim: str, text: str) -> float:
    claim_tokens = {token for token in normalize_claim(claim).split() if len(token) > 2}
    text_tokens = {token for token in normalize_claim(text).split() if len(token) > 2}
    if not claim_tokens:
        return 0.0
    return len(claim_tokens & text_tokens) / len(claim_tokens)
