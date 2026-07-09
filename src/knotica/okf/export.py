"""OKF bundle export -- pure interoperability artifact."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal

from knotica.core.links import iter_page_paths
from knotica.core.page import parse_page, serialize_frontmatter
from knotica.okf.constants import IMAGE_EXTENSIONS
from knotica.okf.datetime_fmt import now_rfc3339
from knotica.okf.check import check_vault
from knotica.okf.frontmatter import (
    is_concept_file,
    is_reserved_file,
    normalize_concept_frontmatter,
)
from knotica.okf.index import build_vault_index
from knotica.okf.links import (
    extract_internal_links,
    resolve_internal_link,
    rewrite_links_for_export,
)
from knotica.okf.log_fmt import normalize_log_to_okf
from knotica.store import LocalFSStore, VaultStore


@dataclass(frozen=True)
class ExportOptions:
    """Options controlling OKF export."""

    output: Path
    pure: bool = False
    link_style: Literal["bundle-relative", "relative"] = "bundle-relative"
    lossy_embeds: bool = False
    force: bool = False
    export_ready: bool = False


@dataclass
class ExportResult:
    """Outcome of an OKF export."""

    status: str
    source_vault: str
    output_path: str
    files_exported: int = 0
    files_transformed: int = 0
    attachments_copied: int = 0
    wikilinks_converted: int = 0
    warnings: list[str] = field(default_factory=list)
    report_path: str | None = None
    post_check_status: str | None = None


def export_bundle(store: VaultStore, options: ExportOptions) -> ExportResult:
    """Export the vault to a pure OKF bundle at ``options.output``."""
    source_root = Path(store.root).resolve()
    output = options.output.resolve()

    if output == source_root:
        raise ValueError("refusing to export into the active vault path")
    if output.exists() and any(output.iterdir()) and not options.force:
        raise ValueError(f"output path is not empty: {output} (pass --force to overwrite)")

    if output.exists() and options.force:
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)

    index = build_vault_index(store)
    result = ExportResult(
        status="EXPORTED",
        source_vault=str(source_root),
        output_path=str(output),
    )

    for path in sorted(iter_page_paths(store)):
        raw = store.read_text(path)
        dest = output / path
        dest.parent.mkdir(parents=True, exist_ok=True)

        if is_reserved_file(path) and path.endswith("index.md"):
            if raw.startswith("---"):
                _, _err, body = parse_page(raw)
                content = body
                result.warnings.append(f"stripped frontmatter from exported {path}")
            else:
                content = raw
            before_links = content.count("[[")
            content, link_warnings = rewrite_links_for_export(
                path, content, index, link_style=options.link_style
            )
            result.warnings.extend(link_warnings)
            result.wikilinks_converted += before_links - content.count("[[")
            dest.write_text(content, encoding="utf-8")
            result.files_exported += 1
            if content != raw:
                result.files_transformed += 1
            continue

        if is_reserved_file(path) and path.endswith("log.md"):
            normalized = normalize_log_to_okf(raw)
            content = normalized.content
            before_links = content.count("[[")
            content, link_warnings = rewrite_links_for_export(
                path, content, index, link_style=options.link_style
            )
            result.warnings.extend(normalized.warnings)
            result.warnings.extend(link_warnings)
            result.wikilinks_converted += before_links - content.count("[[")
            dest.write_text(content, encoding="utf-8")
            result.files_exported += 1
            if content != raw:
                result.files_transformed += 1
            continue

        if is_concept_file(path):
            normalized = normalize_concept_frontmatter(path, raw, pure=options.pure)
            result.warnings.extend(normalized.warnings)
            if not normalized.fields.get("type"):
                result.warnings.append(f"{path}: export produced type-less frontmatter")
            _, _err, body = parse_page(raw)
            before_links = body.count("[[")
            body, link_warnings = rewrite_links_for_export(
                path,
                body,
                index,
                link_style=options.link_style,
                lossy_embeds=options.lossy_embeds,
            )
            result.warnings.extend(link_warnings)
            result.wikilinks_converted += before_links - body.count("[[")
            if options.export_ready and "[[" in body:
                result.warnings.append(f"{path}: export-ready mode left wikilinks in body")
            content = serialize_frontmatter(normalized.fields) + body
            dest.write_text(content, encoding="utf-8")
            result.files_exported += 1
            if content != raw:
                result.files_transformed += 1
            continue

        dest.write_text(raw, encoding="utf-8")
        result.files_exported += 1

    attachment_paths = _collect_attachment_paths(store, index)
    for attachment in sorted(attachment_paths):
        source_file = source_root / attachment
        if not source_file.is_file():
            result.warnings.append(f"referenced attachment missing from vault: {attachment}")
            continue
        dest_file = output / attachment
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, dest_file)
        result.attachments_copied += 1

    report_path = output / "okf-export-report.md"
    report_body = _render_report(result, options)
    report_frontmatter = {
        "type": "report",
        "title": "OKF Export Report",
        "timestamp": now_rfc3339(),
        "tags": ["okf", "export"],
    }
    report_path.write_text(
        serialize_frontmatter(report_frontmatter) + report_body,
        encoding="utf-8",
    )
    result.report_path = str(report_path)

    post_store = LocalFSStore(output)
    post_check = check_vault(
        post_store,
        strict=options.export_ready,
        export_ready=options.export_ready,
    )
    result.post_check_status = post_check.status
    if post_check.failed:
        result.status = "FAILED"
        result.warnings.append("exported bundle failed post-export OKF check")
    elif options.export_ready and "[[" in _bundle_markdown_text(output):
        result.status = "FAILED"
        result.warnings.append("export-ready mode requires Markdown-only internal links")
    return result


def _collect_attachment_paths(store: VaultStore, index) -> set[str]:
    paths: set[str] = set()
    for path, body in index.body_by_path.items():
        for link in extract_internal_links(path, body):
            if link.is_external:
                continue
            suffix = PurePosixPath(link.target_ref).suffix.lower()
            if suffix in IMAGE_EXTENSIONS or (suffix and suffix != ".md"):
                resolved = resolve_internal_link(link, index)
                candidate = resolved.target_path or link.target_ref
                if store.exists(candidate):
                    paths.add(candidate)
    return paths


def _bundle_markdown_text(output: Path) -> str:
    chunks: list[str] = []
    for path in output.rglob("*.md"):
        if path.name == "okf-export-report.md":
            continue
        chunks.append(path.read_text(encoding="utf-8"))
    return "\n".join(chunks)


def _render_report(result: ExportResult, options: ExportOptions) -> str:
    lines = [
        "# OKF Export Report",
        "",
        f"- Source vault: `{result.source_vault}`",
        f"- Output path: `{result.output_path}`",
        f"- Export mode: {'pure' if options.pure else 'default'}",
        f"- Link style: {options.link_style}",
        f"- Files exported: {result.files_exported}",
        f"- Files transformed: {result.files_transformed}",
        f"- Attachments copied: {result.attachments_copied}",
        f"- Wikilinks converted: {result.wikilinks_converted}",
        f"- Post-export check: {result.post_check_status}",
        "",
    ]
    if result.warnings:
        lines.append("## Warnings")
        lines.extend(f"- {warning}" for warning in result.warnings)
        lines.append("")
    return "\n".join(lines)
