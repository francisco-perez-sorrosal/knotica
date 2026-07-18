"""Compiled query runner — same retrieve→synthesize path with optimized instructions."""

from __future__ import annotations

from knotica.core.compiled import CompiledArtifact, format_compiled_program
from knotica.evals.cache import ResponseCache
from knotica.evals.config import WORKER_SNAPSHOT
from knotica.evals.llm import LLMClient
from knotica.evals.runner import MessagesApiRunner

__all__ = ["CompiledRunner"]


class CompiledRunner(MessagesApiRunner):
    """BaselineRunner that injects compiled instructions + demos into synthesis."""

    def __init__(
        self,
        artifact: CompiledArtifact,
        llm_client: LLMClient,
        *,
        worker_snapshot: str = WORKER_SNAPSHOT,
        cache: ResponseCache | None = None,
    ) -> None:
        super().__init__(
            llm_client,
            worker_snapshot,
            cache=cache,
            instructions_override=format_compiled_program(artifact),
        )
        self._artifact = artifact
