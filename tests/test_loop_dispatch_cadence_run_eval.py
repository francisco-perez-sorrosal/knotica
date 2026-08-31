"""Behavioral tests for the `loop` dispatcher's `cadence` and `run_eval` actions.

Two concerns, tested at the payload-builder layer (`tools_vault._loop_cadence_payload`
/ `_loop_run_eval_payload`) since these are the functions the `loop` dispatcher routes
to verbatim:

- Cadence config writes are additive: a pre-existing sibling table in
  `config.toml` (`[gapfill]`) survives a `cadence` write untouched. The same
  rail carries `arena_scorer`, which is validated *before* the write so a
  rejected value can never land on disk.
- The `run_eval` two-phase decision envelope never bills on a bare, stale,
  mismatched, or replayed call -- only a fresh, matching, unexpired nonce
  reaches the billing boundary (`_execute_run_eval`), exactly once.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from knotica.core.config import config_file_path
from knotica.store.local import LocalFSStore

TOPIC = "agentic-systems"


def _store_and_path(template_vault: Path) -> tuple[LocalFSStore, Path]:
    return LocalFSStore(template_vault), template_vault


def _confirmed_scorer_switch(vault: Path, **overrides: object) -> dict:
    """Drive the two-phase confirm for `arena_scorer="eval"` and return phase 2.

    Switching TO the eval scorer bills one golden-set eval per variant on every
    future race, so it is gated exactly like the billed actions; every test that
    wants the switch *applied* has to say so twice, which is the point.
    """
    from knotica.mcp_server.tools_vault import _loop_cadence_payload

    kwargs: dict = {
        "eval_min_interval_hours": None,
        "eval_window": None,
        "eval_num_threads": None,
        "arena_scorer": "eval",
    }
    kwargs.update(overrides)
    preview = _loop_cadence_payload(vault, TOPIC, **kwargs)
    nonce = (preview["data"] if "data" in preview else preview)["confirm_nonce"]
    return _loop_cadence_payload(vault, TOPIC, confirm=nonce, **kwargs)


# ---------------------------------------------------------------------------
# cadence read/write round-trips through config.toml without clobbering sibling tables
# ---------------------------------------------------------------------------


def test_cadence_write_preserves_preexisting_gapfill_table(
    vault_config: Path, template_vault: Path
) -> None:
    """A pre-existing `[gapfill]` table survives a `cadence` write untouched."""
    from knotica.mcp_server.tools_vault import _loop_cadence_payload

    path = config_file_path()
    original = path.read_text(encoding="utf-8")
    path.write_text(
        original + "\n[gapfill]\nauto_discover = true\nmax_sources = 3\n",
        encoding="utf-8",
    )

    _loop_cadence_payload(
        template_vault,
        TOPIC,
        eval_min_interval_hours=6.0,
        eval_window=None,
        eval_num_threads=None,
        arena_scorer=None,
    )

    written = path.read_text(encoding="utf-8")
    assert "[gapfill]" in written
    assert "auto_discover = true" in written
    assert "max_sources = 3" in written
    assert "[loop]" in written
    assert "eval_min_interval_hours = 6.0" in written


def test_cadence_read_only_call_does_not_write_config(
    vault_config: Path, template_vault: Path
) -> None:
    """Calling with no cadence params is read-only -- resolved values are returned
    but the config file on disk is left byte-identical."""
    from knotica.mcp_server.tools_vault import _loop_cadence_payload

    path = config_file_path()
    before = path.read_text(encoding="utf-8")

    result = _loop_cadence_payload(
        template_vault,
        TOPIC,
        eval_min_interval_hours=None,
        eval_window=None,
        eval_num_threads=None,
        arena_scorer=None,
    )

    after = path.read_text(encoding="utf-8")
    assert before == after
    payload = result["data"] if "data" in result else result
    assert payload["eval_min_interval_hours"] == 0.0


# ---------------------------------------------------------------------------
# arena_scorer rides the same cadence rail: readable, validated, additively written
# ---------------------------------------------------------------------------


def test_cadence_read_reports_the_default_arena_scorer(
    vault_config: Path, template_vault: Path
) -> None:
    """A read-only cadence call names the packaged `arena_scorer` default."""
    from knotica.mcp_server.tools_vault import _loop_cadence_payload

    result = _loop_cadence_payload(
        template_vault,
        TOPIC,
        eval_min_interval_hours=None,
        eval_window=None,
        eval_num_threads=None,
        arena_scorer=None,
    )

    payload = result["data"] if "data" in result else result
    assert payload["arena_scorer"] == "heuristic"


def test_writing_the_arena_scorer_persists_and_reads_back(
    vault_config: Path, template_vault: Path
) -> None:
    """Switching to the eval scorer lands in `[loop]` and round-trips on the next read."""
    from knotica.mcp_server.tools_vault import _loop_cadence_payload

    written = _confirmed_scorer_switch(template_vault)

    write_payload = written["data"] if "data" in written else written
    assert write_payload["arena_scorer"] == "eval"
    assert 'arena_scorer = "eval"' in config_file_path().read_text(encoding="utf-8")

    read_back = _loop_cadence_payload(
        template_vault,
        TOPIC,
        eval_min_interval_hours=None,
        eval_window=None,
        eval_num_threads=None,
        arena_scorer=None,
    )
    read_payload = read_back["data"] if "data" in read_back else read_back
    assert read_payload["arena_scorer"] == "eval"


def test_an_unknown_arena_scorer_is_rejected_and_leaves_the_config_untouched(
    vault_config: Path, template_vault: Path
) -> None:
    """Validation happens before the write -- a bad value never reaches disk."""
    from knotica.core.errors import ErrorCode, KnoticaError
    from knotica.mcp_server.tools_vault import _loop_cadence_payload

    path = config_file_path()
    before = path.read_text(encoding="utf-8")

    with pytest.raises(KnoticaError) as caught:
        _loop_cadence_payload(
            template_vault,
            TOPIC,
            eval_min_interval_hours=None,
            eval_window=None,
            eval_num_threads=None,
            arena_scorer="vibes",
        )

    assert caught.value.code is ErrorCode.INVALID_ARGUMENT, (
        "a value the caller just passed is a bad argument, not a broken install -- "
        "NOT_CONFIGURED sends an agent down the setup path instead of fixing one argument"
    )
    assert 'arena_scorer="heuristic" or "eval"' in caught.value.fix
    assert "arena_scorer" in str(caught.value)
    assert path.read_text(encoding="utf-8") == before


def test_an_arena_scorer_write_leaves_sibling_loop_keys_and_tables_intact(
    vault_config: Path, template_vault: Path
) -> None:
    """The scorer write is additive: other `[loop]` keys and sibling tables survive."""

    path = config_file_path()
    path.write_text(
        path.read_text(encoding="utf-8")
        + "\n[loop]\neval_min_interval_hours = 6.0\neval_num_threads = 3\n"
        + "\n[gapfill]\nmax_gaps = 3\n",
        encoding="utf-8",
    )

    result = _confirmed_scorer_switch(template_vault)

    payload = result["data"] if "data" in result else result
    assert payload["eval_min_interval_hours"] == 6.0
    assert payload["eval_num_threads"] == 3
    assert payload["arena_scorer"] == "eval"
    written = path.read_text(encoding="utf-8")
    assert "[gapfill]" in written
    assert "max_gaps = 3" in written


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("eval_window", "tonight"),
        ("eval_min_interval_hours", -1.0),
        ("eval_num_threads", 999),
    ],
)
def test_a_rejected_cadence_value_leaves_the_config_byte_identical(
    vault_config: Path, template_vault: Path, key: str, value: object
) -> None:
    """All four keys are validated before the file is opened, not just
    `arena_scorer`.

    A written-then-rejected value poisons the whole `[loop]` table: the caller
    gets an error *and* every unrelated reader (`build_loop_runner`, the cadence
    check, the CLI watcher) fails until a human edits the file by hand.
    """
    from knotica.core.errors import ErrorCode, KnoticaError
    from knotica.mcp_server.tools_vault import _loop_cadence_payload

    path = config_file_path()
    before = path.read_text(encoding="utf-8")
    kwargs: dict = {
        "eval_min_interval_hours": None,
        "eval_window": None,
        "eval_num_threads": None,
        "arena_scorer": None,
    }
    kwargs[key] = value

    with pytest.raises(KnoticaError) as caught:
        _loop_cadence_payload(template_vault, TOPIC, **kwargs)

    assert caught.value.code is ErrorCode.INVALID_ARGUMENT
    assert path.read_text(encoding="utf-8") == before


def test_a_rejected_value_does_not_write_its_valid_siblings(
    vault_config: Path, template_vault: Path
) -> None:
    """Validation is all-or-nothing across the call: a good `eval_num_threads`
    beside a bad `eval_window` must not land on its own."""
    from knotica.core.errors import KnoticaError
    from knotica.mcp_server.tools_vault import _loop_cadence_payload

    path = config_file_path()
    before = path.read_text(encoding="utf-8")

    with pytest.raises(KnoticaError):
        _loop_cadence_payload(
            template_vault,
            TOPIC,
            eval_min_interval_hours=None,
            eval_window="tonight",
            eval_num_threads=2,
            arena_scorer=None,
        )

    assert path.read_text(encoding="utf-8") == before


def test_switching_to_the_eval_scorer_previews_before_it_writes(
    vault_config: Path, template_vault: Path
) -> None:
    """A one-call switch to `eval` commits to strictly more spend than the
    single eval `run_eval` gates behind two phases -- every future gate-failure
    race, fired autonomously by the daemon, bills one eval per variant."""
    from knotica.mcp_server.tools_vault import _loop_cadence_payload

    path = config_file_path()
    before = path.read_text(encoding="utf-8")

    preview = _loop_cadence_payload(
        template_vault,
        TOPIC,
        eval_min_interval_hours=None,
        eval_window=None,
        eval_num_threads=None,
        arena_scorer="eval",
    )

    payload = preview["data"] if "data" in preview else preview
    assert payload["confirm_nonce"]
    assert payload["requested_arena_scorer"] == "eval"
    assert payload["arena_scorer"] == "heuristic", "the preview reports what is still in effect"
    assert path.read_text(encoding="utf-8") == before, "phase 1 writes nothing"


def test_switching_back_to_the_heuristic_scorer_needs_no_confirm(
    vault_config: Path, template_vault: Path
) -> None:
    """The gate is on the spend, not on the parameter: leaving `eval` is free."""
    from knotica.mcp_server.tools_vault import _loop_cadence_payload

    _confirmed_scorer_switch(template_vault)

    result = _loop_cadence_payload(
        template_vault,
        TOPIC,
        eval_min_interval_hours=None,
        eval_window=None,
        eval_num_threads=None,
        arena_scorer="heuristic",
    )

    payload = result["data"] if "data" in result else result
    assert payload["arena_scorer"] == "heuristic"


def test_a_stale_scorer_confirm_re_previews_instead_of_writing(
    vault_config: Path, template_vault: Path
) -> None:
    """A mismatched nonce falls through to phase 1, exactly as `run_eval` does --
    never a silent apply."""
    from knotica.mcp_server.tools_vault import _loop_cadence_payload

    path = config_file_path()
    before = path.read_text(encoding="utf-8")

    result = _loop_cadence_payload(
        template_vault,
        TOPIC,
        eval_min_interval_hours=None,
        eval_window=None,
        eval_num_threads=None,
        arena_scorer="eval",
        confirm="not-a-real-nonce",
    )

    payload = result["data"] if "data" in result else result
    assert payload["confirm_nonce"]
    assert path.read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# two-phase billed trigger: preview envelope on bare call, nonce lifecycle on confirm
# ---------------------------------------------------------------------------


def test_phase_one_call_with_no_confirm_never_bills(
    vault_config: Path, template_vault: Path
) -> None:
    """A bare call with no `confirm` mints a preview envelope and never invokes
    the billing boundary."""
    from knotica.mcp_server.tools_vault import _loop_run_eval_payload

    store, vault_path = _store_and_path(template_vault)
    with patch("knotica.mcp_server.tools_vault._execute_run_eval") as billing:
        result = _loop_run_eval_payload(store, vault_path, TOPIC, confirm="", num_threads=None)

    billing.assert_not_called()
    payload = result["data"] if "data" in result else result
    assert payload["action"] == "run_eval"
    assert "confirm_nonce" in payload
    assert payload["confirm_nonce"]


def test_phase_one_call_with_mismatched_nonce_never_bills(
    vault_config: Path, template_vault: Path
) -> None:
    """A `confirm` value that does not match any minted nonce falls through to
    phase 1 -- the billing boundary is never reached."""
    from knotica.mcp_server.tools_vault import _loop_run_eval_payload

    store, vault_path = _store_and_path(template_vault)
    with patch("knotica.mcp_server.tools_vault._execute_run_eval") as billing:
        result = _loop_run_eval_payload(
            store, vault_path, TOPIC, confirm="not-a-real-nonce", num_threads=None
        )

    billing.assert_not_called()
    payload = result["data"] if "data" in result else result
    assert "confirm_nonce" in payload


def test_phase_two_call_with_valid_nonce_bills_exactly_once_with_requested_threads(
    vault_config: Path, template_vault: Path
) -> None:
    """A `confirm` matching the freshly minted nonce reaches the billing
    boundary exactly once, passing through the requested `num_threads` as it was
    fixed into the envelope at mint (phase-1) time -- the number of threads a
    human sees and approves in the preview is the number that actually bills."""
    from knotica.mcp_server.tools_vault import _loop_run_eval_payload

    store, vault_path = _store_and_path(template_vault)

    with patch("knotica.mcp_server.tools_vault._execute_run_eval") as billing:
        billing.return_value = {"billed": True}
        preview = _loop_run_eval_payload(store, vault_path, TOPIC, confirm="", num_threads=7)
        preview_payload = preview["data"] if "data" in preview else preview
        nonce = preview_payload["confirm_nonce"]
        assert preview_payload["num_threads"] == 7

        billing.assert_not_called()

        _loop_run_eval_payload(store, vault_path, TOPIC, confirm=nonce, num_threads=None)

    billing.assert_called_once()
    _, kwargs = billing.call_args
    assert kwargs["num_threads"] == 7


def test_expired_nonce_is_rejected_and_does_not_bill(
    vault_config: Path, template_vault: Path
) -> None:
    """A nonce past its TTL is rejected on phase 2 -- billing never fires."""
    from knotica.mcp_server import confirm_nonce
    from knotica.mcp_server.tools_vault import _loop_run_eval_payload, _run_eval_nonce_path

    store, vault_path = _store_and_path(template_vault)

    with patch("knotica.mcp_server.tools_vault._execute_run_eval") as billing:
        preview = _loop_run_eval_payload(store, vault_path, TOPIC, confirm="", num_threads=None)
        preview_payload = preview["data"] if "data" in preview else preview
        nonce = preview_payload["confirm_nonce"]

        # Rewrite the minted nonce file with a stale `minted_at`, simulating
        # TTL expiry without a real sleep.
        import json

        nonce_path = _run_eval_nonce_path(vault_path, TOPIC)
        record = json.loads(nonce_path.read_text(encoding="utf-8"))
        record["minted_at"] = (
            datetime.now(UTC) - timedelta(seconds=confirm_nonce.NONCE_TTL_SECONDS + 5)
        ).isoformat()
        nonce_path.write_text(json.dumps(record), encoding="utf-8")

        result = _loop_run_eval_payload(store, vault_path, TOPIC, confirm=nonce, num_threads=None)

    billing.assert_not_called()
    result_payload = result["data"] if "data" in result else result
    # Fell through to a fresh phase-1 envelope, not a billed execution.
    assert "confirm_nonce" in result_payload


def test_second_phase_two_call_reusing_consumed_nonce_is_rejected(
    vault_config: Path, template_vault: Path
) -> None:
    """A nonce is single-use: replaying the same `confirm` value on a second
    call is rejected and does not bill a second time."""
    from knotica.mcp_server.tools_vault import _loop_run_eval_payload

    store, vault_path = _store_and_path(template_vault)

    with patch("knotica.mcp_server.tools_vault._execute_run_eval") as billing:
        billing.return_value = {"billed": True}
        preview = _loop_run_eval_payload(store, vault_path, TOPIC, confirm="", num_threads=None)
        preview_payload = preview["data"] if "data" in preview else preview
        nonce = preview_payload["confirm_nonce"]

        _loop_run_eval_payload(store, vault_path, TOPIC, confirm=nonce, num_threads=None)
        billing.assert_called_once()

        # Replay: the same nonce, a second time.
        replay_result = _loop_run_eval_payload(
            store, vault_path, TOPIC, confirm=nonce, num_threads=None
        )

    # No second billing call -- still exactly one.
    billing.assert_called_once()
    replay_payload = replay_result["data"] if "data" in replay_result else replay_result
    assert "confirm_nonce" in replay_payload
