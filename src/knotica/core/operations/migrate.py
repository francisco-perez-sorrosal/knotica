"""``migrate`` -- schema-version migration that never clobbers evolved files.

The vault's schema evolves additive-only under this one operation: it compares
the vault's ``schema_version`` against the packaged template's and, when the
vault is behind, brings the structural files forward inside a single
:class:`~knotica.core.transaction.VaultTransaction` -- one commit, one log
entry, the audit invariant intact.

Three-way, never-clobber
------------------------
For every file the packaged template ships (except the transaction-owned
operation log), the vault file falls into one of three buckets:

* **add** -- the template introduces a file the vault lacks; the migration
  writes it verbatim (a genuinely new structural file, not an overwrite).
* **current** -- vault bytes already equal the template; nothing to do.
* **evolved** -- the vault file differs from the template. This is user (or
  agent) work the migration must never destroy, so it is *surfaced as a
  conflict and skipped*: the file is preserved byte-identical and the report
  names it with guidance to reconcile by hand.

The migration's completion signal is the ``schema_version`` bump on the scoped
schema file (root ``SCHEMA.md`` by default, or a topic overlay under
``--topic``). The bump preserves the file's body exactly and only rewrites the
version in its frontmatter -- so even an evolved constitution keeps every word
the user wrote while the version advances.

Config-agnostic like every operation: the caller resolves the vault root and
passes it in. Availability is decided purely by version (behind vs. current);
``apply=False`` (``--check`` / ``--dry-run``) computes the plan and writes
nothing.
"""

import importlib.resources
from dataclasses import dataclass
from pathlib import Path, PurePath

from knotica.core.errors import ErrorCode, KnoticaError, err, ok
from knotica.core.page import parse_page, serialize_frontmatter
from knotica.core.schema import ROOT_SCHEMA_PATH, overlay_path
from knotica.core.transaction import LOG_PATH, VaultTransaction
from knotica.core.vcs import VaultVcs
from knotica.store import VaultStore

#: The template directory name mapped into the wheel (see pyproject force-include).
_TEMPLATE_DIRNAME = "vault-template"

#: Frontmatter key carrying a schema file's version.
_SCHEMA_VERSION_KEY = "schema_version"


@dataclass(frozen=True)
class MigrationPlan:
    """The computed effect of a migration, before (or without) applying it.

    Attributes:
        scope: ``"root"`` or the topic name the migration is scoped to.
        current_version: The vault's current ``schema_version`` for the scope,
            or ``None`` when the scoped schema file is absent or unversioned.
        target_version: The packaged template's ``schema_version`` for the scope.
        available: Whether a migration is available (the vault is behind).
        additions: Template files the vault lacks (written on apply).
        conflicts: Evolved files preserved and skipped (never overwritten).
    """

    scope: str
    current_version: int | None
    target_version: int
    available: bool
    additions: tuple[str, ...]
    conflicts: tuple[str, ...]


def migrate(
    store: VaultStore,
    vault_root: str | PurePath,
    *,
    topic: str = "",
    apply: bool = False,
) -> dict[str, object]:
    """Compare vault vs. packaged-template schema version and (optionally) migrate.

    Args:
        store: The vault storage backend.
        vault_root: The already-resolved vault root (operations are config-agnostic).
        topic: When given, scope the migration to that topic's overlay instead
            of the root constitution.
        apply: When ``True`` and a migration is available, write the changes
            through a single transaction; otherwise compute the plan only.

    Returns:
        A success envelope whose pointer carries the plan (``available``,
        ``current_version``, ``target_version``, ``additions``, ``conflicts``,
        ``applied``, ``commit_sha``), or a typed failure envelope.
    """
    cleaned_topic = topic.strip()
    version_rel = overlay_path(cleaned_topic) if cleaned_topic else ROOT_SCHEMA_PATH
    scope = cleaned_topic or "root"
    template_root = _packaged_template_root()

    target_version = _template_schema_version(template_root, version_rel)
    if target_version is None:
        return err(
            ErrorCode.INVALID_FRONTMATTER,
            f"migrate failed because the packaged template has no versioned schema "
            f"for scope '{scope}' (expected {_SCHEMA_VERSION_KEY} in {version_rel}).",
        )

    plan = _build_plan(store, template_root, cleaned_topic, version_rel, scope, target_version)
    if not apply or not plan.available:
        head = VaultVcs(vault_root).head_sha()
        return ok(_plan_pointer(plan, commit_sha=head, applied=False))
    return _apply(store, vault_root, plan, template_root, version_rel)


def _packaged_template_root() -> Path:
    """Locate the packaged ``vault-template`` (wheel data, else the repo-root copy).

    Installed wheels carry the template as package data; editable/dev installs
    resolve the repo-root copy instead (the package tree has no template).
    """
    resource = importlib.resources.files("knotica") / _TEMPLATE_DIRNAME
    if resource.is_dir():
        return Path(str(resource))
    for parent in Path(__file__).resolve().parents:
        candidate = parent / _TEMPLATE_DIRNAME
        if candidate.is_dir():
            return candidate
    raise KnoticaError(
        ErrorCode.GIT_ERROR,
        "migrate failed because the packaged vault-template could not be located "
        "(neither as installed package data nor as a repo-root directory).",
        fix="Reinstall knotica so the packaged template ships with the wheel.",
    )


def _template_schema_version(template_root: Path, version_rel: str) -> int | None:
    """Read the ``schema_version`` of the template's scoped schema file, or None."""
    path = template_root / version_rel
    if not path.is_file():
        return None
    return _parse_schema_version(path.read_text(encoding="utf-8"))


def _parse_schema_version(text: str) -> int | None:
    """Extract the integer ``schema_version`` from a schema file's frontmatter."""
    frontmatter, _error, _body = parse_page(text)
    if frontmatter is None:
        return None
    value = frontmatter.get(_SCHEMA_VERSION_KEY)
    return value if isinstance(value, int) else None


def _build_plan(
    store: VaultStore,
    template_root: Path,
    topic: str,
    version_rel: str,
    scope: str,
    target_version: int,
) -> MigrationPlan:
    """Compute the add/current/evolved buckets and the availability decision."""
    current_version = _vault_schema_version(store, version_rel)
    additions: list[str] = []
    conflicts: list[str] = []
    for rel in _template_files(template_root, topic):
        if not store.exists(rel):
            additions.append(rel)
        elif store.read_text(rel) != (template_root / rel).read_text(encoding="utf-8"):
            conflicts.append(rel)
    return MigrationPlan(
        scope=scope,
        current_version=current_version,
        target_version=target_version,
        available=current_version is None or current_version < target_version,
        additions=tuple(additions),
        conflicts=tuple(conflicts),
    )


def _vault_schema_version(store: VaultStore, version_rel: str) -> int | None:
    """Read the vault's current ``schema_version`` for the scoped schema file."""
    if not store.exists(version_rel):
        return None
    return _parse_schema_version(store.read_text(version_rel))


def _template_files(template_root: Path, topic: str) -> list[str]:
    """Vault-relative template file paths in scope, excluding the operation log.

    The operation log is per-vault, transaction-owned content -- the migration
    never manages it. When ``topic`` is given, only files under that topic's
    subtree are considered.
    """
    prefix = f"{topic}/" if topic else ""
    files: list[str] = []
    for path in sorted(template_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(template_root).as_posix()
        if rel == LOG_PATH or not rel.startswith(prefix):
            continue
        files.append(rel)
    return files


def _apply(
    store: VaultStore,
    vault_root: str | PurePath,
    plan: MigrationPlan,
    template_root: Path,
    version_rel: str,
) -> dict[str, object]:
    """Write additions and the version bump through one transaction; envelope it.

    Evolved files (``plan.conflicts``) are deliberately absent from the write
    set -- they are preserved untouched. The scoped schema file's version is
    bumped in place with its body preserved.
    """
    title = f"schema migration to v{plan.target_version} ({plan.scope})"
    try:
        with VaultTransaction(store, vault_root, "migrate", plan.scope, title) as txn:
            for rel in plan.additions:
                if rel == version_rel:
                    continue
                txn.write(rel, (template_root / rel).read_text(encoding="utf-8"))
            txn.write(version_rel, _version_bumped(store, template_root, version_rel, plan))
    except KnoticaError as error:
        return error.envelope()
    result = txn.result
    return ok(
        _plan_pointer(plan, commit_sha=result.commit_sha, applied=True), warnings=result.warnings()
    )


def _version_bumped(
    store: VaultStore,
    template_root: Path,
    version_rel: str,
    plan: MigrationPlan,
) -> str:
    """Return the scoped schema file with its version set to the target.

    When the vault already has the file, its body is preserved exactly and only
    the ``schema_version`` frontmatter is rewritten -- an evolved constitution
    keeps every word. When the file is absent, the template copy (already at the
    target version) is written verbatim.
    """
    if not store.exists(version_rel):
        return (template_root / version_rel).read_text(encoding="utf-8")
    frontmatter, _error, body = parse_page(store.read_text(version_rel))
    fields: dict[str, object] = dict(frontmatter) if frontmatter else {}
    fields[_SCHEMA_VERSION_KEY] = plan.target_version
    return serialize_frontmatter(fields) + body


def _plan_pointer(plan: MigrationPlan, *, commit_sha: str, applied: bool) -> dict[str, object]:
    """Render a migration plan as a success-envelope pointer."""
    return {
        "scope": plan.scope,
        "current_version": plan.current_version,
        "target_version": plan.target_version,
        "available": plan.available,
        "applied": applied,
        "commit_sha": commit_sha,
        "additions": list(plan.additions),
        "conflicts": list(plan.conflicts),
    }
