"""``metrics.jsonl`` -- one eval-history record per scored generation.

The shape is frozen so the eval harness appends against a stable contract: one
scalar plus its four-way component breakdown, pinned to the corpus commit and
harness version that produced it.
"""

import json
from dataclasses import dataclass

from knotica.core.records.fields import (
    RecordParseError,
    _load_json_object,
    _optional_str,
    _required_int,
    _required_number,
    _required_str,
    _validate_schema_version,
)

__all__ = [
    "METRICS_SCHEMA_VERSION",
    "MetricsComponents",
    "MetricsRecord",
]

#: Current schema_version of the eval-history record.
METRICS_SCHEMA_VERSION = 1


@dataclass(frozen=True, kw_only=True)
class MetricsComponents:
    """The ``components`` breakdown of one eval scalar."""

    qa_accuracy: float
    citation_validity: float
    lint_violations: float
    token_cost: float


@dataclass(frozen=True, kw_only=True)
class MetricsRecord:
    """One eval-history record, a single ``metrics.jsonl`` line.

    The shape is frozen now so the eval harness appends against a stable
    contract; nothing in this codebase writes the file yet.
    """

    schema_version: int = METRICS_SCHEMA_VERSION
    topic: str
    timestamp: str
    generation: int
    harness_version: str
    scalar: float
    components: MetricsComponents
    n_examples: int
    corpus_ref: str
    artifact_ref: str | None

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        if self.generation < 0:
            raise ValueError(f"generation must be >= 0, got {self.generation}")
        if not self.corpus_ref.startswith("git:"):
            raise ValueError(f"corpus_ref must be a 'git:<sha>' reference, got {self.corpus_ref!r}")

    def to_json_line(self) -> str:
        """Serialize to one JSON line (no trailing newline), fields in schema order."""
        payload = {
            "schema_version": self.schema_version,
            "topic": self.topic,
            "timestamp": self.timestamp,
            "generation": self.generation,
            "harness_version": self.harness_version,
            "scalar": self.scalar,
            "components": {
                "qa_accuracy": self.components.qa_accuracy,
                "citation_validity": self.components.citation_validity,
                "lint_violations": self.components.lint_violations,
                "token_cost": self.components.token_cost,
            },
            "n_examples": self.n_examples,
            "corpus_ref": self.corpus_ref,
            "artifact_ref": self.artifact_ref,
        }
        return json.dumps(payload, ensure_ascii=False)

    @classmethod
    def from_json_line(cls, line: str) -> "MetricsRecord":
        """Parse one ``metrics.jsonl`` line; unknown extra fields are tolerated."""
        data = _load_json_object(line, record="metrics.jsonl")
        components = data.get("components")
        if not isinstance(components, dict):
            raise RecordParseError(
                f"metrics.jsonl record field 'components' must be an object, got {components!r}"
            )
        return cls(
            schema_version=_required_int(data, "schema_version", record="metrics.jsonl"),
            topic=_required_str(data, "topic", record="metrics.jsonl"),
            timestamp=_required_str(data, "timestamp", record="metrics.jsonl"),
            generation=_required_int(data, "generation", record="metrics.jsonl"),
            harness_version=_required_str(data, "harness_version", record="metrics.jsonl"),
            scalar=_required_number(data, "scalar", record="metrics.jsonl"),
            components=MetricsComponents(
                qa_accuracy=_required_number(components, "qa_accuracy", record="metrics.jsonl"),
                citation_validity=_required_number(
                    components, "citation_validity", record="metrics.jsonl"
                ),
                lint_violations=_required_number(
                    components, "lint_violations", record="metrics.jsonl"
                ),
                token_cost=_required_number(components, "token_cost", record="metrics.jsonl"),
            ),
            n_examples=_required_int(data, "n_examples", record="metrics.jsonl"),
            corpus_ref=_required_str(data, "corpus_ref", record="metrics.jsonl"),
            artifact_ref=_optional_str(data, "artifact_ref", record="metrics.jsonl"),
        )
