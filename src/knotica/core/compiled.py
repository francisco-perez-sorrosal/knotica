"""Compiled query artifacts — JSON instructions + demos under ``.knotica/compiled/``."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from knotica.store import VaultStore

__all__ = [
    "ARTIFACT_FILENAME",
    "COMPILED_SCHEMA_VERSION",
    "CompiledArtifact",
    "CompiledDemo",
    "compiled_dir",
    "compiled_artifact_path",
    "compiled_manifest_path",
    "load_compiled",
    "artifact_write_bodies",
    "format_compiled_demos",
    "format_compiled_program",
    "is_compiled_healthy",
]

_COMPILED_GUIDANCE = (
    "## Compiled few-shot demos\n"
    "\n"
    "Use these curated examples as style and factual anchors. Prefer exact "
    "numeric claims and citation keys that appear in the retrieved pages.\n"
)

COMPILED_SCHEMA_VERSION = 1
ARTIFACT_FILENAME = "query_v1.json"
_MANIFEST_FILENAME = "MANIFEST.json"
_KNOTICA = ".knotica"
_COMPILED = "compiled"


@dataclass(frozen=True, slots=True)
class CompiledDemo:
    """One few-shot demo distilled from curated train examples."""

    question: str
    answer: str
    citations: tuple[str, ...] = ()

    def render(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "answer": self.answer,
            "citations": list(self.citations),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompiledDemo:
        citations = data.get("citations") or []
        return cls(
            question=str(data.get("question") or ""),
            answer=str(data.get("answer") or ""),
            citations=tuple(str(c) for c in citations),
        )


@dataclass(frozen=True, slots=True)
class CompiledArtifact:
    """Portable compiled query program (no pickle)."""

    optimized_instructions: str
    demos: tuple[CompiledDemo, ...] = ()
    schema_version: int = COMPILED_SCHEMA_VERSION
    version: str = "query_v1"
    metrics: dict[str, float] = field(default_factory=dict)
    created_at: str = ""
    train_n: int = 0
    golden_n: int = 0
    harness_version: str = ""
    #: Which optimizer actually produced this artifact ("mipro" | "bootstrap");
    #: with ``fallback_reason`` set when MIPRO was attempted but fell back.
    optimizer: str = ""
    fallback_reason: str = ""

    def render(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "version": self.version,
            "optimized_instructions": self.optimized_instructions,
            "demos": [demo.render() for demo in self.demos],
            "metrics": dict(self.metrics),
            "created_at": self.created_at,
            "train_n": self.train_n,
            "golden_n": self.golden_n,
            "harness_version": self.harness_version,
            "optimizer": self.optimizer,
            "fallback_reason": self.fallback_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompiledArtifact:
        demos_raw = data.get("demos") or []
        demos = tuple(
            CompiledDemo.from_dict(item) if isinstance(item, dict) else CompiledDemo("", "")
            for item in demos_raw
        )
        metrics_raw = data.get("metrics") or {}
        metrics = {
            str(key): float(value)
            for key, value in metrics_raw.items()
            if isinstance(value, (int, float))
        }
        return cls(
            schema_version=int(data.get("schema_version") or COMPILED_SCHEMA_VERSION),
            version=str(data.get("version") or "query_v1"),
            optimized_instructions=str(data.get("optimized_instructions") or ""),
            demos=demos,
            metrics=metrics,
            created_at=str(data.get("created_at") or ""),
            train_n=int(data.get("train_n") or 0),
            golden_n=int(data.get("golden_n") or 0),
            harness_version=str(data.get("harness_version") or ""),
            optimizer=str(data.get("optimizer") or ""),
            fallback_reason=str(data.get("fallback_reason") or ""),
        )


def compiled_dir(topic: str) -> str:
    """Vault-relative directory for a topic's compiled artifacts."""
    return f"{topic.strip().strip('/')}/{_KNOTICA}/{_COMPILED}"


def compiled_artifact_path(topic: str) -> str:
    return f"{compiled_dir(topic)}/{ARTIFACT_FILENAME}"


def compiled_manifest_path(topic: str) -> str:
    return f"{compiled_dir(topic)}/{_MANIFEST_FILENAME}"


def format_compiled_demos(
    demos: tuple[CompiledDemo, ...] | list[CompiledDemo],
    *,
    limit: int = 8,
) -> str:
    """Render few-shot demos into the compiled program appendix."""
    blocks: list[str] = []
    for index, demo in enumerate(demos[:limit], start=1):
        cites = ", ".join(demo.citations) if demo.citations else "(none)"
        blocks.append(f"### Demo {index}\nQ: {demo.question}\nA: {demo.answer}\nCitations: {cites}")
    return "\n\n".join(blocks)


def format_compiled_program(artifact: CompiledArtifact, *, demo_limit: int = 8) -> str:
    """Compose the full runtime instruction substrate served by ``CompiledRunner``."""
    base = artifact.optimized_instructions.strip()
    demos_text = format_compiled_demos(artifact.demos, limit=demo_limit)
    if not demos_text:
        return base
    return f"{base}\n\n{_COMPILED_GUIDANCE}\n{demos_text}"


def is_compiled_healthy(artifact: CompiledArtifact | None) -> bool:
    """Whether ``artifact`` is safe to serve behind the query facade."""
    if artifact is None:
        return False
    if artifact.schema_version != COMPILED_SCHEMA_VERSION:
        return False
    return bool(artifact.optimized_instructions.strip())


def load_compiled(store: VaultStore, topic: str) -> CompiledArtifact | None:
    """Load a topic's compiled artifact, or ``None`` when absent/invalid."""
    path = compiled_artifact_path(topic)
    if not store.exists(path):
        return None
    try:
        data = json.loads(store.read_text(path))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    artifact = CompiledArtifact.from_dict(data)
    return artifact if is_compiled_healthy(artifact) else None


def artifact_write_bodies(artifact: CompiledArtifact) -> tuple[str, str]:
    """Return ``(artifact_json, manifest_json)`` bodies for transactional write."""
    created = artifact.created_at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = artifact.render()
    payload["created_at"] = created
    artifact_text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    manifest = {
        "schema_version": COMPILED_SCHEMA_VERSION,
        "version": artifact.version,
        "scalar_before": artifact.metrics.get("baseline"),
        "scalar_after": artifact.metrics.get("compiled"),
        "harness_version": artifact.harness_version,
        "train_n": artifact.train_n,
        "golden_n": artifact.golden_n,
        "created_at": created,
    }
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    return artifact_text, manifest_text
