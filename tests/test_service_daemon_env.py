"""The daemon's environment bootstrap: ``~/.config/knotica/.env`` -> process env.

launchd/systemd start the loop daemon with a near-empty environment while the
eval-LLM credential is process-environment-only by contract. The daemon entry
therefore loads the canonical config-dir ``.env`` into unset environment keys
before supervision starts. These tests pin that contract: setdefault semantics
(a real exported variable always wins), the minimal dotenv grammar, silence on
a missing file, and the entry point actually bootstrapping before it
supervises.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knotica.service.__main__ import bootstrap_environment, main


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_unset_keys_are_loaded_from_the_config_dotenv(tmp_path: Path) -> None:
    dotenv = _write(tmp_path / ".env", "CLAUDE_CODE_OAUTH_TOKEN=tok-abc\n")
    env: dict[str, str] = {}

    bootstrap_environment(dotenv, environ=env)

    assert env == {"CLAUDE_CODE_OAUTH_TOKEN": "tok-abc"}


def test_an_already_set_variable_is_never_overridden(tmp_path: Path) -> None:
    dotenv = _write(tmp_path / ".env", "KNOTICA_YOUCOM_API_KEY=from-file\n")
    env = {"KNOTICA_YOUCOM_API_KEY": "from-real-environment"}

    bootstrap_environment(dotenv, environ=env)

    assert env["KNOTICA_YOUCOM_API_KEY"] == "from-real-environment", (
        "a genuinely exported variable must always win over the .env fallback"
    )


def test_comments_blanks_export_prefix_and_quotes_follow_the_dotenv_grammar(
    tmp_path: Path,
) -> None:
    dotenv = _write(
        tmp_path / ".env",
        "# comment line\n"
        "\n"
        "export QUOTED='single'\n"
        'DOUBLE="double"\n'
        "not a key-value line\n"
        "EMPTY=\n",
    )
    env: dict[str, str] = {}

    bootstrap_environment(dotenv, environ=env)

    assert env == {"QUOTED": "single", "DOUBLE": "double"}, (
        "comments, blanks, non-assignments, and empty values are skipped; the "
        "export prefix and surrounding quotes are stripped"
    )


def test_a_missing_dotenv_file_is_a_silent_no_op(tmp_path: Path) -> None:
    env: dict[str, str] = {"PRESENT": "untouched"}

    bootstrap_environment(tmp_path / "does-not-exist.env", environ=env)

    assert env == {"PRESENT": "untouched"}


def test_main_bootstraps_the_environment_before_supervising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "knotica.service.__main__.bootstrap_environment",
        lambda *a, **k: calls.append("bootstrap"),
    )
    monkeypatch.setattr(
        "knotica.service.__main__.supervise",
        lambda *a, **k: calls.append("supervise"),
    )

    main()

    assert calls == ["bootstrap", "supervise"], (
        "the daemon must load the config-dir .env before supervision starts"
    )
