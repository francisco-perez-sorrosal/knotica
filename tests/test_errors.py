"""Behavioral contract tests for ``knotica.core.errors``.

The tool-result error contract: the failure discriminator is the **presence of
an ``error`` key** — there is no top-level ``status`` field. Every failure
carries a stable ``code`` from a fixed ten-member enum, a ``message`` and a
``fix`` following the "X failed because Y. To fix: Z." grammar, and a
``retryable`` flag. ``LOCK_BUSY`` is the only retryable code.
``SECRET_SCRUBBED`` is a *warning riding on success*, never an error.

Expected interface:

- an enum of exactly the ten contract codes (probed by name: ``ErrorCode`` or
  any module-defined Enum carrying the members);
- ``KnoticaError(code, message, fix=..., retryable=...)`` exposing
  ``.code`` / ``.message`` / ``.fix`` / ``.retryable``, with per-code
  **default** ``fix`` text and ``retryable`` flag when omitted — the
  code→fix/retryable table lives in this module, not at call sites;
- an envelope render surface producing ``{"error": {code, message, fix,
  retryable}}`` with the code as its wire string (probed: method or module
  function);
- a warning type carrying ``SECRET_SCRUBBED`` that is *not* a ``KnoticaError``.

Production imports are deferred into test bodies so collection succeeds even
while the module under test is still in flight.
"""

import json

import pytest

# ---------------------------------------------------------------------------
# The contract table, mirrored verbatim.
# ---------------------------------------------------------------------------

# code name -> retryable (SECRET_SCRUBBED is absent on purpose: it is a
# warning on success, "retryable" is n/a — see the warning-separation tests).
RETRYABLE_BY_CODE = {
    "NOT_CONFIGURED": False,
    "TOPIC_NOT_FOUND": False,
    "PAGE_NOT_FOUND": False,
    "RESERVED_NAME": False,
    "SOURCE_EXISTS": False,
    "INVALID_FRONTMATTER": False,
    "LOCK_BUSY": True,
    "GIT_ERROR": False,
    "INVALID_CURSOR": False,
    # Eval-harness LLM transport failures (rate limit / server error / network):
    # transient by default -- raisers pass retryable=False explicitly for
    # non-transient statuses such as auth rejections.
    "LLM_API_ERROR": True,
    # Discovery-layer (gapfill P2) search-provider transport failures: same
    # transient-by-default posture as LLM_API_ERROR -- rate limits and 5xx
    # clear on their own; a raiser passes retryable=False for non-transient
    # statuses (e.g. auth rejections).
    "SEARCH_API_ERROR": True,
}

ERROR_CODE_NAMES = frozenset(RETRYABLE_BY_CODE)
ALL_CODE_NAMES = ERROR_CODE_NAMES | {"SECRET_SCRUBBED"}

# Message-uniformity markers: every surface's unconfigured remediation must
# name BOTH setup paths (plugin command and CLI). test_config.py imports this
# helper so the expectation has one source; migrate it into the shared
# conftest once the test spine lands.
UNCONFIGURED_REMEDIATION_MARKERS = ("/knotica:setup", "knotica init")


def assert_names_both_setup_paths(text: str) -> None:
    """The uniform unconfigured remediation names the plugin AND CLI paths."""
    for marker in UNCONFIGURED_REMEDIATION_MARKERS:
        assert marker in text, (
            f"unconfigured remediation must mention {marker!r} — all surfaces "
            f"share one remediation contract; got: {text!r}"
        )


# ---------------------------------------------------------------------------
# Interface probes (tolerant of naming, diagnostic on mismatch)
# ---------------------------------------------------------------------------


def _errors_module():
    import knotica.core.errors as errors

    return errors


def _code_enum(errors):
    """Locate the error-code enum: named ``ErrorCode``, else any module-defined
    Enum whose member names overlap the contract set."""
    import enum

    candidate = getattr(errors, "ErrorCode", None)
    if candidate is not None:
        return candidate
    for obj in vars(errors).values():
        if (
            isinstance(obj, type)
            and issubclass(obj, enum.Enum)
            and set(obj.__members__) & ALL_CODE_NAMES
        ):
            return obj
    raise AssertionError(
        "knotica.core.errors must expose the ten-code contract enum "
        "(looked for 'ErrorCode', then for any Enum carrying members from "
        f"{sorted(ALL_CODE_NAMES)})"
    )


def _code(errors, name: str):
    return _code_enum(errors)[name]


def _make_error(errors, code_name: str, message: str, **kwargs):
    return errors.KnoticaError(_code(errors, code_name), message, **kwargs)


def _render_envelope(errors, err) -> dict:
    """Render an error into the wire envelope, probing the render surface."""
    attempts = []
    for attr in ("to_envelope", "as_envelope", "envelope", "render"):
        member = getattr(err, attr, None)
        if callable(member):
            return member()
        if isinstance(member, dict):
            return member
        attempts.append(f"KnoticaError.{attr}")
    for attr in ("render_envelope", "error_envelope", "to_envelope", "envelope"):
        fn = getattr(errors, attr, None)
        if callable(fn):
            return fn(err)
        attempts.append(f"errors.{attr}")
    raise AssertionError(
        "no envelope render surface found on knotica.core.errors; tried: " + ", ".join(attempts)
    )


# ---------------------------------------------------------------------------
# Enum completeness
# ---------------------------------------------------------------------------


def test_the_code_enum_matches_the_contract_code_set_exactly():
    errors = _errors_module()
    members = set(_code_enum(errors).__members__)
    assert members == ALL_CODE_NAMES, (
        "the code enum must match the interface contract exactly — "
        f"missing: {sorted(ALL_CODE_NAMES - members)}, "
        f"unexpected: {sorted(members - ALL_CODE_NAMES)}"
    )


# ---------------------------------------------------------------------------
# Constructor round-trip + envelope shape
# ---------------------------------------------------------------------------


def test_constructed_error_round_trips_all_four_contract_fields():
    errors = _errors_module()
    err = _make_error(
        errors,
        "TOPIC_NOT_FOUND",
        "read_page failed because no topic named 'agent-memory' exists.",
        fix="Call list_topics to see valid topics.",
        retryable=False,
    )
    assert err.code == _code(errors, "TOPIC_NOT_FOUND")
    assert err.message == ("read_page failed because no topic named 'agent-memory' exists.")
    assert err.fix == "Call list_topics to see valid topics."
    assert err.retryable is False


def test_error_envelope_shape_matches_the_wire_contract():
    errors = _errors_module()
    err = _make_error(
        errors,
        "TOPIC_NOT_FOUND",
        "read_page failed because no topic named 'agent-memory' exists.",
        fix="Call list_topics to see valid topics.",
        retryable=False,
    )
    envelope = _render_envelope(errors, err)

    assert "error" in envelope, "failure discriminator is the 'error' key"
    assert "status" not in envelope, (
        "no top-level status field — presence of 'error' is the ONLY "
        "failure discriminator in the tool-result contract"
    )
    body = envelope["error"]
    assert set(body) == {"code", "message", "fix", "retryable"}
    assert body["code"] == "TOPIC_NOT_FOUND", (
        "the wire code is the plain enum-name string the model branches on"
    )
    assert body["message"] == err.message
    assert body["fix"] == err.fix
    assert body["retryable"] is False
    # The envelope goes into MCP tool-result content: it must be plain JSON.
    json.dumps(envelope)


# ---------------------------------------------------------------------------
# Retryable flags + default fix texts — table-driven mirror of the contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code_name", "expected_retryable"),
    sorted(RETRYABLE_BY_CODE.items()),
)
def test_default_retryable_flag_mirrors_the_contract_table(
    code_name: str, expected_retryable: bool
):
    """LOCK_BUSY is the single retryable code; every other code is final.

    The table is encoded once, in the errors module: constructing an error
    from just (code, message) must yield the correct flag without every call
    site restating it.
    """
    errors = _errors_module()
    err = _make_error(errors, code_name, f"op failed because of {code_name}.")
    assert err.retryable is expected_retryable
    assert _render_envelope(errors, err)["error"]["retryable"] is expected_retryable


@pytest.mark.parametrize("code_name", sorted(ERROR_CODE_NAMES))
def test_every_error_code_carries_a_default_fix_text(code_name: str):
    """Every code ships a default fix — the 'To fix: Z' leg of the grammar
    must never be empty on the wire."""
    errors = _errors_module()
    err = _make_error(errors, code_name, f"op failed because of {code_name}.")
    assert isinstance(err.fix, str) and err.fix.strip(), (
        f"{code_name} must carry a default fix text"
    )


def test_not_configured_default_fix_names_both_setup_paths():
    errors = _errors_module()
    err = _make_error(errors, "NOT_CONFIGURED", "tool failed because knotica is not set up.")
    assert_names_both_setup_paths(err.fix)


def test_lock_busy_fix_tells_the_model_to_retry():
    errors = _errors_module()
    err = _make_error(errors, "LOCK_BUSY", "write_page failed because the vault lock is held.")
    assert "retry" in err.fix.lower()


# ---------------------------------------------------------------------------
# Warning-vs-error separation (SECRET_SCRUBBED)
# ---------------------------------------------------------------------------


def test_secret_scrubbed_cannot_be_constructed_as_an_error():
    """SECRET_SCRUBBED rides on a *successful* write as a warning — the type
    layer must refuse the category error of raising it as a failure."""
    errors = _errors_module()
    with pytest.raises((ValueError, TypeError)):
        errors.KnoticaError(
            _code(errors, "SECRET_SCRUBBED"),
            "write_page scrubbed a secret.",
        )


def _warning_type(errors):
    """Locate the module's warning type (any module-defined class with
    'Warning' in its name that is not the error type)."""
    candidates = [
        obj
        for name, obj in vars(errors).items()
        if isinstance(obj, type)
        and "warning" in name.lower()
        and getattr(obj, "__module__", "") == errors.__name__
    ]
    if not candidates:
        raise AssertionError(
            "knotica.core.errors must ship a warning type for SECRET_SCRUBBED; "
            "no module-defined class with 'Warning' in its name was found"
        )
    return candidates[0]


def _instantiate_warning(errors, cls, message: str):
    secret_scrubbed = _code(errors, "SECRET_SCRUBBED")
    fix = "Review the redacted spans in the response before relying on the page."
    for args, kwargs in (
        ((message,), {}),
        ((secret_scrubbed, message), {}),
        ((secret_scrubbed, message, fix), {}),
        ((), {"message": message}),
        ((), {"code": secret_scrubbed, "message": message}),
        ((), {"code": secret_scrubbed, "message": message, "fix": fix}),
    ):
        try:
            return cls(*args, **kwargs)
        except TypeError:
            continue
    raise AssertionError(
        f"could not instantiate warning type {cls.__name__} with a message "
        "(tried message-only, code+message, and code+message+fix forms)"
    )


def test_the_warning_type_carries_the_secret_scrubbed_code():
    errors = _errors_module()
    cls = _warning_type(errors)
    assert not issubclass(cls, errors.KnoticaError), (
        "the warning type must be disjoint from the error type — warnings "
        "ride on success results, errors are failures"
    )
    warning = _instantiate_warning(
        errors,
        cls,
        "write_page scrubbed 2 secret spans; review before relying on the page.",
    )
    code = getattr(warning, "code", None) or getattr(cls, "code", None)
    code_name = getattr(code, "name", code)
    assert code_name == "SECRET_SCRUBBED", (
        f"warning must identify itself as SECRET_SCRUBBED, got {code!r}"
    )
