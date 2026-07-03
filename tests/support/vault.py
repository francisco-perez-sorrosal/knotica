"""Vault-test helpers: frontmatter, git inspection, frozen grammars, foreign edits.

This module is the helper half of the vault-fixture test spine (the fixture half
lives in ``tests/conftest.py``). Every mutation test builds on these:

- ``parse_frontmatter`` — parse a page's YAML frontmatter block. Deliberately a
  minimal parser for the frontmatter subset the vault constitution
  (``vault-template/SCHEMA.md``) actually uses — scalar values, inline lists
  (``[a, b]``) and block lists (``- item``) — so the test suite carries no YAML
  dependency. Not a general YAML parser; extend it only when the constitution
  grows a new shape.
- ``run_git`` / ``git_commit_subjects`` / ``git_commit_count`` /
  ``git_status_porcelain`` / ``git_head_sha`` / ``git_is_ignored`` — subprocess
  git inspection for throwaway vaults, isolated from the developer's global and
  system git config (identity, hooks, gpg signing cannot leak in).
- ``KNOTICA_COMMIT_RE`` / ``parse_knotica_commit`` — the frozen commit-message
  grammar ``knotica(<op>): <topic> — <title>`` (em-dash separator).
- ``LOG_ENTRY_RE`` / ``parse_log_entries`` — the frozen log-entry grammar
  ``## [YYYY-MM-DD] <op> | <topic> | <title>`` plus its touched-page bullets;
  fenced code blocks (the format examples in ``log.md``'s header) are skipped.
- ``make_foreign_edit`` / ``ForeignEdit`` — simulate a concurrent, uncommitted
  user edit (an Obsidian window left open on a draft): transaction tests assert
  the edit survives both a commit and a rollback untouched via
  ``ForeignEdit.assert_intact()``.
"""

import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Frontmatter (constitution subset)
# ---------------------------------------------------------------------------


def parse_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """Split a page into its frontmatter fields and body.

    Returns ``(fields, body)``. Raises ``ValueError`` when the text carries no
    leading ``---``-delimited frontmatter block.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("no frontmatter block: text does not start with a '---' line")
    try:
        end = next(i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration:
        raise ValueError("unterminated frontmatter: opening '---' has no closing '---'") from None

    fields: dict[str, object] = {}
    open_list: list[object] | None = None
    for raw in lines[1:end]:
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith("- ") and open_list is not None:
            open_list.append(_coerce_scalar(stripped[2:]))
            continue
        open_list = None
        key, sep, value = raw.partition(":")
        if not sep:
            raise ValueError(f"unparseable frontmatter line: {raw!r}")
        key = key.strip()
        value = value.strip()
        if not value:
            block_list: list[object] = []
            fields[key] = block_list
            open_list = block_list
        elif value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            fields[key] = [_coerce_scalar(item) for item in inner.split(",")] if inner else []
        else:
            fields[key] = _coerce_scalar(value)
    body = "\n".join(lines[end + 1 :])
    return fields, body


def _coerce_scalar(value: str) -> object:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    if value in ("null", "~"):
        return None
    if value.lstrip("-").isdigit():
        return int(value)
    return value


# ---------------------------------------------------------------------------
# Git inspection (isolated from the developer's git config)
# ---------------------------------------------------------------------------

_GIT_RETRIES = 3
_GIT_RETRY_WAIT_S = 2.0


def _git_env() -> dict[str, str]:
    """Environment that blinds git to the developer's global/system config.

    Keeps throwaway-vault behavior deterministic: no inherited hooks, gpg
    signing, or defaultBranch surprises; no credential prompts can hang a test.
    """
    return {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
    }


def run_git(repo: Path, *args: str) -> str:
    """Run ``git -C <repo> <args>``, returning stdout; raise on failure.

    An ``index.lock`` collision (another process mid-operation on the same
    repo) is retried up to 3 times with a 2 s wait — the normal path never
    sleeps.
    """
    last: subprocess.CompletedProcess[str] | None = None
    for _ in range(_GIT_RETRIES):
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            env=_git_env(),
        )
        if result.returncode == 0:
            return result.stdout
        last = result
        if "index.lock" not in result.stderr:
            break
        time.sleep(_GIT_RETRY_WAIT_S)
    assert last is not None
    raise RuntimeError(
        f"git {' '.join(args)} failed in {repo} (rc={last.returncode}):\n{last.stderr}"
    )


def git_commit_subjects(repo: Path) -> list[str]:
    """Commit subject lines, newest first."""
    return [line for line in run_git(repo, "log", "--format=%s").splitlines() if line]


def git_commit_count(repo: Path) -> int:
    return int(run_git(repo, "rev-list", "--count", "HEAD").strip())


def git_status_porcelain(repo: Path) -> str:
    """``git status --porcelain`` output; empty string means a clean tree."""
    return run_git(repo, "status", "--porcelain")


def git_head_sha(repo: Path) -> str:
    return run_git(repo, "rev-parse", "HEAD").strip()


def git_is_ignored(repo: Path, relpath: str) -> bool:
    """Whether git would ignore ``relpath`` (which need not exist)."""
    result = subprocess.run(
        ["git", "-C", str(repo), "check-ignore", "-q", relpath],
        capture_output=True,
        text=True,
        env=_git_env(),
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(f"git check-ignore {relpath} errored in {repo}:\n{result.stderr}")
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Frozen grammars (single source: vault-template/SCHEMA.md §Machine-record schemas)
# ---------------------------------------------------------------------------

# Commit message: knotica(<op>): <topic> — <title>  (em-dash with surrounding spaces)
KNOTICA_COMMIT_RE = re.compile(r"^knotica\((?P<op>[a-z_]+)\): (?P<topic>.+?) — (?P<title>.+)$")

# Log entry H2: ## [YYYY-MM-DD] <op> | <topic> | <title>
LOG_ENTRY_RE = re.compile(
    r"^## \[(?P<date>\d{4}-\d{2}-\d{2})\] (?P<op>[a-z_]+) \| (?P<topic>.+?) \| (?P<title>.+)$"
)


def parse_knotica_commit(subject: str) -> dict[str, str] | None:
    """Parse a commit subject against the frozen grammar; None when it does not match."""
    match = KNOTICA_COMMIT_RE.match(subject)
    return match.groupdict() if match else None


@dataclass
class LogEntry:
    date: str
    op: str
    topic: str
    title: str
    pages: list[str] = field(default_factory=list)


def parse_log_entries(log_text: str) -> list[LogEntry]:
    """Parse every frozen-grammar entry in a ``log.md`` body, with its bullets.

    Lines inside fenced code blocks are skipped — ``log.md``'s header carries
    the format specification and an example inside fences, and those must never
    count as real operation entries.
    """
    entries: list[LogEntry] = []
    current: LogEntry | None = None
    in_fence = False
    for line in log_text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = LOG_ENTRY_RE.match(line)
        if match:
            current = LogEntry(**match.groupdict())
            entries.append(current)
            continue
        if not line.strip():
            continue
        if current is not None and line.startswith("- "):
            current.pages.append(line[2:].strip())
        else:
            current = None
    return entries


# ---------------------------------------------------------------------------
# Foreign uncommitted edit (the concurrent Obsidian user)
# ---------------------------------------------------------------------------

_DEFAULT_FOREIGN_CONTENT = "Draft note typed in Obsidian while a knotica operation runs.\n"


@dataclass(frozen=True)
class ForeignEdit:
    """A concurrent user edit that no knotica operation may sweep up or destroy."""

    path: Path
    content: str

    def assert_intact(self) -> None:
        assert self.path.exists(), (
            f"foreign uncommitted edit vanished: {self.path} — an operation "
            "touched a file outside its own transaction"
        )
        actual = self.path.read_text(encoding="utf-8")
        assert actual == self.content, (
            f"foreign uncommitted edit was altered: {self.path}\n"
            f"expected: {self.content!r}\n"
            f"actual:   {actual!r}"
        )


def make_foreign_edit(
    vault: Path,
    relpath: str = "concurrent-obsidian-note.md",
    content: str = _DEFAULT_FOREIGN_CONTENT,
) -> ForeignEdit:
    """Write an uncommitted edit into the vault, as a concurrent Obsidian user would.

    With the default ``relpath`` this creates a new untracked file; pass the
    path of an existing tracked page to simulate an unstaged modification.
    Nothing is staged or committed either way.
    """
    path = vault / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return ForeignEdit(path=path, content=content)
