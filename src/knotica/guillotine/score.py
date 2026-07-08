"""Transparent risk scoring and verdict recommendation."""

from __future__ import annotations

from knotica.guillotine.models import (
    EvidenceGraph,
    GuillotineResult,
    Passage,
    PassageRole,
    ScoreFactor,
    Verdict,
    VerdictThreshold,
    VERDICT_THRESHOLDS,
)

_INDEX_LIKE = ("index.md", "overview", "synthesis", "summary")


def build_evidence_graph(passages: list[Passage]) -> EvidenceGraph:
    """Derive a compact evidence summary from classified passages."""
    supporting: list[str] = []
    contradicting: list[str] = []
    qualified: list[str] = []
    interested: list[str] = []
    uncited: list[str] = []

    for passage in passages:
        if passage.is_source:
            if passage.role == PassageRole.QUOTES:
                supporting.append(passage.path)
            if "vendor" in passage.text.lower():
                interested.append(passage.path)
            continue
        if passage.role == PassageRole.REFUTES:
            contradicting.append(passage.path)
        elif passage.role == PassageRole.CONTRADICTS:
            contradicting.append(passage.path)
        elif passage.role == PassageRole.QUALIFIES:
            qualified.append(passage.path)
        elif passage.role == PassageRole.ASSERTS:
            uncited.append(passage.path)
            if "vendor" in passage.text.lower():
                interested.append(passage.path)

    return EvidenceGraph(
        supporting_sources=tuple(dict.fromkeys(supporting)),
        contradicting_sources=tuple(dict.fromkeys(contradicting)),
        qualified_sources=tuple(dict.fromkeys(qualified)),
        interested_sources=tuple(dict.fromkeys(interested)),
        uncited_mentions=tuple(dict.fromkeys(uncited)),
    )


def score_and_recommend(
    result: GuillotineResult, passages: list[Passage], evidence: EvidenceGraph
) -> None:
    """Populate risk score, factors, summary, and recommendation on ``result``."""
    score = 0
    factors: list[ScoreFactor] = []

    assertions = [p for p in passages if p.role == PassageRole.ASSERTS and not p.is_source]
    refutations = [p for p in passages if p.role in {PassageRole.REFUTES, PassageRole.CONTRADICTS}]
    quotes = [p for p in passages if p.role == PassageRole.QUOTES]

    if assertions and any(p.modality == "unqualified" for p in assertions):
        score, factors = _add(score, factors, 25, "Universal / unqualified assertion wording")
    if len(evidence.supporting_sources) <= 1 and assertions:
        score, factors = _add(score, factors, 20, "Only one (or zero) supporting sources")
    if evidence.interested_sources:
        score, factors = _add(score, factors, 20, "Vendor- or interest-aligned source signal")
    if assertions and any(p.strength == "strong" for p in assertions):
        score, factors = _add(score, factors, 15, "High-strength assertion wording")
    if any(_is_central_page(p.path) for p in assertions):
        score, factors = _add(score, factors, 15, "Appears on index/overview/synthesis-like page")
    if assertions and not refutations:
        score, factors = _add(score, factors, 10, "No counterevidence in scope")
    if assertions and _affects_user_agency(assertions):
        score, factors = _add(score, factors, 10, "Affects user agency / safety framing")
    if len(assertions) >= 2:
        score, factors = _add(score, factors, 10, "Claim appears in multiple synthesized pages")
    if evidence.uncited_mentions:
        score, factors = _add(score, factors, 10, "Synthesized assertions lack clear citations")

    if any(p.modality in {"attributed", "qualified"} for p in passages):
        score, factors = _add(score, factors, -15, "Explicitly attributed or qualified wording")
    if any("disputed" in p.text.lower() for p in passages):
        score, factors = _add(score, factors, -20, "Already marked disputed in the wiki")
    if quotes or refutations:
        score, factors = _add(score, factors, -20, "Substantial quote or refutation presence")
    if len(evidence.supporting_sources) >= 2:
        score, factors = _add(score, factors, -10, "Multiple independent supporting sources")

    raw_score = score
    final_score = max(0, min(100, score))
    verdict = _verdict_from_score(final_score, assertions, refutations)
    result.risk_score_raw = raw_score
    result.risk_score = final_score
    result.score_factors = factors
    result.score_breakdown = [f"{factor.delta:+d} {factor.description}" for factor in factors]
    result.recommendation = verdict
    result.summary = _build_summary(assertions, refutations, quotes, verdict)


def verdict_threshold_for(score: int) -> VerdictThreshold:
    """Return the verdict band that contains ``score``."""
    for threshold in VERDICT_THRESHOLDS:
        if score <= threshold.max_score:
            return threshold
    return VERDICT_THRESHOLDS[-1]


def _add(
    score: int, factors: list[ScoreFactor], delta: int, description: str
) -> tuple[int, list[ScoreFactor]]:
    factors.append(ScoreFactor(delta=delta, description=description))
    return score + delta, factors


def _verdict_from_score(
    score: int, assertions: list[Passage], refutations: list[Passage]
) -> Verdict:
    if not assertions and not refutations:
        return Verdict.KEEP
    threshold = verdict_threshold_for(score)
    if threshold.verdict == Verdict.DISPUTE and not refutations:
        return Verdict.DEMOTE
    return threshold.verdict


def _is_central_page(path: str) -> bool:
    lowered = path.lower()
    return any(marker in lowered for marker in _INDEX_LIKE)


def _affects_user_agency(assertions: list[Passage]) -> bool:
    terms = ("unsafe", "security", "privacy", "serious users", "fail", "hallucinate", "risk")
    return any(any(term in passage.text.lower() for term in terms) for passage in assertions)


def _build_summary(
    assertions: list[Passage],
    refutations: list[Passage],
    quotes: list[Passage],
    verdict: Verdict,
) -> str:
    if not assertions and not refutations:
        return "No synthesized assertions of the target claim were found in scope."
    parts = [
        f"The claim appears as an unqualified assertion in {len(assertions)} synthesized passage(s)",
        f"with {len(refutations)} refutation(s) or contradiction(s)",
        f"and {len(quotes)} quote(s).",
    ]
    parts.append(f"Recommended verdict: {verdict.value}.")
    return " ".join(parts)
