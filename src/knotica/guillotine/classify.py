"""Deterministic passage classification for guillotine trials."""

from __future__ import annotations

import re

from knotica.guillotine.models import Passage, PassageRole, SuggestedAction
from knotica.guillotine.search import CandidateHit, normalize_claim

_REFUTE_MARKERS = (
    "the claim that",
    "is false",
    "is unsupported",
    "too broad",
    "misleading",
    "overgeneral",
    "does not prove",
    "not prove",
    "contradict",
    "refute",
    "dispute",
    "however",
    "but this",
)

_QUALIFY_MARKERS = (
    "some argue",
    "some vendors",
    "some teams",
    "according to",
    "vendor claims",
    "may ",
    "might ",
    "can ",
    "often ",
    "in some",
    "suggests",
    "report claims",
    "argues that",
    "debate whether",
    "whether ",
)

_NEGATION_MARKERS = ("not ", "never ", "no longer ", "cannot ", "can't ", "don't ")

_USER_HARM_TERMS = frozenset(
    {
        "unsafe",
        "security",
        "privacy",
        "health",
        "legal",
        "finance",
        "identity",
        "dangerous",
        "harm",
        "risk",
    }
)


def classify_passages(claim: str, hits: list[CandidateHit]) -> list[Passage]:
    """Classify each candidate passage using deterministic heuristics."""
    normalized = normalize_claim(claim)
    return [_classify_one(claim, normalized, hit) for hit in hits]


def _classify_one(claim: str, normalized_claim: str, hit: CandidateHit) -> Passage:
    text_lower = hit.text.lower()
    claim_in_text = normalized_claim in normalize_claim(hit.text)

    if hit.is_source:
        role = PassageRole.QUOTES if _looks_quoted(hit.text, claim) else PassageRole.MENTIONS
        return _build_passage(hit, role, claim_in_text)

    if _looks_quoted(hit.text, claim):
        role = PassageRole.QUOTES
    elif any(marker in text_lower for marker in _REFUTE_MARKERS):
        role = PassageRole.REFUTES
    elif any(marker in text_lower for marker in _NEGATION_MARKERS) and claim_in_text:
        role = PassageRole.CONTRADICTS
    elif any(marker in text_lower for marker in _QUALIFY_MARKERS):
        role = PassageRole.QUALIFIES if claim_in_text else PassageRole.MENTIONS
    elif claim_in_text and _looks_assertion(hit.text, claim):
        role = PassageRole.ASSERTS
    elif claim_in_text:
        role = PassageRole.ASSERTS if _looks_assertion(hit.text, claim) else PassageRole.MENTIONS
    else:
        role = PassageRole.IRRELEVANT

    return _build_passage(hit, role, claim_in_text)


def _build_passage(hit: CandidateHit, role: PassageRole, claim_in_text: bool) -> Passage:
    modality = _infer_modality(hit.text, role)
    strength = _infer_strength(hit.text, role)
    risk = _infer_risk(role, hit.text, hit.is_source)
    suggested = _suggest_action(role, hit.is_source)
    reason = _reason_for_role(role, hit.is_source, claim_in_text)
    return Passage(
        path=hit.path,
        line_start=hit.line_start,
        line_end=hit.line_end,
        text=hit.text,
        role=role,
        strength=strength,
        modality=modality,
        risk=risk,
        reason=reason,
        suggested_action=suggested,
        is_source=hit.is_source,
    )


def _looks_quoted(text: str, claim: str) -> bool:
    lowered = text.lower()
    if "report claims" in lowered or "paper claims" in lowered or "vendor claims" in lowered:
        return True
    # Double-quoted or full-sentence single-quoted spans (not possessives like model's).
    if re.search(r'"[^"]{8,}"', text):
        return True
    if re.search(r"(?<!\w)'[^']{8,}'(?!\w)", text):
        return True
    return False


def _looks_assertion(text: str, claim: str) -> bool:
    lowered = text.lower()
    if any(marker in lowered for marker in _QUALIFY_MARKERS):
        return False
    if any(marker in lowered for marker in _REFUTE_MARKERS):
        return False
    # Categorical verbs without hedging.
    categorical = ("are ", "is ", "will ", "must ", "always ", "never ", "fail ", "hallucinate")
    return any(marker in lowered for marker in categorical)


def _infer_modality(text: str, role: PassageRole) -> str:
    lowered = text.lower()
    if role == PassageRole.QUOTES:
        return "quoted"
    if role in {PassageRole.REFUTES, PassageRole.CONTRADICTS}:
        return "negated"
    if any(marker in lowered for marker in _QUALIFY_MARKERS):
        return "qualified"
    if "according to" in lowered or "vendor" in lowered:
        return "attributed"
    if role == PassageRole.ASSERTS:
        return "unqualified"
    return "uncertain"


def _infer_strength(text: str, role: PassageRole) -> str:
    if role in {PassageRole.REFUTES, PassageRole.CONTRADICTS, PassageRole.IRRELEVANT}:
        return "weak"
    lowered = text.lower()
    if any(word in lowered for word in ("always", "never", "inherently", "must", "prove")):
        return "strong"
    if role == PassageRole.ASSERTS:
        return "strong"
    return "medium"


def _infer_risk(role: PassageRole, text: str, is_source: bool) -> str:
    if is_source or role in {PassageRole.REFUTES, PassageRole.CONTRADICTS, PassageRole.IRRELEVANT}:
        return "none" if role != PassageRole.IRRELEVANT else "low"
    if role == PassageRole.QUOTES:
        return "low"
    if role == PassageRole.MENTIONS:
        return "low"
    if role == PassageRole.QUALIFIES:
        return "medium"
    if role == PassageRole.ASSERTS:
        tokens = set(re.split(r"[^a-z]+", text.lower()))
        if tokens & _USER_HARM_TERMS:
            return "high"
        return "high"
    return "medium"


def _suggest_action(role: PassageRole, is_source: bool) -> SuggestedAction:
    if is_source:
        return "keep"
    return {
        PassageRole.ASSERTS: "retract",
        PassageRole.QUALIFIES: "keep",
        PassageRole.CONTRADICTS: "keep",
        PassageRole.REFUTES: "keep",
        PassageRole.QUOTES: "keep",
        PassageRole.MENTIONS: "keep",
        PassageRole.DEPENDS_ON: "qualify",
        PassageRole.IRRELEVANT: "ignore",
    }[role]


def _reason_for_role(role: PassageRole, is_source: bool, claim_in_text: bool) -> str:
    if is_source:
        return "Raw source passage — preserved for audit; not a synthesis edit target."
    reasons = {
        PassageRole.ASSERTS: (
            "The passage states the target claim as fact without sufficient attribution or caveat."
        ),
        PassageRole.QUALIFIES: "The passage hedges or attributes the claim.",
        PassageRole.CONTRADICTS: "The passage asserts something incompatible with the target claim.",
        PassageRole.REFUTES: "The passage explicitly argues the claim is wrong, unsupported, or too broad.",
        PassageRole.QUOTES: "The passage quotes the claim without endorsing it.",
        PassageRole.MENTIONS: "The passage mentions the claim without clearly asserting or refuting it.",
        PassageRole.DEPENDS_ON: "The passage appears to rely on the claim as a premise.",
        PassageRole.IRRELEVANT: "Overlapping terms but not clearly about the target claim.",
    }
    base = reasons[role]
    if not claim_in_text and role != PassageRole.IRRELEVANT:
        return f"{base} (partial / paraphrased match)."
    return base
