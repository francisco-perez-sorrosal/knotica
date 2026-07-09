"""Normalized internal link representation -- wikilinks and Markdown."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import urlparse

from knotica.okf.constants import IMAGE_EXTENSIONS
from knotica.okf.index import VaultIndex, topic_root_for_path
from knotica.okf.slug import slugify_heading

__all__ = [
    "InternalLink",
    "extract_internal_links",
    "resolve_internal_link",
    "rewrite_links_for_export",
]

_WIKILINK_RE = re.compile(r"(!?)\[\[([^\[\]]+)\]\]")
_MARKDOWN_LINK_RE = re.compile(r"(!?)\[([^\]]*)\]\(([^)]+)\)")
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_EXTERNAL_SCHEMES = frozenset({"http", "https", "mailto", "ftp", "ftps"})


@dataclass(frozen=True)
class InternalLink:
    """One normalized link edge extracted from markdown body text."""

    source_path: str
    raw: str
    label: str | None
    target_ref: str
    target_path: str | None
    target_concept_id: str | None
    heading: str | None
    block_id: str | None
    syntax: Literal["markdown", "wikilink"]
    is_embed: bool = False
    is_image: bool = False
    is_external: bool = False
    resolved: bool = False
    ambiguous: bool = False
    warnings: tuple[str, ...] = ()


def extract_internal_links(source_path: str, text: str) -> list[InternalLink]:
    """Extract wikilinks and internal Markdown links from ``text``."""
    links: list[InternalLink] = []
    in_fence = False
    for line_number, line in enumerate(text.splitlines(), start=1):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        masked = _INLINE_CODE_RE.sub(lambda match: " " * len(match.group(0)), line)
        for match in _WIKILINK_RE.finditer(masked):
            embed_prefix, reference = match.group(1), match.group(2)
            link = _parse_wikilink(source_path, match.group(0), reference, bool(embed_prefix))
            if link is not None:
                links.append(link)
        for match in _MARKDOWN_LINK_RE.finditer(masked):
            embed_prefix, label, target = match.group(1), match.group(2), match.group(3)
            link = _parse_markdown_link(
                source_path, match.group(0), label, target, bool(embed_prefix)
            )
            if link is not None:
                links.append(link)
    return links


def resolve_internal_link(link: InternalLink, index: VaultIndex) -> InternalLink:
    """Resolve ``link`` against ``index``; return an updated copy."""
    if link.is_external:
        return link
    candidates = _resolution_candidates(link, index)
    if not candidates:
        return InternalLink(
            **{**link.__dict__, "resolved": False, "warnings": link.warnings + ("unresolved",)}
        )
    if len(candidates) > 1:
        return InternalLink(
            **{
                **link.__dict__,
                "target_path": candidates[0],
                "target_concept_id": candidates[0].removesuffix(".md"),
                "resolved": False,
                "ambiguous": True,
                "warnings": link.warnings + (f"ambiguous: {', '.join(sorted(candidates))}",),
            }
        )
    target = candidates[0]
    return InternalLink(
        **{
            **link.__dict__,
            "target_path": target,
            "target_concept_id": target.removesuffix(".md"),
            "resolved": True,
        }
    )


def resolve_all_links(index: VaultIndex) -> list[InternalLink]:
    """Extract and resolve all internal links in indexed concept bodies."""
    resolved: list[InternalLink] = []
    for path, body in index.body_by_path.items():
        for link in extract_internal_links(path, body):
            resolved.append(resolve_internal_link(link, index))
    for path in index.reserved_paths:
        raw_body = index.body_by_path.get(path, "")
        if not raw_body and path.endswith(".md"):
            continue
        for link in extract_internal_links(path, raw_body):
            resolved.append(resolve_internal_link(link, index))
    return resolved


def rewrite_links_for_export(
    source_path: str,
    text: str,
    index: VaultIndex,
    *,
    link_style: Literal["bundle-relative", "relative"] = "bundle-relative",
    lossy_embeds: bool = False,
) -> tuple[str, list[str]]:
    """Rewrite wikilinks/embeds to standard Markdown links; return warnings."""
    warnings: list[str] = []

    def replace_wikilink(match: re.Match[str]) -> str:
        embed_prefix, reference = match.group(1), match.group(2)
        raw = match.group(0)
        link = _parse_wikilink(source_path, raw, reference, bool(embed_prefix))
        if link is None:
            return raw
        resolved = resolve_internal_link(link, index)
        if resolved.is_image:
            return _export_image_embed(resolved, link_style)
        if resolved.is_embed and not lossy_embeds:
            warnings.append(f"note embed preserved as warning: {raw}")
            return raw
        if resolved.block_id and not lossy_embeds:
            warnings.append(f"block reference not losslessly convertible: {raw}")
            return raw
        if not resolved.resolved:
            warnings.append(f"unresolved wikilink: {raw}")
            return raw
        return _to_markdown_link(resolved, index, link_style, lossy_embeds=lossy_embeds)

    def replace_markdown(match: re.Match[str]) -> str:
        embed_prefix, label, target = match.group(1), match.group(2), match.group(3)
        raw = match.group(0)
        link = _parse_markdown_link(source_path, raw, label, target, bool(embed_prefix))
        if link is None or link.is_external:
            return raw
        resolved = resolve_internal_link(link, index)
        if not resolved.resolved:
            warnings.append(f"unresolved markdown link: {raw}")
            return raw
        return _to_markdown_link(resolved, index, link_style, label=label)

    result = text
    in_fence = False
    lines: list[str] = []
    for line in result.splitlines(keepends=True):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            lines.append(line)
            continue
        if in_fence:
            lines.append(line)
            continue
        masked_line = line
        # Only rewrite outside inline code spans.
        parts: list[str] = []
        last = 0
        for code_match in _INLINE_CODE_RE.finditer(line):
            segment = line[last : code_match.start()]
            segment = _WIKILINK_RE.sub(replace_wikilink, segment)
            segment = _MARKDOWN_LINK_RE.sub(replace_markdown, segment)
            parts.append(segment)
            parts.append(code_match.group(0))
            last = code_match.end()
        tail = line[last:]
        tail = _WIKILINK_RE.sub(replace_wikilink, tail)
        tail = _MARKDOWN_LINK_RE.sub(replace_markdown, tail)
        parts.append(tail)
        lines.append("".join(parts))
    return "".join(lines), warnings


def _parse_wikilink(
    source_path: str, raw: str, reference: str, is_embed: bool
) -> InternalLink | None:
    target_part, _, alias_part = reference.partition("|")
    alias = alias_part.strip() or None
    heading = None
    block_id = None
    if "#" in target_part:
        target_part, fragment = target_part.split("#", 1)
        if fragment.startswith("^"):
            block_id = fragment[1:]
        else:
            heading = fragment
    target_ref = target_part.strip()
    if not target_ref:
        return None
    suffix = PurePosixPath(target_ref).suffix.lower()
    is_image = is_embed and suffix in IMAGE_EXTENSIONS
    return InternalLink(
        source_path=source_path,
        raw=raw,
        label=alias,
        target_ref=target_ref,
        target_path=None,
        target_concept_id=None,
        heading=heading,
        block_id=block_id,
        syntax="wikilink",
        is_embed=is_embed,
        is_image=is_image,
    )


def _parse_markdown_link(
    source_path: str, raw: str, label: str, target: str, is_embed: bool
) -> InternalLink | None:
    target = target.strip()
    if not target:
        return None
    parsed = urlparse(target)
    if parsed.scheme in _EXTERNAL_SCHEMES:
        return InternalLink(
            source_path=source_path,
            raw=raw,
            label=label or None,
            target_ref=target,
            target_path=None,
            target_concept_id=None,
            heading=None,
            block_id=None,
            syntax="markdown",
            is_embed=is_embed,
            is_external=True,
            resolved=True,
        )
    heading = None
    if "#" in target:
        target, fragment = target.split("#", 1)
        heading = fragment
    return InternalLink(
        source_path=source_path,
        raw=raw,
        label=label or None,
        target_ref=target,
        target_path=None,
        target_concept_id=None,
        heading=heading,
        block_id=None,
        syntax="markdown",
        is_embed=is_embed,
        is_image=is_embed and PurePosixPath(target).suffix.lower() in IMAGE_EXTENSIONS,
    )


def _resolution_candidates(link: InternalLink, index: VaultIndex) -> list[str]:
    ref = link.target_ref
    source_dir = str(PurePosixPath(link.source_path).parent)
    if source_dir == ".":
        source_dir = ""
    tiers: list[list[str]] = []

    if link.syntax == "wikilink":
        if "/" in ref:
            tiers.append(_path_variants(ref))
        else:
            if source_dir:
                tiers.append(_path_variants(f"{source_dir}/{ref}"))
            if link.source_path in {"index.md", "START_HERE.md", "log.md"}:
                tiers.append(_path_variants(ref))
            topic = topic_root_for_path(link.source_path)
            if topic and topic != source_dir:
                tiers.append(_path_variants(f"{topic}/{ref}"))
            if not source_dir:
                tiers.append(_path_variants(ref))
            tiers.append(index.by_basename.get(ref, []))
            lower_ref = ref.lower()
            tiers.append(index.by_title.get(lower_ref, []))
            tiers.append(index.by_h1.get(lower_ref, []))
    else:
        if ref.startswith("/"):
            tiers.append(_path_variants(ref.lstrip("/")))
        else:
            base = source_dir or ""
            joined = f"{base}/{ref}" if base else ref
            tiers.append(_path_variants(joined))

    for tier in tiers:
        existing = _dedupe_existing(tier, index)
        if existing:
            return existing
    return []


def _dedupe_existing(candidates: list[str], index: VaultIndex) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for candidate in candidates:
        if candidate in index.concept_paths and candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)
    return unique


def _path_variants(ref: str) -> list[str]:
    ref = ref.removesuffix("/")
    if ref.endswith(".md"):
        return [ref]
    return [f"{ref}.md", f"{ref}/index.md"]


def _to_markdown_link(
    link: InternalLink,
    index: VaultIndex,
    link_style: Literal["bundle-relative", "relative"],
    *,
    label: str | None = None,
    lossy_embeds: bool = False,
) -> str:
    assert link.target_path is not None
    target_path = link.target_path
    if link_style == "bundle-relative":
        href = f"/{target_path}"
    else:
        source_parts = PurePosixPath(link.source_path).parent.parts
        target_parts = PurePosixPath(target_path).parts
        href = _relative_posix_path(source_parts, target_parts)
    if link.heading:
        href += f"#{slugify_heading(link.heading)}"
    if link.block_id:
        href += f"#^{link.block_id}"
    display = label or link.label or _default_label(link, index)
    if link.is_embed and lossy_embeds:
        display = f"Embedded note: {display}"
    prefix = "!" if link.is_image else ""
    return f"{prefix}[{display}]({href})"


def _default_label(link: InternalLink, index: VaultIndex) -> str:
    if link.target_path and link.target_path in index.frontmatter_by_path:
        title = index.frontmatter_by_path[link.target_path].get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
    if link.target_path and link.target_path in index.body_by_path:
        for line in index.body_by_path[link.target_path].splitlines():
            if line.startswith("# "):
                return line[2:].strip()
    return title_from_ref(link.target_ref)


def _relative_posix_path(source_parts: tuple[str, ...], target_parts: tuple[str, ...]) -> str:
    """Compute a relative POSIX path from source directory to target file."""
    common = 0
    for left, right in zip(source_parts, target_parts, strict=False):
        if left != right:
            break
        common += 1
    ups = [".."] * (len(source_parts) - common)
    down = list(target_parts[common:])
    parts = ups + down
    return "/".join(parts) if parts else target_parts[-1]


def title_from_ref(ref: str) -> str:
    stem = PurePosixPath(ref).stem
    return stem.replace("-", " ").replace("_", " ").title()


def _export_image_embed(
    link: InternalLink, link_style: Literal["bundle-relative", "relative"]
) -> str:
    assert link.target_path or link.target_ref
    path = link.target_path or link.target_ref
    if link_style == "bundle-relative":
        href = f"/{path}" if not path.startswith("/") else path
    else:
        href = path
    alt = PurePosixPath(path).name
    return f"![{alt}]({href})"
