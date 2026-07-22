"""Behavioral tests for the `loop` dispatcher's `run_once` action.

``run_once`` triggers a real, billed eval (`observe_default` + `poll_once` in
one tick) -- the same class of billed-surface risk as `run_eval`. It reuses
the exact same nonce mint/consume/TTL mechanism (see
`test_loop_dispatch_cadence_run_eval.py` for the `run_eval` coverage this
mirrors), keyed under a `run_once`-specific nonce file so the two actions
never collide.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from knotica.store.local import LocalFSStore

TOPIC = "agentic-systems"


def _store_and_path(template_vault: Path) -> tuple[LocalFSStore, Path]:
    return LocalFSStore(template_vault), template_vault


def test_phase_one_call_with_no_confirm_never_bills(
    vault_config: Path, template_vault: Path
) -> None:
    """A bare call with no `confirm` mints a preview envelope and never
    invokes the billing boundary (`observe_default`/`poll_once`)."""
    from knotica.mcp_server.tools_vault import _loop_once_payload

    store, vault_path = _store_and_path(template_vault)
    with patch("knotica.mcp_server.tools_vault._execute_run_once") as billing:
        result = _loop_once_payload(store, vault_path, TOPIC, confirm="")

    billing.assert_not_called()
    payload = result["data"] if "data" in result else result
    assert payload["action"] == "run_once"
    assert "confirm_nonce" in payload
    assert payload["confirm_nonce"]


def test_phase_one_call_with_mismatched_nonce_never_bills(
    vault_config: Path, template_vault: Path
) -> None:
    """A `confirm` value that does not match any minted nonce falls through
    to phase 1 -- the billing boundary is never reached."""
    from knotica.mcp_server.tools_vault import _loop_once_payload

    store, vault_path = _store_and_path(template_vault)
    with patch("knotica.mcp_server.tools_vault._execute_run_once") as billing:
        result = _loop_once_payload(store, vault_path, TOPIC, confirm="not-a-real-nonce")

    billing.assert_not_called()
    payload = result["data"] if "data" in result else result
    assert "confirm_nonce" in payload


def test_phase_two_call_with_valid_nonce_bills_exactly_once(
    vault_config: Path, template_vault: Path
) -> None:
    """A `confirm` matching the freshly minted nonce reaches the billing
    boundary exactly once."""
    from knotica.mcp_server.tools_vault import _loop_once_payload

    store, vault_path = _store_and_path(template_vault)

    with patch("knotica.mcp_server.tools_vault._execute_run_once") as billing:
        billing.return_value = {"billed": True}
        preview = _loop_once_payload(store, vault_path, TOPIC, confirm="")
        preview_payload = preview["data"] if "data" in preview else preview
        nonce = preview_payload["confirm_nonce"]

        billing.assert_not_called()

        _loop_once_payload(store, vault_path, TOPIC, confirm=nonce)

    billing.assert_called_once()


def test_expired_nonce_is_rejected_and_does_not_bill(
    vault_config: Path, template_vault: Path
) -> None:
    """A nonce past its TTL is rejected on phase 2 -- billing never fires."""
    from knotica.mcp_server import tools_vault
    from knotica.mcp_server.tools_vault import _loop_once_payload, _run_once_nonce_path

    store, vault_path = _store_and_path(template_vault)

    with patch("knotica.mcp_server.tools_vault._execute_run_once") as billing:
        preview = _loop_once_payload(store, vault_path, TOPIC, confirm="")
        preview_payload = preview["data"] if "data" in preview else preview
        nonce = preview_payload["confirm_nonce"]

        # Rewrite the minted nonce file with a stale `minted_at`, simulating
        # TTL expiry without a real sleep.
        nonce_path = _run_once_nonce_path(vault_path, TOPIC)
        record = json.loads(nonce_path.read_text(encoding="utf-8"))
        record["minted_at"] = (
            datetime.now(UTC) - timedelta(seconds=tools_vault._RUN_EVAL_NONCE_TTL_SECONDS + 5)
        ).isoformat()
        nonce_path.write_text(json.dumps(record), encoding="utf-8")

        result = _loop_once_payload(store, vault_path, TOPIC, confirm=nonce)

    billing.assert_not_called()
    result_payload = result["data"] if "data" in result else result
    # Fell through to a fresh phase-1 envelope, not a billed execution.
    assert "confirm_nonce" in result_payload


def test_second_phase_two_call_reusing_consumed_nonce_is_rejected(
    vault_config: Path, template_vault: Path
) -> None:
    """A nonce is single-use: replaying the same `confirm` value on a second
    call is rejected and does not bill a second time."""
    from knotica.mcp_server.tools_vault import _loop_once_payload

    store, vault_path = _store_and_path(template_vault)

    with patch("knotica.mcp_server.tools_vault._execute_run_once") as billing:
        billing.return_value = {"billed": True}
        preview = _loop_once_payload(store, vault_path, TOPIC, confirm="")
        preview_payload = preview["data"] if "data" in preview else preview
        nonce = preview_payload["confirm_nonce"]

        _loop_once_payload(store, vault_path, TOPIC, confirm=nonce)
        billing.assert_called_once()

        # Replay: the same nonce, a second time.
        replay_result = _loop_once_payload(store, vault_path, TOPIC, confirm=nonce)

    # No second billing call -- still exactly one.
    billing.assert_called_once()
    replay_payload = replay_result["data"] if "data" in replay_result else replay_result
    assert "confirm_nonce" in replay_payload


def test_run_once_and_run_eval_nonces_do_not_collide(
    vault_config: Path, template_vault: Path
) -> None:
    """Minting a `run_once` nonce and a `run_eval` nonce for the same topic
    writes to distinct nonce files -- confirming one never consumes the
    other."""
    from knotica.mcp_server.tools_vault import (
        _loop_once_payload,
        _loop_run_eval_payload,
        _run_eval_nonce_path,
        _run_once_nonce_path,
    )

    store, vault_path = _store_and_path(template_vault)

    with (
        patch("knotica.mcp_server.tools_vault._execute_run_once"),
        patch("knotica.mcp_server.tools_vault._execute_run_eval"),
    ):
        _loop_once_payload(store, vault_path, TOPIC, confirm="")
        _loop_run_eval_payload(store, vault_path, TOPIC, confirm="", num_threads=None)

    assert _run_once_nonce_path(vault_path, TOPIC) != _run_eval_nonce_path(vault_path, TOPIC)
    assert _run_once_nonce_path(vault_path, TOPIC).exists()
    assert _run_eval_nonce_path(vault_path, TOPIC).exists()


def test_run_eval_nonce_cannot_confirm_run_once(vault_config: Path, template_vault: Path) -> None:
    """A nonce minted by `run_eval`'s phase 1 is rejected when passed as the
    `confirm` value to `run_once` -- treated as mismatched, falling through
    to a fresh phase-1 envelope without reaching the billing boundary."""
    from knotica.mcp_server.tools_vault import _loop_once_payload, _loop_run_eval_payload

    store, vault_path = _store_and_path(template_vault)

    with (
        patch("knotica.mcp_server.tools_vault._execute_run_once") as run_once_billing,
        patch("knotica.mcp_server.tools_vault._execute_run_eval"),
    ):
        eval_preview = _loop_run_eval_payload(
            store, vault_path, TOPIC, confirm="", num_threads=None
        )
        eval_preview_payload = eval_preview["data"] if "data" in eval_preview else eval_preview
        eval_nonce = eval_preview_payload["confirm_nonce"]

        result = _loop_once_payload(store, vault_path, TOPIC, confirm=eval_nonce)

    run_once_billing.assert_not_called()
    result_payload = result["data"] if "data" in result else result
    assert "confirm_nonce" in result_payload


def test_run_once_nonce_cannot_confirm_run_eval(vault_config: Path, template_vault: Path) -> None:
    """A nonce minted by `run_once`'s phase 1 is rejected when passed as the
    `confirm` value to `run_eval` -- treated as mismatched, falling through
    to a fresh phase-1 envelope without reaching the billing boundary."""
    from knotica.mcp_server.tools_vault import _loop_once_payload, _loop_run_eval_payload

    store, vault_path = _store_and_path(template_vault)

    with (
        patch("knotica.mcp_server.tools_vault._execute_run_once"),
        patch("knotica.mcp_server.tools_vault._execute_run_eval") as run_eval_billing,
    ):
        once_preview = _loop_once_payload(store, vault_path, TOPIC, confirm="")
        once_preview_payload = once_preview["data"] if "data" in once_preview else once_preview
        once_nonce = once_preview_payload["confirm_nonce"]

        result = _loop_run_eval_payload(
            store, vault_path, TOPIC, confirm=once_nonce, num_threads=None
        )

    run_eval_billing.assert_not_called()
    result_payload = result["data"] if "data" in result else result
    assert "confirm_nonce" in result_payload
