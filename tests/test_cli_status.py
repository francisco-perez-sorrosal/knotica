"""Behavioral tests for ``knotica status`` — deterministic vault counts.

``status`` reports mechanical counts (pages per topic, curated examples, unpushed
commits) as a width-aware table or ``--json`` (INTERFACE_DESIGN §4.2). These
tests pin the numbers against ground truth *computed from the fixture vault*
(never a hardcoded literal that could drift): the count the command reports must
equal what is actually on disk, and must track a real change (adding a page bumps
the count), so the assertion is non-vacuous. They also pin the surface contract:
``--topic`` scopes the counts, ``--json`` is stable machine output, and an
unconfigured vault exits 3 with the uniform not-configured remediation.

RED until the ``status`` command lands: the registered stub raises
``NotImplementedError`` (empty stdout, exit 1), so every assertion fails until
Step-38 impl commits.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from knotica.cli.common import UNCONFIGURED_MESSAGE
from support.vault import run_git

SEED_TOPIC = "agentic-systems"


def _cli(*args: str) -> list[str]:
    console = Path(sys.executable).with_name("knotica")
    if console.exists():
        return [str(console), *args]
    return [
        sys.executable,
        "-c",
        "import sys; from knotica.cli import main; sys.exit(main())",
        *args,
    ]


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["NO_COLOR"] = "1"
    return subprocess.run(_cli(*args), capture_output=True, text=True, env=env, timeout=60)


def _content_page_count(vault: Path, topic: str) -> int:
    """Ground truth: ``.md`` pages under a topic, excluding its ``SCHEMA.md`` overlay."""
    topic_dir = vault / topic
    return sum(
        1
        for path in topic_dir.iterdir()
        if path.is_file() and path.suffix == ".md" and path.name != "SCHEMA.md"
    )


def _add_content_page(vault: Path, topic: str, name: str) -> None:
    (vault / topic / name).write_text("# extra page\n", encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", f"test: add {name}")


def _string_values(obj: object) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for value in obj.values() for s in _string_values(value)]
    if isinstance(obj, list):
        return [s for item in obj for s in _string_values(item)]
    return []


def _int_values(obj: object) -> list[int]:
    if isinstance(obj, bool):
        return []
    if isinstance(obj, int):
        return [obj]
    if isinstance(obj, dict):
        return [n for value in obj.values() for n in _int_values(value)]
    if isinstance(obj, list):
        return [n for item in obj for n in _int_values(item)]
    return []


# ---------------------------------------------------------------------------
# Counts equal fixture ground truth (and track a real change)
# ---------------------------------------------------------------------------


def test_page_count_equals_fixture_ground_truth(vault_config: Path, template_vault: Path):
    expected = _content_page_count(template_vault, SEED_TOPIC)
    assert expected > 0, "fixture precondition: the seed topic has content pages"

    result = _run("status")

    assert result.returncode == 0, result.stderr
    assert str(expected) in result.stdout, (
        f"status must report the {expected} content pages actually on disk; got {result.stdout!r}"
    )


def test_page_count_tracks_an_added_page(vault_config: Path, template_vault: Path):
    """Non-vacuity guard: adding one page bumps the reported count by exactly one,
    proving the number is derived from the vault, not a constant."""
    before = _content_page_count(template_vault, SEED_TOPIC)
    _add_content_page(template_vault, SEED_TOPIC, "brand-new-page.md")
    after = _content_page_count(template_vault, SEED_TOPIC)
    assert after == before + 1

    result = _run("status", "--json")

    payload = json.loads(result.stdout)
    assert after in _int_values(payload), (
        f"after adding a page the count must be {after}; JSON values: {_int_values(payload)!r}"
    )


# ---------------------------------------------------------------------------
# --topic scoping
# ---------------------------------------------------------------------------


def test_topic_scoping_counts_only_the_named_topic(vault_config: Path, template_vault: Path):
    """A second topic's pages must not leak into ``--topic <seed>`` counts."""
    seed_pages = _content_page_count(template_vault, SEED_TOPIC)
    other = template_vault / "other-topic"
    other.mkdir()
    (other / "one.md").write_text("# one\n", encoding="utf-8")
    (other / "two.md").write_text("# two\n", encoding="utf-8")
    run_git(template_vault, "add", "-A")
    run_git(template_vault, "commit", "-m", "test: add a second topic")

    scoped = _run("status", "--topic", SEED_TOPIC, "--json")

    assert scoped.returncode == 0, scoped.stderr
    payload = json.loads(scoped.stdout)
    ints = _int_values(payload)
    assert seed_pages in ints, (
        f"--topic {SEED_TOPIC} must report its own {seed_pages} pages; saw {ints!r}"
    )
    assert seed_pages + 2 not in ints, (
        "the second topic's pages must not be counted under --topic scoping"
    )


# ---------------------------------------------------------------------------
# --json stability + unconfigured exit 3
# ---------------------------------------------------------------------------


def test_json_output_is_stable_machine_readable(vault_config: Path, template_vault: Path):
    result = _run("status", "--json")

    payload = json.loads(result.stdout)
    assert json.loads(json.dumps(payload)) == payload, "JSON output must round-trip"
    assert SEED_TOPIC in " ".join(_string_values(payload)), (
        "the JSON must name the topic it is counting"
    )


def test_unconfigured_vault_exits_three_with_the_setup_remediation(
    unconfigured_env: Path,
):
    result = _run("status")

    assert result.returncode == 3, (
        f"unconfigured must exit 3 (got {result.returncode}); stderr: {result.stderr!r}"
    )
    assert UNCONFIGURED_MESSAGE in result.stderr
