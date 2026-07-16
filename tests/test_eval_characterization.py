"""Characterization: the one-writer seam works unchanged against a git *clone*.

The eval harness is designed to run entirely on a throwaway ``git clone`` of a
vault -- "loops always work on a clone, never the live vault" -- and to persist
its result through the same single mutation path every other operation uses,
:class:`~knotica.core.transaction.VaultTransaction`. That reuse is a *design
hypothesis*: no evaluator code exists yet, and nothing has ever driven a
``VaultTransaction`` against a clone. This module pins that hypothesis before
any evaluator is built on top of it.

The hypothesis, stated as observable behavior:

1. A ``VaultTransaction(clone_store, clone_root, "eval", topic, title)`` that
   appends a ``metrics.jsonl`` line lands **exactly one** ``knotica(eval): …``
   commit on the clone, in the frozen commit grammar, with a matching ``log.md``
   entry and a clean work tree.
2. The **source** vault it was cloned from is byte-identical afterwards -- same
   ``HEAD``, same commit count, clean tree, no leaked file. The evaluation has
   zero side effect on live content.
3. The appended line round-trips through :meth:`MetricsRecord.from_json_line`,
   so the on-disk record is exactly the record the harness will build.
4. The transaction needs no knotica config present -- it takes an explicit
   store + root and resolves nothing from ``~/.config/knotica``.
5. The mutation flock lives on the clone as untracked runtime state, never
   swept into the eval commit.

This is a solo gating step -- there is no paired implementer. A green result
therefore *confirms* the hypothesis (the pipeline proceeds); a red result fires
the metrics-write-path reversal trigger and halts execution for re-architecture.
The behaviors are pinned against real git repositories; nothing about git or the
transaction is mocked.
"""

from dataclasses import dataclass
from pathlib import Path

from knotica.core.records import MetricsComponents, MetricsRecord
from knotica.core.transaction import VaultTransaction
from knotica.store import LocalFSStore
from support.vault import (
    git_commit_count,
    git_commit_subjects,
    git_head_sha,
    git_is_ignored,
    git_status_porcelain,
    parse_knotica_commit,
    parse_log_entries,
    run_git,
)

#: The topic the template vault ships (its ``.knotica/`` is committed state).
SEED_TOPIC = "agentic-systems"
#: Where the eval harness appends its per-generation history record.
METRICS_PATH = f"{SEED_TOPIC}/.knotica/metrics.jsonl"
#: The mutation guard's in-vault flock; gitignored via the template.
LOCK_PATH = ".knotica/locks/vault.lock"
#: Title slot of this characterization's eval transaction.
EVAL_TITLE = "characterization"
#: The exact commit subject the frozen grammar renders for the run above.
EXPECTED_SUBJECT = f"knotica(eval): {SEED_TOPIC} — {EVAL_TITLE}"


@dataclass(frozen=True)
class CloneEval:
    """The outcome of running one eval transaction against a fresh clone."""

    clone: Path
    record: MetricsRecord
    commits_before: int
    changed: bool


def _build_metrics_record(corpus_sha: str) -> MetricsRecord:
    """A minimal, valid ``MetricsRecord`` with a fixed (deterministic) timestamp.

    ``corpus_ref`` carries the clone's own HEAD as ``git:<sha>`` -- the exact
    snapshot-pinning shape the harness records for the evaluated corpus.
    """
    return MetricsRecord(
        topic=SEED_TOPIC,
        timestamp="2026-07-15T00:00:00+00:00",
        generation=1,
        harness_version="characterization-test",
        scalar=0.5,
        components=MetricsComponents(
            qa_accuracy=0.6,
            citation_validity=1.0,
            lint_violations=0.0,
            token_cost=0.4,
        ),
        n_examples=3,
        corpus_ref=f"git:{corpus_sha}",
        artifact_ref=None,
    )


def _clone_and_run_eval(source: Path, work_root: Path) -> CloneEval:
    """Clone ``source`` and append one ``metrics.jsonl`` line via a transaction.

    Mirrors the ``curate_example`` append pattern (read-nothing / write one
    JSONL line) but against a clone rather than the live vault. A fresh clone
    does *not* inherit committer identity from the source's local git config, so
    identity is set locally on the clone -- this keeps the commit hermetic
    (independent of the developer's global git identity) and mirrors exactly how
    the ``vault_seed`` fixture prepares a committable repo.
    """
    clone = work_root / "clone"
    run_git(work_root, "clone", str(source), str(clone))
    run_git(clone, "config", "user.name", "knotica-tests")
    run_git(clone, "config", "user.email", "tests@knotica.invalid")
    run_git(clone, "config", "commit.gpgsign", "false")

    commits_before = git_commit_count(clone)
    record = _build_metrics_record(corpus_sha=git_head_sha(clone))
    line = record.to_json_line() + "\n"

    with VaultTransaction(LocalFSStore(clone), clone, "eval", SEED_TOPIC, EVAL_TITLE) as txn:
        txn.write(METRICS_PATH, line)

    return CloneEval(
        clone=clone, record=record, commits_before=commits_before, changed=txn.result.changed
    )


# ---------------------------------------------------------------------------
# The clone gains exactly one eval commit
# ---------------------------------------------------------------------------


def test_eval_transaction_on_a_clone_lands_exactly_one_eval_commit(
    template_vault: Path, tmp_path: Path, isolated_home: Path
) -> None:
    outcome = _clone_and_run_eval(template_vault, tmp_path)

    assert outcome.changed is True, "appending a new metrics line must be an effective mutation"
    assert git_commit_count(outcome.clone) == outcome.commits_before + 1, (
        "one eval run is exactly one commit on the clone"
    )

    subject = git_commit_subjects(outcome.clone)[0]
    assert subject == EXPECTED_SUBJECT, (
        "the eval commit subject must be byte-exact (em-dash included)"
    )
    assert parse_knotica_commit(subject) == {
        "op": "eval",
        "topic": SEED_TOPIC,
        "title": EVAL_TITLE,
    }, "the subject must parse under the frozen commit grammar with op 'eval'"

    assert git_status_porcelain(outcome.clone) == "", "the clone tree is clean after the commit"
    assert (outcome.clone / METRICS_PATH).exists(), "the metrics line was written on the clone"


def test_eval_transaction_appends_exactly_one_log_entry_on_the_clone(
    template_vault: Path, tmp_path: Path, isolated_home: Path
) -> None:
    entries_before = parse_log_entries((template_vault / "log.md").read_text(encoding="utf-8"))

    outcome = _clone_and_run_eval(template_vault, tmp_path)

    log_entries = parse_log_entries((outcome.clone / "log.md").read_text(encoding="utf-8"))
    assert len(log_entries) == len(entries_before) + 1, (
        "one eval run appends exactly one log entry (clone started with the source's log)"
    )
    newest = log_entries[-1]
    assert newest.op == "eval", "the newest log entry records the eval op"
    assert newest.topic == SEED_TOPIC, "the newest log entry records the evaluated topic"
    assert newest.title == EVAL_TITLE, "the newest log entry carries the transaction title"


# ---------------------------------------------------------------------------
# The source vault is untouched -- the eval has zero side effect on live content
# ---------------------------------------------------------------------------


def test_the_source_vault_is_byte_identical_after_an_eval_on_its_clone(
    template_vault: Path, tmp_path: Path, isolated_home: Path
) -> None:
    head_before = git_head_sha(template_vault)
    count_before = git_commit_count(template_vault)
    status_before = git_status_porcelain(template_vault)

    _clone_and_run_eval(template_vault, tmp_path)

    assert git_head_sha(template_vault) == head_before, "the source HEAD must not move"
    assert git_commit_count(template_vault) == count_before, "the source gains no commit"
    assert git_status_porcelain(template_vault) == status_before, "the source tree is unchanged"
    assert not (template_vault / METRICS_PATH).exists(), (
        "the metrics write landed on the clone only, never on the source"
    )


# ---------------------------------------------------------------------------
# The appended record round-trips through the frozen parser
# ---------------------------------------------------------------------------


def test_the_appended_metrics_line_round_trips_through_from_json_line(
    template_vault: Path, tmp_path: Path, isolated_home: Path
) -> None:
    outcome = _clone_and_run_eval(template_vault, tmp_path)

    stored = (outcome.clone / METRICS_PATH).read_text(encoding="utf-8")
    lines = [line for line in stored.splitlines() if line.strip()]
    assert len(lines) == 1, "exactly one metrics record was appended"

    parsed = MetricsRecord.from_json_line(lines[0])
    assert parsed == outcome.record, "the on-disk record is exactly the record the harness built"
    assert parsed.corpus_ref.startswith("git:"), "corpus_ref pins the clone snapshot as git:<sha>"


# ---------------------------------------------------------------------------
# Config independence: the transaction resolves nothing from ~/.config/knotica
# ---------------------------------------------------------------------------


def test_the_eval_transaction_needs_no_knotica_config_present(
    template_vault: Path, tmp_path: Path, unconfigured_env: Path
) -> None:
    # `unconfigured_env` redirects HOME/XDG into tmp and guarantees no config.toml
    # is discoverable; the transaction still commits, so it read no vault config.
    assert not (unconfigured_env / ".config" / "knotica" / "config.toml").exists(), (
        "precondition: no knotica config exists, so success can only be config-independent"
    )

    outcome = _clone_and_run_eval(template_vault, tmp_path)

    assert outcome.changed is True, "the transaction committed with no knotica config resolvable"
    assert git_commit_subjects(outcome.clone)[0] == EXPECTED_SUBJECT


# ---------------------------------------------------------------------------
# The mutation flock on the clone is untracked runtime state
# ---------------------------------------------------------------------------


def test_the_clone_lock_file_is_git_ignored_runtime_state(
    template_vault: Path, tmp_path: Path, isolated_home: Path
) -> None:
    outcome = _clone_and_run_eval(template_vault, tmp_path)

    lock_file = outcome.clone / LOCK_PATH
    assert lock_file.exists(), "the flock guard created its lock file on the clone"
    assert git_is_ignored(outcome.clone, LOCK_PATH), (
        "the clone lock file is runtime state git must ignore, never eval-committed content"
    )
    assert LOCK_PATH not in git_status_porcelain(outcome.clone), (
        "the lock file is not surfaced as an untracked change on the clone"
    )
    assert not (template_vault / LOCK_PATH).exists(), (
        "the source vault never gains a lock file from an eval run on its clone"
    )
