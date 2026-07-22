"""Behavioral tests for the `loop` dispatcher's `cadence` and `run_eval` actions.

Two concerns, tested at the payload-builder layer (`tools_vault._loop_cadence_payload`
/ `_loop_run_eval_payload`) since these are the functions the `loop` dispatcher routes
to verbatim:

- Cadence config writes are additive: a pre-existing sibling table in
  `config.toml` (`[gapfill]`) survives a `cadence` write untouched.
- The `run_eval` two-phase decision envelope never bills on a bare, stale,
  mismatched, or replayed call -- only a fresh, matching, unexpired nonce
  reaches the billing boundary (`_execute_run_eval`), exactly once.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch


from knotica.core.config import config_file_path
from knotica.store.local import LocalFSStore

TOPIC = "agentic-systems"


def _store_and_path(template_vault: Path) -> tuple[LocalFSStore, Path]:
    return LocalFSStore(template_vault), template_vault


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
        TOPIC,
        eval_min_interval_hours=6.0,
        eval_window=None,
        eval_num_threads=None,
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
        TOPIC, eval_min_interval_hours=None, eval_window=None, eval_num_threads=None
    )

    after = path.read_text(encoding="utf-8")
    assert before == after
    payload = result["data"] if "data" in result else result
    assert payload["eval_min_interval_hours"] == 0.0


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
    from knotica.mcp_server import tools_vault
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
            datetime.now(UTC) - timedelta(seconds=tools_vault._RUN_EVAL_NONCE_TTL_SECONDS + 5)
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
