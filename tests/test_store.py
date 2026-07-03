"""Behavioral contract tests for ``knotica.store``.

Covers three behavioral areas of the storage boundary:

1. **Protocol conformance** — every ``VaultStore`` backend must satisfy the same
   observable behaviors (round-trip, overwrite, exists lifecycle, listing,
   deletion, missing-path errors). The ``store`` fixture is parametrized over
   ``STORE_KINDS`` so any future backend joins the suite by adding one entry to
   the registry in ``_make_store``.
2. **Atomicity** — ``write_text_atomic`` must commit via a same-directory
   temp+rename: no partial content is ever observable at the target path, and a
   simulated crash at the commit point leaves the previous state fully intact.
   These tests intercept ``os.replace`` / ``os.rename`` (the standard commit
   spelling for temp+rename, including ``pathlib.Path.replace``).
3. **Path safety** — no operation may escape the vault root; traversal and
   absolute-path attempts are rejected and provably write nothing outside.

Production imports are deferred into fixtures/test bodies so collection
succeeds while the implementation is still in flight (expected-red until the
store module lands).
"""

import os
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixtures: parametrizable store construction
# ---------------------------------------------------------------------------

# Registry of VaultStore backends under conformance test. Future backends:
# append a kind here and add a construction branch in _make_store.
STORE_KINDS = ["local-fs"]


def _make_store(kind: str, root: Path):
    if kind == "local-fs":
        from knotica.store.local import LocalFSStore

        return LocalFSStore(root)
    raise ValueError(f"unknown store kind: {kind!r}")


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    """A throwaway vault root, one level below tmp_path.

    The parent directory exists and is writable, so an unguarded traversal
    (``../…``) would *succeed* — keeping the path-safety assertions non-vacuous.
    """
    root = tmp_path / "vault"
    root.mkdir()
    return root


@pytest.fixture(params=STORE_KINDS)
def store(request, vault_root: Path):
    """Any VaultStore backend bound to a fresh vault root (conformance suite)."""
    return _make_store(request.param, vault_root)


@pytest.fixture
def local_store(vault_root: Path):
    """The filesystem backend specifically (atomicity-mechanism tests)."""
    from knotica.store.local import LocalFSStore

    return LocalFSStore(vault_root)


# ---------------------------------------------------------------------------
# Helpers: rename-commit interception
# ---------------------------------------------------------------------------


def _simulate_crash_at_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every rename-family commit fail, as if the process died mid-write.

    RuntimeError (not OSError) on purpose: shutil.move silently falls back to a
    non-atomic copy when rename raises OSError — a RuntimeError propagates
    through every spelling, so a copy-fallback cannot mask the injected crash.
    """

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated crash at the rename commit point")

    monkeypatch.setattr(os, "replace", _boom)
    monkeypatch.setattr(os, "rename", _boom)


def _record_renames(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Let renames proceed, recording (src, dst, full src content at call time)."""
    calls: list[dict] = []

    def _wrap(real):
        def _wrapper(src, dst, **kwargs):
            src_path = Path(os.fspath(src))
            content = None
            if src_path.is_file():
                content = src_path.read_text(encoding="utf-8")
            calls.append({"src": src_path, "dst": Path(os.fspath(dst)), "src_content": content})
            return real(src, dst, **kwargs)

        return _wrapper

    monkeypatch.setattr(os, "replace", _wrap(os.replace))
    monkeypatch.setattr(os, "rename", _wrap(os.rename))
    return calls


# ---------------------------------------------------------------------------
# Protocol conformance (any VaultStore backend)
# ---------------------------------------------------------------------------


def test_local_fs_store_satisfies_the_vault_store_protocol(vault_root: Path):
    from knotica.store import VaultStore
    from knotica.store.local import LocalFSStore

    impl = LocalFSStore(vault_root)
    for method in ("read_text", "write_text_atomic", "exists", "list_dir", "delete"):
        assert callable(getattr(impl, method, None)), f"missing protocol method: {method}"

    try:
        conforms = isinstance(impl, VaultStore)
    except TypeError:
        # Protocol is not runtime_checkable — the structural check above suffices.
        conforms = True
    assert conforms


def test_written_text_round_trips_through_read(store):
    text = "# Página\n\nRésumé of [[Agent Workflow Memory]] — naïve ✓ 🧠\n"

    store.write_text_atomic("page.md", text)

    assert store.read_text("page.md") == text


def test_overwriting_replaces_the_previous_content(store):
    store.write_text_atomic("page.md", "first version\n")

    store.write_text_atomic("page.md", "second version\n")

    assert store.read_text("page.md") == "second version\n"


def test_exists_tracks_the_write_and_delete_lifecycle(store):
    assert not store.exists("page.md")

    store.write_text_atomic("page.md", "alive\n")
    assert store.exists("page.md")

    store.delete("page.md")
    assert not store.exists("page.md")


def test_reading_a_missing_path_raises_file_not_found(store):
    with pytest.raises(FileNotFoundError):
        store.read_text("nowhere/absent.md")


def test_deleted_content_is_no_longer_readable(store):
    store.write_text_atomic("page.md", "soon gone\n")

    store.delete("page.md")

    with pytest.raises(FileNotFoundError):
        store.read_text("page.md")


def test_write_creates_missing_parent_directories(store):
    # The protocol exposes no mkdir: nested vault paths (sources/<topic>/<key>)
    # are only reachable if writes create their parents.
    store.write_text_atomic("sources/agentic-systems/wang2024awm.md", "provenance\n")

    assert store.read_text("sources/agentic-systems/wang2024awm.md") == "provenance\n"


def test_list_dir_reports_the_written_entries(store):
    store.write_text_atomic("notes/alpha.md", "a\n")
    store.write_text_atomic("notes/beta.md", "b\n")

    entries = store.list_dir("notes")

    # Backends may return names, relative paths, or Path objects — normalize to
    # final path components before asserting membership.
    names = {Path(os.fspath(entry)).name for entry in entries}
    assert {"alpha.md", "beta.md"} <= names


# ---------------------------------------------------------------------------
# Atomicity: temp+rename semantics (filesystem backend)
# ---------------------------------------------------------------------------


def test_interrupted_overwrite_preserves_the_previous_content(
    local_store, vault_root: Path, monkeypatch: pytest.MonkeyPatch
):
    local_store.write_text_atomic("page.md", "v1: the committed original\n")

    _simulate_crash_at_commit(monkeypatch)
    with pytest.raises(Exception):
        local_store.write_text_atomic("page.md", "v2: must never land\n")

    surviving = (vault_root / "page.md").read_text(encoding="utf-8")
    assert surviving == "v1: the committed original\n", (
        "a crash at the commit point exposed partial/new content at the target"
    )


def test_interrupted_new_file_write_leaves_no_file_behind(
    local_store, vault_root: Path, monkeypatch: pytest.MonkeyPatch
):
    _simulate_crash_at_commit(monkeypatch)

    with pytest.raises(Exception):
        local_store.write_text_atomic("fresh.md", "never committed\n")

    assert not (vault_root / "fresh.md").exists()


def test_commit_is_a_same_directory_rename_carrying_the_full_content(
    local_store, vault_root: Path, monkeypatch: pytest.MonkeyPatch
):
    text = "complete body\nline two\n"
    target = vault_root / "page.md"
    renames = _record_renames(monkeypatch)

    local_store.write_text_atomic("page.md", text)

    commits = [call for call in renames if call["dst"] == target]
    assert commits, (
        f"no rename targeted {target} — the write did not commit via temp+rename; "
        f"observed renames: {renames}"
    )
    commit = commits[-1]
    # Same-directory rename: the guarantee that the move is a single-filesystem
    # atomic operation (a cross-directory temp can silently degrade to a copy).
    assert commit["src"].parent == target.parent
    # The temp file already carried the complete content at the commit point —
    # readers can never observe a partially written target.
    assert commit["src_content"] == text
    assert target.read_text(encoding="utf-8") == text


def test_successful_write_leaves_only_the_target_in_its_directory(local_store, vault_root: Path):
    local_store.write_text_atomic("notes/solo.md", "content\n")

    leftovers = sorted(p.name for p in (vault_root / "notes").iterdir())
    assert leftovers == ["solo.md"], f"stray temp artifacts left beside the target: {leftovers}"


# ---------------------------------------------------------------------------
# Path safety: nothing escapes the vault root (any backend)
# ---------------------------------------------------------------------------


def test_write_escaping_the_vault_root_is_rejected(store, vault_root: Path):
    escaped = vault_root.parent / "escaped.md"

    with pytest.raises(Exception):
        store.write_text_atomic("../escaped.md", "outside the vault\n")

    assert not escaped.exists(), "a traversal write escaped the vault root"


def test_write_via_nested_traversal_is_rejected(store, vault_root: Path):
    escaped = vault_root.parent / "escaped-nested.md"

    with pytest.raises(Exception):
        store.write_text_atomic("notes/../../escaped-nested.md", "outside via nesting\n")

    assert not escaped.exists(), "a nested traversal write escaped the vault root"


def test_absolute_path_outside_the_vault_is_rejected(store, vault_root: Path):
    outside = vault_root.parent / "absolute-target.md"

    with pytest.raises(Exception):
        store.write_text_atomic(str(outside), "absolute escape\n")

    assert not outside.exists(), "an absolute-path write escaped the vault root"


def test_delete_escaping_the_vault_root_is_rejected(store, vault_root: Path):
    victim = vault_root.parent / "victim.md"
    victim.write_text("must survive\n", encoding="utf-8")

    with pytest.raises(Exception):
        store.delete("../victim.md")

    assert victim.read_text(encoding="utf-8") == "must survive\n"


def test_read_escaping_the_vault_root_is_rejected(store, vault_root: Path):
    secret = vault_root.parent / "secret.md"
    secret.write_text("outside content\n", encoding="utf-8")

    with pytest.raises(Exception):
        store.read_text("../secret.md")
