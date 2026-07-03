"""Behavioral contract of schema resolution: root constitution ⊕ topic overlay.

Derived from the contracts, not from the implementation: the vault
constitution (``vault-template/SCHEMA.md`` — "topic overlays extend but never
contradict" the root), the pre-plan's settled hierarchical-schema decision
("divergence is earned: new topics start with an empty overlay inheriting
root"), and the interface design's resolved-schema resource (the merged
document is what ``knotica://schema/resolved/{topic}`` serves).

The pinned behaviors:

- a topic with no overlay resolves to the root constitution alone, and the
  merged document never leaves the consumer wondering whether a layer was
  silently dropped;
- with an overlay present, the merge is root-first / overlay-last, so the
  overlay's refinements read last and take precedence where the constitution
  delegates — and each layer's file is named in the merged output (provenance);
- a missing *topic* is a typed caller error; a missing root ``SCHEMA.md``
  means "not an initialized knotica vault" (the uniform not-configured
  contract); a missing or malformed overlay *frontmatter version* is never an
  error — flagging it is lint's job;
- resolution is per-call fresh (an edited schema is served on the next call)
  and byte-deterministic (same inputs → identical merged output — the
  evolvability substrate depends on stable bytes).
"""

from pathlib import Path

import pytest
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.page import TopicNotFoundError
from knotica.core.schema import (
    ROOT_SCHEMA_PATH,
    overlay_path,
    read_root_schema,
    read_topic_overlay,
    resolve_schema,
    validated_topic,
)
from knotica.store import LocalFSStore, VaultStore

SEED_TOPIC = "agentic-systems"


@pytest.fixture
def store(template_vault: Path) -> VaultStore:
    return LocalFSStore(template_vault)


def _add_overlayless_topic(vault: Path, name: str = "distributed-systems") -> str:
    """A brand-new topic directory: exists, but has earned no overlay yet."""
    (vault / name).mkdir()
    return name


# ---------------------------------------------------------------------------
# Reading the individual layers
# ---------------------------------------------------------------------------


def test_root_constitution_reads_with_its_version_and_frontmatter_stripped_body(
    store: VaultStore, template_vault: Path
):
    root = read_root_schema(store)
    assert root.path == ROOT_SCHEMA_PATH
    assert root.schema_version == 1
    assert root.body.startswith("# SCHEMA"), (
        "the body must be the markdown after the frontmatter block, stripped; "
        f"got a body starting with: {root.body[:40]!r}"
    )
    assert root.raw == (template_vault / ROOT_SCHEMA_PATH).read_text(encoding="utf-8")


def test_seed_topic_overlay_reads_with_its_own_version_and_body(store: VaultStore):
    overlay = read_topic_overlay(store, SEED_TOPIC)
    assert overlay is not None
    assert overlay.path == f"{SEED_TOPIC}/SCHEMA.md"
    assert overlay.schema_version == 1
    assert "ntity types" in overlay.body, (
        "the seed overlay defines the topic's entity types (pre-plan overlay contract)"
    )


def test_topic_without_an_overlay_file_reads_none_not_an_error(
    store: VaultStore, template_vault: Path
):
    """A missing overlay is a normal state — divergence is earned."""
    topic = _add_overlayless_topic(template_vault)
    assert read_topic_overlay(store, topic) is None


def test_missing_topic_raises_the_typed_topic_error(store: VaultStore):
    """A missing *topic* is a caller error, distinct from a missing overlay."""
    with pytest.raises(TopicNotFoundError) as excinfo:
        read_topic_overlay(store, "no-such-topic")
    assert excinfo.value.topic == "no-such-topic"
    assert "no-such-topic" in str(excinfo.value)

    with pytest.raises(TopicNotFoundError):
        resolve_schema(store, "no-such-topic")


# ---------------------------------------------------------------------------
# The merged resolved schema (what the resolved-schema resource serves)
# ---------------------------------------------------------------------------


def test_topic_without_overlay_resolves_to_the_root_constitution_alone(
    store: VaultStore, template_vault: Path
):
    topic = _add_overlayless_topic(template_vault)
    resolved = resolve_schema(store, topic)

    assert resolved.overlay is None
    assert resolved.root.body in resolved.merged
    assert topic in resolved.merged, "the merged document must name the topic it was resolved for"
    assert "overlay" in resolved.merged, (
        "the merged document must say explicitly that no overlay applies — "
        "a consumer never wonders whether a layer was silently dropped"
    )
    assert f"{topic}/SCHEMA.md" not in resolved.merged, (
        "no overlay file exists, so none may be claimed as provenance"
    )


def test_overlay_refinements_read_after_the_root_so_they_take_precedence(
    store: VaultStore,
):
    resolved = resolve_schema(store, SEED_TOPIC)

    assert resolved.overlay is not None
    root_at = resolved.merged.find(resolved.root.body)
    overlay_at = resolved.merged.find(resolved.overlay.body)
    assert root_at != -1, "the full root constitution body must be in the merge"
    assert overlay_at != -1, "the full overlay body must be in the merge"
    assert root_at < overlay_at, (
        "root constitution first, topic overlay second: the overlay extends "
        "and refines, so its text must read last to take precedence"
    )


def test_merged_document_names_each_layers_file_as_provenance(store: VaultStore):
    resolved = resolve_schema(store, SEED_TOPIC)

    assert resolved.root.path in resolved.merged
    assert resolved.overlay is not None
    assert resolved.overlay.path in resolved.merged
    assert "schema_version" in resolved.merged, (
        "provenance headers carry each layer's schema_version"
    )


def test_effective_schema_version_is_the_root_constitutions(store: VaultStore):
    resolved = resolve_schema(store, SEED_TOPIC)
    assert resolved.schema_version == 1
    assert resolved.schema_version == resolved.root.schema_version


@pytest.mark.parametrize(
    "overlay_content",
    [
        pytest.param(
            "# Overlay without any frontmatter\n\nRefinement text.\n", id="no-frontmatter"
        ),
        pytest.param(
            "---\n: not [valid yaml\n---\n\nRefinement text.\n", id="malformed-frontmatter"
        ),
        pytest.param("---\ntags: [a]\n---\n\nRefinement text.\n", id="version-key-absent"),
        pytest.param(
            "---\nschema_version: one\n---\n\nRefinement text.\n", id="version-not-an-integer"
        ),
    ],
)
def test_layer_without_a_parseable_version_resolves_with_none_instead_of_failing(
    store: VaultStore, template_vault: Path, overlay_content: str
):
    """Resolution never fails on a malformed layer — flagging it is lint's job."""
    (template_vault / SEED_TOPIC / "SCHEMA.md").write_text(overlay_content, encoding="utf-8")

    resolved = resolve_schema(store, SEED_TOPIC)
    assert resolved.overlay is not None
    assert resolved.overlay.schema_version is None
    assert "Refinement text." in resolved.merged, (
        "the layer's content must still be served even when its version is unreadable"
    )


def test_vault_without_a_root_constitution_reports_not_configured(
    store: VaultStore, template_vault: Path
):
    """No root SCHEMA.md means "not an initialized knotica vault" — the same
    uniform contract config resolution collapses to, not a bare file error."""
    (template_vault / ROOT_SCHEMA_PATH).unlink()

    with pytest.raises(KnoticaError) as excinfo:
        read_root_schema(store)
    assert excinfo.value.code == ErrorCode.NOT_CONFIGURED

    with pytest.raises(KnoticaError) as resolve_info:
        resolve_schema(store, SEED_TOPIC)
    assert resolve_info.value.code == ErrorCode.NOT_CONFIGURED


# ---------------------------------------------------------------------------
# Determinism and per-call freshness
# ---------------------------------------------------------------------------


def test_resolution_is_byte_identical_across_calls_and_store_instances(
    template_vault: Path,
):
    """Same inputs → byte-identical merged output: the DSPy/SIA evolvability
    substrate and the resolved-schema resource both depend on stable bytes."""
    first = resolve_schema(LocalFSStore(template_vault), SEED_TOPIC).merged
    second = resolve_schema(LocalFSStore(template_vault), SEED_TOPIC).merged
    assert first == second

    overlayless = _add_overlayless_topic(template_vault)
    store = LocalFSStore(template_vault)
    assert resolve_schema(store, overlayless).merged == resolve_schema(store, overlayless).merged


def test_overlay_edited_between_calls_is_served_fresh(store: VaultStore, template_vault: Path):
    """Nothing is cached: a schema evolved after boot takes effect next call."""
    before = resolve_schema(store, SEED_TOPIC).merged

    overlay_file = template_vault / SEED_TOPIC / "SCHEMA.md"
    overlay_file.write_text(
        overlay_file.read_text(encoding="utf-8") + "\n## Earned refinement\n\nNew rule.\n",
        encoding="utf-8",
    )

    after = resolve_schema(store, SEED_TOPIC).merged
    assert "Earned refinement" in after
    assert before != after


# ---------------------------------------------------------------------------
# Topic-shape validation (traversal safety before any vault access)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_topic",
    ["", "   ", "a/b", "a\\b", ".knotica", ".git", "..", "../escape"],
)
def test_non_topic_shaped_names_are_rejected_before_touching_the_vault(
    store: VaultStore, bad_topic: str
):
    """Topics are bare top-level directory names — separators and dot-prefixed
    names never reach the store, so no path can be built out of the vault."""
    with pytest.raises(ValueError):
        resolve_schema(store, bad_topic)
    with pytest.raises(ValueError):
        overlay_path(bad_topic)


def test_topic_names_are_used_stripped_but_otherwise_verbatim(store: VaultStore):
    assert validated_topic("  agentic-systems  ") == SEED_TOPIC
    resolved = resolve_schema(store, "  agentic-systems  ")
    assert resolved.topic == SEED_TOPIC
