"""Console-free vault scaffolding, shared by ``knotica init`` and ``vault action=create``.

Extracted from ``cli.init`` so both surfaces run the exact same scaffold logic
instead of duplicating it: copy the packaged template, optionally seed a topic,
and git-bootstrap the result. This module never prints -- callers own their own
progress reporting (the CLI wizard's ``console.info`` lines, the MCP
dispatcher's structured envelope).

**Git bootstrap exemption (documented, carried over from ``cli.init``).**
Standing up a *new* repository (``git init`` + one initial commit over the
freshly copied template) is one-time repo setup, not ongoing vault mutation,
so it does not go through the ``core`` single-writer seam
(``core.transaction``/``core.vcs``) and never imports ``core.lock``. The
bootstrap is confined to this module via a narrow :func:`subprocess.run`
wrapper.

Every :class:`~knotica.core.errors.KnoticaError` raised here carries a plain,
un-prefixed factual ``message`` (no "init failed because" / "vault action=
create failed because" framing) -- each caller composes its own
surface-appropriate grammar around it (the CLI wizard's ``_InitError`` text,
the MCP dispatcher's structured envelope).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.template import TemplateNotFoundError
from knotica.core.template import packaged_template_path as _locate_template
from knotica.core.vault_layout import RESERVED_TOP_LEVEL_NAMES as RESERVED_TOPIC_NAMES

__all__ = ["RESERVED_TOPIC_NAMES", "ScaffoldResult", "scaffold_vault"]

#: Timeout for every bootstrap subprocess call.
_SUBPROCESS_TIMEOUT_SECONDS = 120.0

_EMPTY_OVERLAY = """\
---
schema_version: 1
---

# SCHEMA — {topic} overlay

Empty overlay: this topic starts with no divergence from the root constitution
(root `SCHEMA.md`). Add entity types and page conventions here as the topic
earns them.
"""


@dataclass(frozen=True, slots=True)
class ScaffoldResult:
    """Outcome of :func:`scaffold_vault`."""

    path: Path
    #: False when ``path`` was already an initialized knotica vault (idempotent skip).
    created: bool
    #: Whether an initial commit was made (false when there was nothing to commit).
    committed: bool


def scaffold_vault(vault_path: Path, *, topic: str | None = None) -> ScaffoldResult:
    """Scaffold a **bare** knotica vault at ``vault_path`` from the packaged template.

    Idempotent and NO-CLOBBER: an already-scaffolded vault (root ``SCHEMA.md``
    present) is left untouched (``created=False``); a non-empty directory that
    is not a knotica vault raises ``INVALID_ARGUMENT`` rather than merging into
    unrelated content. When ``topic`` is given, seeds ``<topic>/SCHEMA.md``
    from the empty-overlay template (idempotent) after rejecting reserved
    names. Always runs the git bootstrap (``git init`` + an initial commit) --
    itself idempotent, see the module docstring's exemption note.

    A scaffolded vault is always **bare** -- the constitution plus any requested
    topic, nothing else. The packaged template ships an ``agentic-systems`` demo
    topic solely as the test suite's fixture data, so every vault the system
    creates (``knotica init`` or ``vault action=create``) has that demo topic
    directory, its ``sources/<topic>`` tree, and its ``index.md`` / ``log.md``
    entries stripped: the system never scaffolds demo content into a user's
    vault. Only a freshly copied template is stripped; an already-scaffolded
    vault is never mutated.

    Raises:
        KnoticaError: ``INVALID_ARGUMENT`` for a non-vault non-empty target or
            a missing packaged template; ``RESERVED_NAME`` for a reserved
            topic; ``GIT_ERROR`` when the bootstrap subprocess fails.
    """
    if topic is not None and topic in RESERVED_TOPIC_NAMES:
        raise KnoticaError(
            code=ErrorCode.RESERVED_NAME,
            message=f"'{topic}' is a reserved name and cannot be a topic.",
            fix="Choose a different topic name (kebab-case or lowercase).",
        )
    created = _copy_template(vault_path)
    if created:
        _strip_demo(vault_path)
    if topic is not None:
        _seed_topic(vault_path, topic)
    committed = _git_bootstrap(vault_path)
    return ScaffoldResult(path=vault_path, created=created, committed=committed)


def _strip_demo(vault_path: Path) -> None:
    """Remove the packaged demo content from a freshly copied template.

    Deletes every top-level *topic* directory (a directory carrying its own
    ``SCHEMA.md``, distinguishing it from the ``.knotica`` / ``sources``
    constitution dirs) and its ``sources/<topic>`` tree, then trims the demo's
    catalog/log entries: ``index.md`` keeps everything before its first ``###``
    topic section, ``log.md`` everything before its first dated ``## YYYY-MM-DD``
    entry (the format-doc example inside a code fence is not a real dated entry,
    so the date-anchored split leaves it intact).
    """
    for entry in sorted(vault_path.iterdir()):
        if (
            entry.is_dir()
            and entry.name not in RESERVED_TOPIC_NAMES
            and (entry / "SCHEMA.md").is_file()
        ):
            shutil.rmtree(entry)
            demo_sources = vault_path / "sources" / entry.name
            if demo_sources.is_dir():
                shutil.rmtree(demo_sources)
    _truncate_before(vault_path / "index.md", re.compile(r"\n### "))
    _truncate_before(vault_path / "log.md", re.compile(r"\n## \d{4}-\d{2}-\d{2}"))


def _truncate_before(path: Path, boundary: re.Pattern[str]) -> None:
    """Keep only the text before the first ``boundary`` match (no-op if absent)."""
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    match = boundary.search(text)
    if match is None:
        return
    path.write_text(text[: match.start()].rstrip() + "\n", encoding="utf-8")


def _copy_template(vault_path: Path) -> bool:
    """Copy the packaged template into ``vault_path``; return whether it was copied."""
    if vault_path.exists() and any(vault_path.iterdir()):
        if (vault_path / "SCHEMA.md").is_file():
            return False
        raise KnoticaError(
            code=ErrorCode.INVALID_ARGUMENT,
            message=f"{vault_path} is not empty and is not a knotica vault.",
            fix="Choose an empty path, or remove the directory first.",
        )
    template = _packaged_template_path()
    shutil.copytree(template, vault_path, dirs_exist_ok=True)
    return True


def _packaged_template_path() -> Path:
    """Locate the packaged template, mapping a missing install to ``INVALID_ARGUMENT``."""
    try:
        return _locate_template()
    except TemplateNotFoundError as missing:
        raise KnoticaError(
            code=ErrorCode.INVALID_ARGUMENT,
            message=str(missing),
            fix="Reinstall knotica so the template ships with the wheel.",
        ) from missing


def _seed_topic(vault_path: Path, topic: str) -> None:
    """Create a minimal empty-overlay topic (idempotent -- skips if present)."""
    schema = vault_path / topic / "SCHEMA.md"
    if schema.is_file():
        return
    schema.parent.mkdir(parents=True, exist_ok=True)
    schema.write_text(_EMPTY_OVERLAY.format(topic=topic), encoding="utf-8")


def _git_bootstrap(vault_path: Path) -> bool:
    """Initialize the vault repo and make the initial commit (idempotent).

    Returns whether a commit was made. Re-running is safe: ``init`` is skipped
    when a repo exists and the commit is skipped when there is nothing to
    commit.
    """
    if not (vault_path / ".git").exists():
        _git(vault_path, "init", "-q")
    _git(vault_path, "add", "-A")
    if not _git(vault_path, "status", "--porcelain").stdout.strip():
        return False
    commit = ["commit", "-q", "-m", "Initialize knotica vault"]
    if not _has_git_identity(vault_path):
        commit = ["-c", "user.name=knotica", "-c", "user.email=knotica@localhost", *commit]
    _git(vault_path, *commit)
    return True


def _has_git_identity(vault_path: Path) -> bool:
    """Return whether git has a committer identity configured for this repo."""
    result = _git(vault_path, "config", "user.email", check=False)
    return bool(result.stdout.strip())


def _git(vault_path: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a ``git -C <vault>`` bootstrap command, surfacing failures cleanly."""
    return _run(["git", "-C", str(vault_path), *args], check=check)


def _run(argv: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
    """Run a subprocess, mapping a checked failure to a ``GIT_ERROR``."""
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=check,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "").strip()
        raise KnoticaError(
            code=ErrorCode.GIT_ERROR,
            message=f"`{' '.join(argv[:2])}` exited {error.returncode} ({detail}).",
        ) from error
    except FileNotFoundError as error:
        raise KnoticaError(
            code=ErrorCode.GIT_ERROR,
            message="`git` is not installed.",
            fix="Install git and retry.",
        ) from error
