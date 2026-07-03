"""Operation-prompt resolution -- the one shared resolver behind both surfaces.

Prompt resolution mirrors schema resolution (``core.schema``): root defaults
at ``.knotica/prompts/<op>.md``, per-topic override at
``<topic>/.knotica/prompts/<op>.md`` once divergence is earned. Built once
here and consumed by both the MCP prompt handler (``prompts/get``) and the
``knotica prompt`` CLI subcommand, so the two surfaces serve byte-identical
bodies -- the vault ``prompts/`` files are simultaneously the UX surface and
the self-improvement substrate.

Pure per-call functions over the :class:`~knotica.store.VaultStore` protocol
-- nothing is cached, deliberately (same rationale as ``core.config``: a
prompt evolved after boot must be served on the next invocation; an
mtime-cache is the sanctioned later optimization, not MVP).

Three outcomes, keeping the unconfigured / malformed-vault distinction:

* **Configured** -- the topic override body when present, else the root
  default body.
* **Unconfigured** (no config file / no resolvable vault) -- :func:`get_prompt`
  returns the setup-guidance body instead of failing: the prompt surface must
  degrade gracefully, and this resolver owns that guard so the prompt bodies
  themselves can assume a configured vault.
* **Malformed vault** (vault resolves READY, but the root default file is
  missing) -- a typed ``NOT_CONFIGURED`` error is raised: the vault's prompt
  defaults are broken, which adapters must surface as a failure, never paper
  over with a silent fallback body.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from knotica.core import config
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.schema import validated_topic
from knotica.store import LocalFSStore, VaultStore

__all__ = [
    "OPERATIONS",
    "PROMPTS_DIR",
    "ResolvedPrompt",
    "get_prompt",
    "override_prompt_path",
    "resolve_prompt",
    "root_prompt_path",
]

#: The four operation prompts (static names; bodies resolve lazily per call).
OPERATIONS: tuple[str, ...] = ("ingest", "query", "lint", "curate")

#: Prompts directory, relative to the vault root (defaults) and to a topic
#: directory (overrides) -- the mirror that keeps both resolutions one shape.
PROMPTS_DIR = ".knotica/prompts"


@dataclass(frozen=True)
class ResolvedPrompt:
    """One resolved prompt body, with its provenance.

    ``source_path`` is the vault-relative file the body came from, or ``None``
    for the setup-guidance body (unconfigured). ``topic`` is the requested
    topic as given (stripped) -- adapters pass it through to the model's
    context; the body's topic-inference policy does the actual resolution.
    """

    operation: str
    topic: str
    body: str
    source_path: str | None
    topic_override: bool

    @property
    def configured(self) -> bool:
        """Whether the body came from the vault (``False`` = setup guidance)."""
        return self.source_path is not None


def root_prompt_path(operation: str) -> str:
    """Vault-relative path of ``operation``'s root default prompt body."""
    return f"{PROMPTS_DIR}/{_validated_operation(operation)}.md"


def override_prompt_path(operation: str, topic: str) -> str:
    """Vault-relative path of ``topic``'s override for ``operation``."""
    return f"{validated_topic(topic)}/{PROMPTS_DIR}/{_validated_operation(operation)}.md"


def resolve_prompt(store: VaultStore, operation: str, topic: str = "") -> ResolvedPrompt:
    """Resolve ``operation``'s prompt body from an already-resolved vault.

    Precedence: topic override when the file exists, else the root default.
    A topic that is empty, not topic-shaped, or simply has no override falls
    through to the root default -- at prompt time the topic argument is
    unvalidated user intent, and the body's own topic-inference policy
    resolves it (no store path is ever built from a non-topic-shaped string).

    A missing root default raises the typed ``NOT_CONFIGURED`` error: the
    vault resolved as READY yet lacks its prompt defaults (malformed vault).
    """
    cleaned_operation = _validated_operation(operation)
    cleaned_topic = topic.strip()
    override = _override_candidate(cleaned_operation, cleaned_topic)
    if override is not None and store.exists(override):
        return ResolvedPrompt(
            operation=cleaned_operation,
            topic=cleaned_topic,
            body=store.read_text(override),
            source_path=override,
            topic_override=True,
        )
    root = root_prompt_path(cleaned_operation)
    if not store.exists(root):
        raise KnoticaError(
            code=ErrorCode.NOT_CONFIGURED,
            message=(
                f"Prompt '{cleaned_operation}' failed to resolve because the vault"
                f" has no root prompt file at {root} -- the vault is initialized"
                " but its prompt defaults are missing."
            ),
            fix=(
                "Restore the default prompt files: re-run `knotica init` for this"
                f" vault, or copy `{PROMPTS_DIR}/` from the vault template."
            ),
        )
    return ResolvedPrompt(
        operation=cleaned_operation,
        topic=cleaned_topic,
        body=store.read_text(root),
        source_path=root,
        topic_override=False,
    )


def get_prompt(
    operation: str,
    topic: str = "",
    *,
    vault: str | None = None,
    config_path: str | os.PathLike[str] | None = None,
    store_factory: Callable[[Path], VaultStore] = LocalFSStore,
) -> ResolvedPrompt:
    """Resolve config fresh, then resolve the prompt -- the per-call entry point.

    This is the function both the MCP prompt handler and ``knotica prompt``
    call. Unconfigured (config resolution fails) returns the setup-guidance
    body -- built from ``core.config``'s state-specific diagnosis and the
    canonical ``core.errors`` fix text -- rather than raising, so an
    unconfigured invocation still serves actionable content. A malformed
    vault (READY but missing prompt defaults) raises from
    :func:`resolve_prompt` and is deliberately *not* converted to guidance.
    """
    cleaned_operation = _validated_operation(operation)
    try:
        resolved_vault = config.resolve(vault=vault, config_path=config_path)
    except KnoticaError as error:
        return _setup_guidance(cleaned_operation, topic, error)
    return resolve_prompt(store_factory(resolved_vault.path), cleaned_operation, topic)


def _validated_operation(operation: str) -> str:
    """Return ``operation`` stripped, or raise ``ValueError`` if unknown."""
    cleaned = operation.strip()
    if cleaned not in OPERATIONS:
        raise ValueError(
            f"Unknown operation {operation!r}; expected one of: {', '.join(OPERATIONS)}."
        )
    return cleaned


def _override_candidate(operation: str, topic: str) -> str | None:
    """The override path to probe, or ``None`` when no override can apply."""
    if not topic:
        return None
    try:
        return override_prompt_path(operation, topic)
    except ValueError:
        return None  # not topic-shaped -> the body's inference policy resolves it


def _setup_guidance(operation: str, topic: str, error: KnoticaError) -> ResolvedPrompt:
    """Build the setup-guidance body served in place of a prompt when unconfigured."""
    body = (
        "# knotica is not configured\n"
        "\n"
        f"The `{operation}` operation needs a configured vault, but none could"
        f" be resolved: {error.message}\n"
        "\n"
        f"{error.fix}\n"
        "\n"
        "Configuration is re-read on every call -- once setup completes, invoke"
        f" `{operation}` again; no restart is needed.\n"
    )
    return ResolvedPrompt(
        operation=operation,
        topic=topic.strip(),
        body=body,
        source_path=None,
        topic_override=False,
    )
