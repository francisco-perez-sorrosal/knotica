#!/usr/bin/env python3
"""One-off, deterministic reconciliation of historical eval manifests to schema v2.

Upgrades pre-substrate manifests (no ``manifest_schema_version``, no
``per_example[].id``/``.pages``) to schema v2 by:

1. Loading the topic's frozen golden set to build a ``question -> QARecord.id``
   map (question-text matching is legitimate here only because this is a
   one-time historical backfill, never a runtime join).
2. Re-cloning the corpus at each manifest's own recorded ``corpus_ref`` into a
   scratch directory (read-only use of the clone -- retrieval only, no mutation).
3. Re-running *only* retrieval (no synthesis, no LLM, no billing) for each
   ``per_example[i]["question"]`` through the same ``RipgrepBackend`` +
   ``retrieve_search_results`` + ``read_page`` path the runner uses, to recompute
   ``pages`` for that question against that historical corpus snapshot.
4. Rewriting each manifest in place: ``manifest_schema_version`` at the top
   level, ``id``/``pages`` on every ``per_example`` entry -- all other keys
   untouched, byte-for-byte.
5. Committing the change on the target vault's own git repo (scoped to exactly
   the rewritten manifest paths, via ``VaultVcs.commit_paths``).

Deliberate, explicit exception to clone-not-live-vault: ``--apply`` both reads
AND writes the live vault directly, scoped to this one-time reconciliation --
never the harness's normal write path (which always works on a throwaway clone
and returns results as branches for review). ``--vault-root`` exists only to
let ``--dry-run`` be exercised against a disposable clone; it defaults to the
live vault, which is the only supported target for ``--apply``.

Usage:
    uv run python scripts/reconcile_manifest_v2.py --topic agentic-systems --dry-run
    uv run python scripts/reconcile_manifest_v2.py --topic agentic-systems --apply
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from knotica.core.page import read_page, topic_relative_page_name
from knotica.core.records import QARecord
from knotica.core.vcs import VaultVcs
from knotica.evals import golden
from knotica.evals.runner import DEFAULT_MAX_PAGES
from knotica.search import RipgrepBackend
from knotica.search.retrieval import retrieve_search_results
from knotica.store.local import LocalFSStore

LIVE_VAULT_ROOT = Path.home() / "dev" / "data" / "knotica"
GENERATIONS = ("gen-2", "gen-3")
MANIFEST_SCHEMA_VERSION = 2


def _manifest_path(vault_root: Path, topic: str, generation: str) -> Path:
    return vault_root / topic / ".knotica" / "eval-runs" / generation / "manifest.json"


def _question_to_id(records: list[QARecord]) -> dict[str, str]:
    return {record.query: record.id for record in records}


def _retrieve_pages(topic: str, corpus_root: Path, question: str) -> list[str]:
    """Recompute the retrieval trace for one question against a frozen corpus clone."""
    store = LocalFSStore(corpus_root)
    backend = RipgrepBackend(corpus_root)
    results = retrieve_search_results(backend, topic, question, limit=DEFAULT_MAX_PAGES)
    pages = [read_page(store, topic, result.path) for result in results]
    return [topic_relative_page_name(topic, page.path) for page in pages]


def _corpus_sha(corpus_ref: str) -> str:
    # Manifests record ``corpus_ref`` as ``f"git:{sha}"`` (see harness._build_manifest).
    prefix = "git:"
    if not corpus_ref.startswith(prefix):
        raise ValueError(f"Unrecognized corpus_ref shape (expected 'git:<sha>'): {corpus_ref!r}")
    return corpus_ref.removeprefix(prefix)


def reconcile_generation(
    vault_root: Path,
    topic: str,
    generation: str,
    question_to_id: dict[str, str],
    scratch_root: Path,
) -> tuple[dict[str, object], bool]:
    """Return the (possibly upgraded) manifest payload and whether it changed."""
    path = _manifest_path(vault_root, topic, generation)
    manifest: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))

    if manifest.get("manifest_schema_version") == MANIFEST_SCHEMA_VERSION:
        return manifest, False

    corpus_root = scratch_root / generation
    sha = _corpus_sha(str(manifest["corpus_ref"]))
    VaultVcs(vault_root).clone_to(corpus_root, ref=sha)

    per_example = manifest["per_example"]
    assert isinstance(per_example, list)
    for entry in per_example:
        question = entry["question"]
        record_id = question_to_id.get(question)
        if record_id is None:
            raise ValueError(
                f"{generation}: no golden QARecord matches manifest question {question!r}"
            )
        entry["id"] = record_id
        entry["pages"] = _retrieve_pages(topic, corpus_root, question)

    manifest["manifest_schema_version"] = MANIFEST_SCHEMA_VERSION
    return manifest, True


def _report(topic: str, generation: str, manifest: dict[str, object]) -> None:
    per_example = manifest["per_example"]
    assert isinstance(per_example, list)
    ids_joined = sum(1 for entry in per_example if entry.get("id"))
    non_empty_traces = sum(1 for entry in per_example if entry.get("pages"))
    print(
        f"[{topic}/{generation}] manifest_schema_version={manifest.get('manifest_schema_version')} "
        f"ids_joined={ids_joined}/{len(per_example)} non_empty_traces={non_empty_traces}/{len(per_example)}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic", required=True)
    parser.add_argument(
        "--vault-root",
        type=Path,
        default=LIVE_VAULT_ROOT,
        help="Vault to operate on (defaults to the live vault). Override only for --dry-run "
        "against a disposable clone -- --apply against a non-live vault is not a supported use.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Report only; write nothing.")
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Write reconciled manifests to the target vault and commit.",
    )
    args = parser.parse_args()

    vault_root: Path = args.vault_root
    store = LocalFSStore(vault_root)
    records = golden.load(store, args.topic)
    question_to_id = _question_to_id(records)

    changed_generations: list[str] = []
    with tempfile.TemporaryDirectory(prefix="knotica-reconcile-") as tmp:
        scratch_root = Path(tmp)
        for generation in GENERATIONS:
            manifest, changed = reconcile_generation(
                vault_root, args.topic, generation, question_to_id, scratch_root
            )
            _report(args.topic, generation, manifest)
            if not changed:
                continue
            changed_generations.append(generation)
            if args.apply:
                path = _manifest_path(vault_root, args.topic, generation)
                path.write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )

    if not args.apply:
        print(f"[dry-run] would rewrite: {changed_generations or 'nothing (already v2)'}")
        return 0

    if not changed_generations:
        print("no changes needed")
        return 0

    vcs = VaultVcs(vault_root)
    rel_paths = [
        str(_manifest_path(vault_root, args.topic, gen).relative_to(vault_root))
        for gen in changed_generations
    ]
    sha = vcs.commit_paths(
        rel_paths,
        f"knotica(eval): {args.topic} — reconcile {'/'.join(changed_generations)} manifests to schema v2",
    )
    print(f"committed {sha}: {rel_paths}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
