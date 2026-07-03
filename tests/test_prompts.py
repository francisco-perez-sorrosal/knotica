"""Behavioral contract of operation-prompt resolution — the one shared resolver.

Derived from the contracts, not from the implementation: the pre-plan's
first-use section (four locked operations; names register statically, bodies
resolve lazily from the vault per invocation; prompt resolution mirrors schema
resolution — root defaults at ``.knotica/prompts/``, topic overrides once
divergence is earned; graceful unconfigured boot) and the interface design's
prompt contracts (§2: protocol outlines, the verbatim topic-inference policy
block, the body-requirements checklist).

The pinned behaviors:

- root default served when the topic has no override; a topic override wins
  the moment it exists; empty / non-topic-shaped / absent topics fall through
  to the root default without erroring (the prompt fetch must never block the
  very flow — topic inference, ``create_topic`` — that resolves the topic);
- a missing *root* prompt file in an otherwise-ready vault is a typed
  not-configured error, never a silent fallback body; an *unconfigured*
  invocation instead serves the setup-guidance body (graceful boot at the
  prompt layer), and configuration written afterwards takes effect with no
  restart;
- bodies resolve per call (an evolved prompt is served on the next
  invocation) and byte-identically through every entry point — the vault
  ``prompts/`` files are simultaneously the UX surface and the DSPy/SIA
  evolvable substrate, so both surfaces must serve the same stable bytes;
- the shipped template bodies satisfy the body-requirements checklist:
  the topic-inference policy block verbatim, curation solicitation where the
  flywheel needs it, and an explicit pointer at the resolved-schema resource.
"""

import re
from pathlib import Path

import pytest
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.prompts import (
    OPERATIONS,
    get_prompt,
    override_prompt_path,
    resolve_prompt,
    root_prompt_path,
)
from knotica.store import LocalFSStore, VaultStore
from test_errors import assert_names_both_setup_paths

SEED_TOPIC = "agentic-systems"

#: The locked topic-inference policy block, identical in ingest/query/lint
#: bodies (single source of truth for the settled decision). Bodies wrap it in
#: a blockquote, so presence is asserted whitespace-normalized, not raw.
TOPIC_INFERENCE_POLICY = (
    "Call `list_topics`. If the caller passed an explicit `topic`, use it"
    " (override always wins). Otherwise infer: if the material clearly matches"
    " one existing topic, auto-place there; if it is ambiguous across topics or"
    " warrants a new topic, ask the user, and on confirmation call"
    " `create_topic`. Always pass the resolved topic explicitly to every tool"
    " — the server holds no active-topic state."
)


@pytest.fixture
def store(template_vault: Path) -> VaultStore:
    return LocalFSStore(template_vault)


def _normalized(text: str) -> str:
    """Strip blockquote markers and collapse whitespace for verbatim checks."""
    unquoted = re.sub(r"^\s*>\s?", "", text, flags=re.MULTILINE)
    return re.sub(r"\s+", " ", unquoted).strip()


def _write_override(vault: Path, operation: str, body: str) -> Path:
    path = vault / override_prompt_path(operation, SEED_TOPIC)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# The static surface and the resolution mirror
# ---------------------------------------------------------------------------


def test_the_prompt_surface_is_exactly_the_four_locked_operations():
    assert set(OPERATIONS) == {"ingest", "query", "lint", "curate"}
    assert len(OPERATIONS) == 4


def test_prompt_paths_mirror_schema_resolution_shape():
    """Defaults at the vault root, overrides inside the topic directory — the
    same root-plus-topic-overlay shape schema resolution uses."""
    assert root_prompt_path("ingest") == ".knotica/prompts/ingest.md"
    assert override_prompt_path("ingest", SEED_TOPIC) == f"{SEED_TOPIC}/.knotica/prompts/ingest.md"


# ---------------------------------------------------------------------------
# Precedence: root default vs earned topic override
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("operation", OPERATIONS)
def test_root_default_is_served_when_the_topic_has_no_override(
    store: VaultStore, template_vault: Path, operation: str
):
    """The template's seed topic ships no prompt overrides — divergence is
    earned — so every operation resolves to the root default, byte-identical
    to the substrate file."""
    resolved = resolve_prompt(store, operation, SEED_TOPIC)

    assert resolved.configured is True
    assert resolved.topic_override is False
    assert resolved.source_path == root_prompt_path(operation)
    assert resolved.body == (template_vault / root_prompt_path(operation)).read_text(
        encoding="utf-8"
    )
    assert resolved.operation == operation
    assert resolved.topic == SEED_TOPIC


def test_topic_override_wins_once_divergence_is_earned(store: VaultStore, template_vault: Path):
    override_body = "# Query — evolved for this topic\n\nRefined protocol.\n"
    _write_override(template_vault, "query", override_body)

    resolved = resolve_prompt(store, "query", SEED_TOPIC)

    assert resolved.topic_override is True
    assert resolved.source_path == override_prompt_path("query", SEED_TOPIC)
    assert resolved.body == override_body


def test_an_override_for_one_operation_leaves_the_others_on_root_defaults(
    store: VaultStore, template_vault: Path
):
    _write_override(template_vault, "query", "# Evolved query\n")

    ingest = resolve_prompt(store, "ingest", SEED_TOPIC)
    assert ingest.topic_override is False
    assert ingest.source_path == root_prompt_path("ingest")


@pytest.mark.parametrize(
    "topic",
    ["", "   ", "../evil", ".knotica", "a/b", "brand-new-topic"],
)
def test_unresolvable_topics_fall_through_to_the_root_default(store: VaultStore, topic: str):
    """At prompt time the topic is unvalidated user intent: empty,
    non-topic-shaped, or not-yet-created topics must serve the root default —
    erroring here would block the very flow (topic inference, create_topic)
    the body itself drives."""
    resolved = resolve_prompt(store, "ingest", topic)

    assert resolved.configured is True
    assert resolved.topic_override is False
    assert resolved.source_path == root_prompt_path("ingest")


def test_unknown_operation_is_rejected_naming_the_valid_ones(store: VaultStore):
    """Only the four locked names exist; misuse is a caller error even when
    unconfigured — never masked as guidance."""
    with pytest.raises(ValueError, match="ingest"):
        resolve_prompt(store, "deploy")
    with pytest.raises(ValueError, match="ingest"):
        get_prompt("deploy")


# ---------------------------------------------------------------------------
# Malformed vault vs unconfigured: two different degradations
# ---------------------------------------------------------------------------


def test_missing_root_prompt_file_is_a_typed_error_not_a_silent_fallback(
    store: VaultStore, template_vault: Path
):
    (template_vault / root_prompt_path("lint")).unlink()

    with pytest.raises(KnoticaError) as excinfo:
        resolve_prompt(store, "lint")

    error = excinfo.value
    assert error.code == ErrorCode.NOT_CONFIGURED
    assert root_prompt_path("lint") in error.message, (
        "the error must name the missing prompt file so the fix is actionable"
    )
    assert error.fix, "a restore path (re-init / copy from template) must be offered"


def test_malformed_vault_error_propagates_through_the_shared_entry_point(
    vault_config: Path, template_vault: Path
):
    """A vault that resolves READY but lacks its prompt defaults must surface
    as a failure through get_prompt too — not be papered over with guidance."""
    (template_vault / root_prompt_path("query")).unlink()

    with pytest.raises(KnoticaError) as excinfo:
        get_prompt("query", config_path=vault_config)
    assert excinfo.value.code == ErrorCode.NOT_CONFIGURED


def test_unconfigured_invocation_serves_setup_guidance_instead_of_failing(
    unconfigured_env: Path, tmp_path: Path
):
    """Graceful boot at the prompt layer: no config anywhere → the invocation
    still returns actionable content, and promises that setup needs no restart."""
    resolved = get_prompt("ingest", config_path=tmp_path / "absent" / "config.toml")

    assert resolved.configured is False
    assert resolved.source_path is None
    assert "ingest" in resolved.body
    assert "restart" in resolved.body, (
        "the guidance must tell the caller setup takes effect without a restart"
    )
    assert_names_both_setup_paths(resolved.body)


def test_configuration_written_after_a_guidance_response_takes_effect_without_restart(
    isolated_home: Path, template_vault: Path, tmp_path: Path
):
    """The graceful-boot story end to end: guidance first, setup writes the
    config, the next invocation serves the real body — per-call resolution."""
    config_path = tmp_path / "late-config" / "config.toml"

    first = get_prompt("ingest", config_path=config_path)
    assert first.configured is False

    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        f'schema_version = 1\ndefault_vault = "main"\n\n[vaults.main]\npath = "{template_vault}"\n',
        encoding="utf-8",
    )

    second = get_prompt("ingest", config_path=config_path)
    assert second.configured is True
    assert second.source_path == root_prompt_path("ingest")


# ---------------------------------------------------------------------------
# Per-call freshness and byte-identity across entry points
# ---------------------------------------------------------------------------


def test_prompt_edited_between_calls_is_served_fresh(vault_config: Path, template_vault: Path):
    """Nothing is cached: a prompt evolved after boot (DSPy/SIA writing the
    substrate) is served on the very next invocation."""
    before = get_prompt("query", config_path=vault_config)

    evolved_body = "# Query — freshly evolved\n\nServe me next call.\n"
    (template_vault / root_prompt_path("query")).write_text(evolved_body, encoding="utf-8")

    after = get_prompt("query", config_path=vault_config)
    assert after.body == evolved_body
    assert after.body != before.body


@pytest.mark.parametrize("operation", OPERATIONS)
def test_both_entry_points_serve_byte_identical_bodies(
    vault_config: Path, template_vault: Path, operation: str
):
    """One resolver behind both surfaces: the MCP prompt handler and the CLI
    renderer must serve the same bytes as the substrate file, every call."""
    via_entry_point = get_prompt(operation, config_path=vault_config)
    via_resolver = resolve_prompt(LocalFSStore(template_vault), operation)
    substrate = (template_vault / root_prompt_path(operation)).read_text(encoding="utf-8")

    assert via_entry_point.body == substrate
    assert via_resolver.body == substrate
    assert get_prompt(operation, config_path=vault_config).body == substrate, (
        "repeat invocation with the same inputs must be byte-identical"
    )


def test_an_earned_override_is_served_through_the_shared_entry_point(
    vault_config: Path, template_vault: Path
):
    override_body = "# Ingest — topic-evolved\n"
    _write_override(template_vault, "ingest", override_body)

    resolved = get_prompt("ingest", SEED_TOPIC, config_path=vault_config)

    assert resolved.topic_override is True
    assert resolved.body == override_body


# ---------------------------------------------------------------------------
# Body-requirements checklist against the shipped template bodies
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("operation", ["ingest", "query", "lint"])
def test_operation_bodies_embed_the_topic_inference_policy_verbatim(
    store: VaultStore, operation: str
):
    """The locked policy block is the single source of truth for topic
    inference and must appear verbatim in every topic-taking body."""
    body = resolve_prompt(store, operation).body
    assert TOPIC_INFERENCE_POLICY in _normalized(body), (
        f"the {operation} body must carry the topic-inference policy verbatim "
        "(modulo line wrapping); it drives auto-place/ask/create_topic and the "
        "explicit-topic stateless-server rule"
    )


@pytest.mark.parametrize("operation", ["ingest", "query"])
def test_flywheel_operations_solicit_curation(store: VaultStore, operation: str):
    """The flywheel will not fill itself: ingest and query must end by
    offering to save the interaction as a curated example."""
    assert "curate_example" in resolve_prompt(store, operation).body


@pytest.mark.parametrize("operation", ["ingest", "query", "lint"])
def test_operation_bodies_point_at_the_resolved_schema_resource(store: VaultStore, operation: str):
    """Resources are not auto-loaded; each schema-guided body must direct the
    client at the merged resolved-schema resource explicitly."""
    assert "knotica://schema/resolved/" in resolve_prompt(store, operation).body
