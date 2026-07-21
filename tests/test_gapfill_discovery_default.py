"""``[gapfill] discover_on_regression``'s conditional-on-a-valid-key default.

Derived from ``SYSTEMS_PLAN.md``'s conditional-discovery-default acceptance
criterion and dec-029's reversal trigger ("once the loop reliably runs with a
provisioned key ... flip the default to on") -- never from the implementation.
Today ``resolve_gapfill_config`` (``core/gapfill_config.py``) always defaults
``discover_on_regression`` to ``False`` regardless of environment. The target
behavior computes the default from whether a discovery-provider API key is
present in the environment, while preserving the existing explicit
``[gapfill] discover_on_regression = true|false`` config knob as an override:

    resolved = False                     if [gapfill] discover_on_regression is explicitly false
             = (a valid key is present)   otherwise (absent config, or explicitly true)

This keeps dec-029's offline guarantee intact for every keyless install (today's
behavior, byte-identical), lets an explicit ``false`` always win (a user's
opt-out is never silently overridden by key presence), and fails an explicit
``true`` closed to ``off`` when no key backs it (you cannot autonomously drain
discovery without a credential to authenticate the search call, regardless of
what the config says).

RED-first / paired with a concurrent implementer step: ``resolve_gapfill_config``
exists today but computes an unconditional ``False`` default -- every test here
that exercises the key-present branch is expected to fail against the
pre-Step-9 implementation and turn green once the paired implementer step
lands. Production symbols are resolved lazily inside a helper so collection
never depends on the implementer's landed code. This file was written without
reading the implementer's code.

**Signature assumption (flagged, not verified against the implementation):**
``resolve_gapfill_config`` keeps its current ``config_path``-only signature --
key presence is read from the real process environment (mirroring
``knotica.discovery.config.resolve_api_key``'s existing env + ``.env``-fallback
precedence), not through a new constructor parameter. Tests drive this purely
through ``monkeypatch.setenv``/``delenv`` on the actual env var
(``KNOTICA_YOUCOM_API_KEY``) rather than assuming any new keyword argument, so
this suite survives whether the implementation calls
``knotica.discovery.config.resolve_api_key`` directly, re-reads
``os.environ`` itself, or delegates some other way. If the landed
implementation instead requires an explicit parameter to inject the key
(rather than reading the environment), that is a reconciliation point for the
integration checkpoint, not a test design error.

The **discovery-failure-mid-drain isolation** and **max_gaps drain cap** cases
named in this feature's Risk Assessment are already fully characterized at the
``LoopRunner`` level in ``tests/test_loop_gapfill_hook.py`` (driven via the
existing explicit ``discover_on_regression=True`` constructor kwarg) -- this
file does not duplicate that coverage. Its own end-to-end test proves only the
new wiring this step adds: that a *computed* (not explicitly passed) ``True``
default, produced by ``resolve_gapfill_config`` from key presence alone, feeds
through ``build_loop_runner`` and actually triggers a drain -- the literal
the conditional-default contract, Given/When/Then.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from test_loop_gapfill_hook import (
    TOPIC,
    _commit_content_change,
    _FakeDiscoveryService,
    _freeze_n_gaps,
    _manifest_with_deltas,
    _per_id_delta,
    _regression_fake_evaluate,
)


def _gapfill_config_module():
    import knotica.core.gapfill_config as gapfill_config

    return gapfill_config


def _write_config(tmp_path: Path, body: str) -> Path:
    config_file = tmp_path / "config.toml"
    config_file.write_text(body, encoding="utf-8")
    return config_file


@pytest.fixture(autouse=True)
def _hermetic_discovery_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test from the real dev machine's environment and ``.env``
    fallback files -- a real exported key must never leak in, and no test may
    accidentally read the real ``~/.config/knotica`` tree or a stray ``./.env``."""
    monkeypatch.delenv("KNOTICA_YOUCOM_API_KEY", raising=False)
    monkeypatch.delenv("KNOTICA_EXA_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))


# ---------------------------------------------------------------------------
# resolve_gapfill_config: the conditional-default computation
# ---------------------------------------------------------------------------


def test_no_discovery_key_and_no_config_leaves_discovery_off_matching_todays_default(
    tmp_path: Path,
):
    gapfill_config = _gapfill_config_module()
    missing_path = tmp_path / "does-not-exist.toml"

    resolved = gapfill_config.resolve_gapfill_config(config_path=missing_path)

    assert resolved.discover_on_regression is False, (
        "a keyless, unconfigured install must resolve to the offline-deterministic "
        "default (dec-029) -- unchanged from today's behavior"
    )


def test_a_valid_discovery_key_with_no_explicit_config_turns_discovery_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    gapfill_config = _gapfill_config_module()
    missing_path = tmp_path / "does-not-exist.toml"
    monkeypatch.setenv("KNOTICA_YOUCOM_API_KEY", "sk-test-valid-key")

    resolved = gapfill_config.resolve_gapfill_config(config_path=missing_path)

    assert resolved.discover_on_regression is True, (
        "a provisioned discovery key with no explicit override must turn the "
        "loop-side batch on -- the conditional default this step introduces"
    )


def test_explicit_discover_on_regression_false_overrides_a_valid_key_to_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    gapfill_config = _gapfill_config_module()
    config_file = _write_config(tmp_path, "[gapfill]\ndiscover_on_regression = false\n")
    monkeypatch.setenv("KNOTICA_YOUCOM_API_KEY", "sk-test-valid-key")

    resolved = gapfill_config.resolve_gapfill_config(config_path=config_file)

    assert resolved.discover_on_regression is False, (
        "an explicit opt-out must win over key presence -- a user's own config "
        "is never silently overridden by an autonomous default"
    )


def test_explicit_discover_on_regression_true_without_a_valid_key_fails_closed_to_off(
    tmp_path: Path,
):
    gapfill_config = _gapfill_config_module()
    config_file = _write_config(tmp_path, "[gapfill]\ndiscover_on_regression = true\n")

    resolved = gapfill_config.resolve_gapfill_config(config_path=config_file)

    assert resolved.discover_on_regression is False, (
        "the 'valid key' gate must fail closed to off even when the config "
        "explicitly requests on -- discovery cannot authenticate without a key "
        "regardless of what the config says (dec-029 offline guarantee intact)"
    )


def test_empty_string_key_value_is_treated_as_absent_and_fails_closed_to_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    gapfill_config = _gapfill_config_module()
    missing_path = tmp_path / "does-not-exist.toml"
    monkeypatch.setenv("KNOTICA_YOUCOM_API_KEY", "")

    resolved = gapfill_config.resolve_gapfill_config(config_path=missing_path)

    assert resolved.discover_on_regression is False, (
        "an empty-string key is not a usable credential -- it must resolve "
        "identically to a wholly absent key, not silently turn discovery on"
    )


def test_max_gaps_still_resolves_independently_when_the_default_is_computed_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    gapfill_config = _gapfill_config_module()
    config_file = _write_config(tmp_path, "[gapfill]\nmax_gaps = 2\n")
    monkeypatch.setenv("KNOTICA_YOUCOM_API_KEY", "sk-test-valid-key")

    resolved = gapfill_config.resolve_gapfill_config(config_path=config_file)

    assert resolved.discover_on_regression is True, "key presence still computes on"
    assert resolved.max_gaps == 2, (
        "max_gaps must keep resolving from its own config value, untouched by "
        "the new key-presence gate governing the other field"
    )


# ---------------------------------------------------------------------------
# End-to-end: the computed default actually drives a drain
# ---------------------------------------------------------------------------


def test_a_valid_key_with_no_explicit_config_drains_discovery_automatically_on_a_genuine_gap_regression(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
):
    """When a regression classifies genuine gaps and a
    valid discovery key is present, the system drains discovery automatically --
    with no explicit ``discover_on_regression=True`` passed anywhere, only the
    resolved config feeding the shared factory."""
    from knotica.core import gapfill as gapfill_mod
    from knotica.core.loop import build_loop_runner

    gapfill_config = _gapfill_config_module()
    missing_path = template_vault.parent / "does-not-exist.toml"
    monkeypatch.setenv("KNOTICA_YOUCOM_API_KEY", "sk-test-valid-key")
    fake_service = _FakeDiscoveryService()
    monkeypatch.setattr(
        gapfill_mod, "build_default_discovery_service", lambda **_kwargs: fake_service
    )

    qa_id = _freeze_n_gaps(template_vault, 1)[0]
    manifest = _manifest_with_deltas(generation=2, per_id={qa_id: _per_id_delta()})
    resolved_config = gapfill_config.resolve_gapfill_config(config_path=missing_path)
    runner = build_loop_runner(
        template_vault,
        TOPIC,
        evaluate=_regression_fake_evaluate(0.40, generation=2, manifest=manifest),
        arena_score=lambda *_args, **_kwargs: 0.0,
        gapfill_config=resolved_config,
    )
    runner.set_baseline(0.90, harness_version="fake-gapfill-hook")
    _commit_content_change(template_vault, "the regressing ingest")

    result = runner.observe_default()

    assert result.acted is True
    assert len(fake_service.calls) == 1, (
        "a valid key with no explicit override must autonomously drain discovery "
        "on the regression tick -- the resolved config, not an explicit kwarg, "
        "is what turned this on"
    )
