"""OKF compatibility checker."""

from __future__ import annotations

from dataclasses import dataclass, field

from knotica.core.links import iter_page_paths
from knotica.okf.frontmatter import (
    FrontmatterFinding,
    check_concept_frontmatter,
    check_index_file,
    is_concept_file,
)
from knotica.okf.index import build_vault_index
from knotica.okf.links import extract_internal_links, resolve_internal_link
from knotica.okf.log_fmt import check_log_shape
from knotica.store import VaultStore


@dataclass
class OkfCheckResult:
    """Outcome of an OKF compatibility check."""

    status: str  # OKF-COMPATIBLE | FAILED
    bundle_root: str
    concept_files_checked: int = 0
    reserved_files_checked: int = 0
    errors: list[FrontmatterFinding] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    strict_failures: list[str] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return self.status == "FAILED" or bool(self.strict_failures)


def check_vault(
    store: VaultStore,
    *,
    strict: bool = False,
    export_ready: bool = False,
    overrides: dict[str, str] | None = None,
) -> OkfCheckResult:
    """Check native OKF compatibility of the vault."""
    index = build_vault_index(store, overrides=overrides)
    result = OkfCheckResult(status="OKF-COMPATIBLE", bundle_root=str(store.root))
    overrides = overrides or {}

    for path in iter_page_paths(store):
        raw = overrides.get(path, store.read_text(path))
        if path.endswith("index.md"):
            result.reserved_files_checked += 1
            for finding in check_index_file(path, raw):
                if finding.severity == "error":
                    result.errors.append(finding)
                else:
                    result.warnings.append(f"{path}: {finding.message}")
        elif path.endswith("log.md"):
            result.reserved_files_checked += 1
            result.warnings.extend(f"log.md: {w}" for w in check_log_shape(raw))
        elif is_concept_file(path):
            result.concept_files_checked += 1
            for finding in check_concept_frontmatter(path, raw):
                if finding.severity == "error":
                    result.errors.append(finding)
                else:
                    result.warnings.append(f"{path}: {finding.message}")

    for path, body in index.body_by_path.items():
        for link in extract_internal_links(path, body):
            if link.is_external:
                continue
            resolved = resolve_internal_link(link, index)
            if not resolved.resolved:
                message = f"{path}: {resolved.raw} -> unresolved"
                if strict or export_ready:
                    result.strict_failures.append(message)
                else:
                    result.warnings.append(message)
            if resolved.ambiguous:
                message = f"{path}: {resolved.raw} -> ambiguous"
                if strict or export_ready:
                    result.strict_failures.append(message)
                else:
                    result.warnings.append(message)
            if export_ready and link.syntax == "wikilink":
                result.strict_failures.append(
                    f"{path}: export-ready requires Markdown links ({link.raw})"
                )

    if (
        result.errors
        or (strict and result.strict_failures)
        or (export_ready and result.strict_failures)
    ):
        result.status = "FAILED"
    return result
