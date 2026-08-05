"""Behavioral spec for topic identity against the store.

``core.topics`` holds the two predicates that decide whether a name is a topic
by reading the vault. They were four near-copies across ``core``, ``service``,
and ``mcp_server`` before consolidation, and two of those copies disagreed about
a name that does not exist -- so this suite pins the reconciled contract:

- **``is_topic`` is total.** Any string is a legal argument. A name that is
  absent, or present but a file, is simply not a topic; the predicate never
  raises. This is the behavior the divergent copies did *not* share, and it is
  what makes the function safe on names that did not come from ``list_dir("")``.
- **Reserved and hidden names are never topics.** The vault-root family
  directories (``sources``, ``notes``) and the constitution's reserved files are
  excluded even when they exist as real directories, and so is anything
  dot-prefixed -- that exclusion is what keeps personal notes out of the scored
  topic corpus.
- **``require_topic`` is a boundary assertion, not a filter.** It normalizes
  surrounding whitespace and slashes, then raises ``TopicNotFoundError`` for
  empty, nested, absent, and non-directory input rather than returning a bool.
  It guards a caller-supplied topic *argument*, so it rejects nested paths --
  where ``is_topic`` rejects reserved names instead.
"""

from pathlib import Path

import pytest

from knotica.core.page import TopicNotFoundError
from knotica.core.topics import is_topic, require_topic
from knotica.store import LocalFSStore


@pytest.fixture
def store(tmp_path: Path) -> LocalFSStore:
    """An empty vault store rooted at a fresh temp directory."""
    return LocalFSStore(tmp_path)


def test_is_topic_accepts_an_ordinary_directory(tmp_path: Path, store: LocalFSStore) -> None:
    (tmp_path / "physics").mkdir()

    assert is_topic(store, "physics") is True


def test_is_topic_returns_false_for_a_name_that_does_not_exist(store: LocalFSStore) -> None:
    # The reconciliation point: two of the pre-consolidation copies let
    # FileNotFoundError escape here instead of answering the question.
    assert is_topic(store, "never-created") is False


def test_is_topic_returns_false_for_a_file(tmp_path: Path, store: LocalFSStore) -> None:
    (tmp_path / "loose.md").write_text("", encoding="utf-8")

    assert is_topic(store, "loose.md") is False


def test_is_topic_returns_false_for_a_hidden_directory(tmp_path: Path, store: LocalFSStore) -> None:
    (tmp_path / ".hidden").mkdir()

    assert is_topic(store, ".hidden") is False


def test_is_topic_returns_false_for_the_notes_family_directory(
    tmp_path: Path, store: LocalFSStore
) -> None:
    (tmp_path / "notes").mkdir()

    assert is_topic(store, "notes") is False


def test_is_topic_returns_false_for_the_sources_family_directory(
    tmp_path: Path, store: LocalFSStore
) -> None:
    (tmp_path / "sources").mkdir()

    assert is_topic(store, "sources") is False


def test_require_topic_returns_the_normalized_name(tmp_path: Path, store: LocalFSStore) -> None:
    (tmp_path / "physics").mkdir()

    assert require_topic(store, "  /physics/  ") == "physics"


def test_require_topic_rejects_an_empty_name(store: LocalFSStore) -> None:
    with pytest.raises(TopicNotFoundError):
        require_topic(store, "   ")


def test_require_topic_rejects_a_nested_path(tmp_path: Path, store: LocalFSStore) -> None:
    (tmp_path / "physics" / "optics").mkdir(parents=True)

    with pytest.raises(TopicNotFoundError):
        require_topic(store, "physics/optics")


def test_require_topic_rejects_a_name_that_does_not_exist(store: LocalFSStore) -> None:
    with pytest.raises(TopicNotFoundError):
        require_topic(store, "never-created")


def test_require_topic_rejects_a_file(tmp_path: Path, store: LocalFSStore) -> None:
    (tmp_path / "loose.md").write_text("", encoding="utf-8")

    with pytest.raises(TopicNotFoundError):
        require_topic(store, "loose.md")
