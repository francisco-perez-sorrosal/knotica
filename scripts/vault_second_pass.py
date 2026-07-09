#!/usr/bin/env python3
"""Second-pass vault normalization: Knotica core fields + OKF compatibility."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from knotica.core.links import iter_page_paths
from knotica.core.page import parse_page, serialize_frontmatter
from knotica.okf.datetime_fmt import now_rfc3339
from knotica.okf.frontmatter import is_concept_file, normalize_concept_frontmatter
from knotica.okf.log_fmt import canonicalize_log
from knotica.store import LocalFSStore

VAULT = Path("/Users/fperez/dev/data/knotica")

# Knotica core defaults for meta pages (OKF-exempt from strict Knotica lint scope).
META_DEFAULTS: dict[str, object] = {
    "topic": "meta",
    "created": "2026-07-03T00:00:00Z",
    "updated": "2026-07-08T00:00:00Z",
    "confidence": "high",
    "sources": [],
    "status": "active",
    "tags": ["meta"],
}

SOURCE_DEFAULTS: dict[str, object] = {
    "created": "2026-07-03T00:00:00Z",
    "updated": "2026-07-08T00:00:00Z",
    "confidence": "high",
    "sources": [],
    "status": "active",
    "tags": ["source", "agentic-systems"],
}


def _git(*args: str) -> None:
    subprocess.run(["git", *args], cwd=VAULT, check=True)


def _relocate_reports() -> list[tuple[str, str]]:
    """Move root ``reports/`` under ``agentic-systems/reports/`` for topic-scoped lint."""
    moves: list[tuple[str, str]] = []
    reports_root = VAULT / "reports"
    if not reports_root.exists():
        return moves
    dest_root = VAULT / "agentic-systems" / "reports"
    dest_root.mkdir(parents=True, exist_ok=True)
    for child in sorted(reports_root.iterdir()):
        if child.name.startswith("."):
            continue
        dest = dest_root / child.name
        if dest.exists():
            continue
        old = str(child.relative_to(VAULT))
        new = str(dest.relative_to(VAULT))
        _git("mv", old, new)
        moves.append((old, new))
    if reports_root.exists() and not any(reports_root.iterdir()):
        reports_root.rmdir()
    return moves


def _merge_frontmatter(path: str, raw: str, extra: dict[str, object]) -> str:
    frontmatter, error, body = parse_page(raw)
    if error is not None or frontmatter is None:
        return raw
    fields: dict[str, object] = dict(frontmatter)
    for key, value in extra.items():
        if key not in fields or fields[key] in (None, "", []):
            fields[key] = value
    normalized = normalize_concept_frontmatter(path, serialize_frontmatter(fields) + body)
    return serialize_frontmatter(normalized.fields) + body


def _fix_report(path: str, store: LocalFSStore) -> bool:
    raw = store.read_text(path)
    frontmatter, error, body = parse_page(raw)
    if error is not None or frontmatter is None:
        return False
    ts = str(frontmatter.get("datetime") or frontmatter.get("timestamp") or now_rfc3339())
    if not ts.endswith("Z") and "+" in ts:
        ts = ts.replace("+00:00", "Z")
    fields: dict[str, object] = dict(frontmatter)
    fields.update(
        {
            "type": "report",
            "topic": "agentic-systems",
            "created": fields.get("created") or ts,
            "updated": fields.get("updated") or ts,
            "confidence": fields.get("confidence") or "medium",
            "sources": fields.get("sources") or [],
            "status": "active",
            "tags": fields.get("tags") or ["report", "agentic-systems"],
            "timestamp": fields.get("timestamp") or ts,
        }
    )
    # Guillotine reports: keep claim/verdict fields; drop invalid status.
    if "guillotine" in path:
        fields["tags"] = ["guillotine", "report", "agentic-systems"]
    new_text = serialize_frontmatter(fields) + body
    # Rewrite moved diff paths in body.
    new_text = new_text.replace("reports/guillotine/", "agentic-systems/reports/guillotine/")
    new_text = new_text.replace("reports/okf/", "agentic-systems/reports/okf/")
    if new_text != raw:
        store.write_text_atomic(path, new_text)
        return True
    return False


def _fix_source(path: str, store: LocalFSStore) -> bool:
    raw = store.read_text(path)
    frontmatter, error, body = parse_page(raw)
    if error is not None or frontmatter is None:
        return False
    ts = str(frontmatter.get("timestamp") or frontmatter.get("retrieved") or "2026-07-03T00:00:00Z")
    fields: dict[str, object] = dict(frontmatter)
    stem = Path(path).stem.replace("-", " ").title()
    fields.update(
        {
            **SOURCE_DEFAULTS,
            "description": fields.get("description") or f"Stored source chunk: {stem}.",
            "timestamp": fields.get("timestamp") or ts,
        }
    )
    new_text = serialize_frontmatter(fields) + body
    if new_text != raw:
        store.write_text_atomic(path, new_text)
        return True
    return False


def _fix_meta(path: str, store: LocalFSStore, type_value: str, tags: list[str]) -> bool:
    raw = store.read_text(path)
    frontmatter, error, body = parse_page(raw)
    if error is not None or frontmatter is None:
        return False
    fields: dict[str, object] = {**META_DEFAULTS, **frontmatter}
    fields["type"] = type_value
    fields["tags"] = tags
    new_text = serialize_frontmatter(fields) + body
    if new_text != raw:
        store.write_text_atomic(path, new_text)
        return True
    return False


def _fix_index(store: LocalFSStore) -> bool:
    raw = store.read_text("index.md")
    additions = """
### Reports

Operational and audit reports generated by knotica tooling.

- [[agentic-systems/reports/guillotine/2026-07-07-react-s-reasoning-acting-synergy-is-the-dominant]] — Guillotine dry-run on ReAct dominance claim.
- [[agentic-systems/reports/guillotine/2026-07-07-reasoning-only-systems-hallucinate-because-they-are-closed]] — Guillotine dry-run on reasoning-only vs acting-only claim.
- [[agentic-systems/reports/okf/2026-07-08-okf-repair]] — OKF compatibility repair report.
"""
    fixed = raw
    # Remove duplicate wikilink in agent-memory catalog line.
    fixed = re.sub(
        r"\[\[agentic-systems/agent-memory\]\] — \[\[agentic-systems/agent-memory\]\]",
        "[[agentic-systems/agent-memory]]",
        fixed,
    )
    if "### Reports" not in fixed:
        fixed = fixed.rstrip() + "\n" + additions
    if fixed != raw:
        store.write_text_atomic("index.md", fixed)
        return True
    return False


def main() -> int:
    store = LocalFSStore(VAULT)
    changed: list[str] = []

    for old, new in _relocate_reports():
        print(f"moved {old} -> {new}")

    # Refresh store paths after moves.
    store = LocalFSStore(VAULT)

    if _fix_index(store):
        changed.append("index.md")

    log_raw = store.read_text("log.md")
    log_fixed = canonicalize_log(log_raw)
    if log_fixed != log_raw:
        store.write_text_atomic("log.md", log_fixed)
        changed.append("log.md")

    for path in ("SCHEMA.md",):
        if _fix_meta(path, store, "schema", ["schema", "meta"]):
            changed.append(path)
    if _fix_meta("START_HERE.md", store, "guide", ["guide", "meta"]):
        changed.append("START_HERE.md")
    if _fix_meta("agentic-systems/SCHEMA.md", store, "schema", ["schema", "agentic-systems"]):
        changed.append("agentic-systems/SCHEMA.md")

    for path in iter_page_paths(store):
        if path.startswith("sources/agentic-systems/"):
            if _fix_source(path, store):
                changed.append(path)
        elif "/reports/" in path and path.endswith(".md"):
            if _fix_report(path, store):
                changed.append(path)

    for path in iter_page_paths(store):
        if not is_concept_file(path):
            continue
        if path.startswith("sources/") or "/reports/" in path:
            continue
        raw = store.read_text(path)
        normalized = normalize_concept_frontmatter(path, raw)
        if normalized.changed:
            _, _e, body = parse_page(raw)
            new_text = serialize_frontmatter(normalized.fields) + body
            if new_text != raw:
                store.write_text_atomic(path, new_text)
                changed.append(path)

    if changed:
        _git("add", "-A")
        _git(
            "commit",
            "-m",
            "knotica(okf): second-pass vault normalization for Knotica + OKF compatibility",
        )
        print(f"committed {len(changed)} file(s)")
    else:
        print("no changes needed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
