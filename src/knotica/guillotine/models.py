"""Data models for Memory Guillotine reports and internal pipeline state."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

RiskLevel = Literal["none", "low", "medium", "high"]
Strength = Literal["weak", "medium", "strong"]
Modality = Literal["unqualified", "qualified", "attributed", "quoted", "negated", "uncertain"]
SuggestedAction = Literal["keep", "qualify", "demote", "dispute", "retract", "delete", "ignore"]
PatchAction = Literal["replace", "insert", "annotate", "remove"]


class PassageRole(StrEnum):
    """How a passage relates to the target claim."""

    ASSERTS = "ASSERTS"
    QUALIFIES = "QUALIFIES"
    CONTRADICTS = "CONTRADICTS"
    REFUTES = "REFUTES"
    QUOTES = "QUOTES"
    MENTIONS = "MENTIONS"
    DEPENDS_ON = "DEPENDS_ON"
    IRRELEVANT = "IRRELEVANT"


class Verdict(StrEnum):
    """Recommended or user-overridden guillotine action."""

    KEEP = "KEEP"
    QUALIFY = "QUALIFY"
    DEMOTE = "DEMOTE"
    DISPUTE = "DISPUTE"
    RETRACT = "RETRACT"
    QUARANTINE_SOURCE = "QUARANTINE_SOURCE"
    DELETE_UNSUPPORTED_SYNTHESIS = "DELETE_UNSUPPORTED_SYNTHESIS"


@dataclass(frozen=True, slots=True)
class Passage:
    """One classified mention of the target claim."""

    path: str
    line_start: int
    line_end: int
    text: str
    role: PassageRole
    strength: Strength
    modality: Modality
    risk: RiskLevel
    reason: str
    suggested_action: SuggestedAction
    is_source: bool = False


@dataclass(frozen=True, slots=True)
class ScoreFactor:
    """One applied adjustment in the overall risk score calculation."""

    delta: int
    description: str


@dataclass(frozen=True, slots=True)
class VerdictThreshold:
    """One verdict band on the 0–100 risk scale."""

    max_score: int
    verdict: Verdict
    label: str


#: Verdict bands (inclusive upper bound). Used in reports and ``_verdict_from_score``.
VERDICT_THRESHOLDS: tuple[VerdictThreshold, ...] = (
    VerdictThreshold(25, Verdict.KEEP, "0–25"),
    VerdictThreshold(45, Verdict.QUALIFY, "26–45"),
    VerdictThreshold(65, Verdict.DISPUTE, "46–65"),
    VerdictThreshold(80, Verdict.RETRACT, "66–80"),
    VerdictThreshold(100, Verdict.DELETE_UNSUPPORTED_SYNTHESIS, "81–100"),
)


@dataclass(frozen=True, slots=True)
class EvidenceGraph:
    """Compact evidence summary for the report."""

    supporting_sources: tuple[str, ...] = ()
    contradicting_sources: tuple[str, ...] = ()
    qualified_sources: tuple[str, ...] = ()
    interested_sources: tuple[str, ...] = ()
    uncited_mentions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Patch:
    """One proposed file edit."""

    path: str
    action: PatchAction
    line_start: int
    line_end: int
    before: str
    after: str
    rationale: str


@dataclass(frozen=True, slots=True)
class ArtifactPaths:
    """Paths of generated guillotine artifacts (vault-relative)."""

    report_path: str
    diff_path: str
    json_path: str


@dataclass(frozen=True, slots=True)
class GuillotineReport:
    """Full guillotine trial result."""

    claim: str
    normalized_claim: str
    topic: str
    recommendation: Verdict
    risk_score: int
    summary: str
    passages: tuple[Passage, ...]
    evidence: EvidenceGraph
    patches: tuple[Patch, ...]
    artifacts: ArtifactPaths
    applied: bool = False
    commit_sha: str | None = None
    status: Literal["dry-run", "applied"] = "dry-run"


@dataclass
class GuillotineResult:
    """Internal pipeline accumulator before artifact write."""

    claim: str
    normalized_claim: str
    topic: str
    recommendation: Verdict
    risk_score: int
    summary: str
    passages: list[Passage] = field(default_factory=list)
    evidence: EvidenceGraph = field(default_factory=EvidenceGraph)
    patches: list[Patch] = field(default_factory=list)
    score_factors: list[ScoreFactor] = field(default_factory=list)
    risk_score_raw: int = 0
    score_breakdown: list[str] = field(default_factory=list)
