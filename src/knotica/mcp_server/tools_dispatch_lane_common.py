"""Shared machinery for the six lane dispatchers -- one generator, six lanes.

The lane dispatchers (``tools_dispatch_home.py`` ... ``tools_dispatch_tend.py``)
are a **projection of** :mod:`knotica.core.process_model`, never a second copy of
it. Three things are generated here rather than hand-written six times over:

* **the action table** -- exactly the verbs :data:`~knotica.core.process_model.LANE_MEMBERSHIP`
  declares for that lane, ordered by the rail position they act on;
* **the call shape** -- the tool's parameters are the union of its verbs' own
  parameters, read off the real handler signatures, so a lane can never advertise
  an argument the verb it routes to does not take;
* **the description's action list** -- rendered from the same declaration, with
  each verb's narration, so a verb added to a lane cannot go unmentioned.

Routing delegates to **the same function object** the verb's own ``@mcp.tool``
registers. That is the point: payload equality between the lane call and the
verb's own handler is structural, not a claim to be re-verified per verb. The
handlers are collected by running each ``register_*_tools`` function against a
capture stand-in (:class:`_HandlerCapture`) that keeps the function and discards
the schema.

Most of those registration functions are **not** called by ``server.py`` any
more: the operator-tier verbs they own were absorbed into the lanes and removed
from the published surface, so this capture is the only place they run. The
seven verbs that remain published flat -- the conversational core the
client-as-brain calls mid-turn -- run in both, which is exactly why the same
function object has to serve both paths.

Two shape rules a caller has to know, both forced by wrapping one dispatcher in
another:

* A verb that already owns a parameter named ``action`` (the prior wave's topical
  dispatchers, plus ``suggestions_review``) takes it as ``<verb>_action`` here,
  because the lane's own selector is already called ``action``.
* Every parameter is optional and defaults to ``None``; only the arguments a
  caller actually passes are forwarded, so each verb keeps *its own* defaults.
  Two verbs in one lane declaring the same parameter with different defaults is
  therefore harmless -- neither default is ever imposed on the other.

**Deprecation, not just rejection.** A lane module may declare
``SUPERSEDED_ACTIONS: Mapping[str, str]`` (a module-level constant next to its
``_LANE``/``_PURPOSE_DESCRIPTION``) mapping an old action string to the live one that
replaced it. It is empty by default -- the nine-topical-to-six-lane re-cut
renamed no action string, so there is nothing to alias yet -- but the
mechanism exists so a future rename never has to break a caller mid-flight:
calling the old name still reaches the new action's handler and gets its
payload back, with a ``deprecation`` note added telling the caller what to
pass instead. The note rides only on a **success** envelope; a failure is
returned unchanged, so the failure's own ``fix=`` stays the one thing a
caller acts on. An unrecognised action that is *not* declared superseded is
rejected exactly as before -- ``INVALID_ARGUMENT`` plus the live action set.
"""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Callable, Mapping
from functools import reduce
from operator import or_
from types import MappingProxyType
from typing import Annotated, Any, cast

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult
from pydantic import Field

from knotica.core import process_model
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.mcp_server import envelope
from knotica.mcp_server.dispatch_telemetry import record_rejected_action
from knotica.mcp_server.tools_dispatch_arena import register_dispatch_arena_tools
from knotica.mcp_server.tools_dispatch_branches import register_dispatch_branches_tools
from knotica.mcp_server.tools_dispatch_compile import register_dispatch_compile_tools
from knotica.mcp_server.tools_dispatch_datasets import register_dispatch_datasets_tools
from knotica.mcp_server.tools_dispatch_golden import register_dispatch_golden_tools
from knotica.mcp_server.tools_dispatch_loop import register_dispatch_loop_tools
from knotica.mcp_server.tools_dispatch_notes import register_dispatch_notes_tools
from knotica.mcp_server.tools_dispatch_vault_health import register_dispatch_vault_health_tools
from knotica.mcp_server.tools_gaps import register_gaps_lane_tools, register_gaps_tools
from knotica.mcp_server.tools_ingest import register_ingest_lane_tools, register_ingest_tools
from knotica.mcp_server.tools_notes import register_notes_tools
from knotica.mcp_server.tools_prompt_diff import register_prompt_diff_tools
from knotica.mcp_server.tools_query import register_query_tools
from knotica.mcp_server.tools_read import register_read_lane_tools, register_read_tools
from knotica.mcp_server.tools_source_ingest import register_source_ingest_tools
from knotica.mcp_server.tools_status import register_status_lane_tools, register_status_tools
from knotica.mcp_server.tools_suggestions import register_suggestions_tools
from knotica.mcp_server.tools_write import register_write_lane_tools, register_write_tools

__all__ = ["lane_actions", "register_lane_dispatcher", "register_verb_handlers"]

ToolResult = CallToolResult

#: The lane selector's own parameter name, and the suffix a wrapped verb's own
#: ``action`` parameter is renamed to so the two never collide in one call.
_SELECTOR = "action"
_OWN_ACTION_SUFFIX = "_action"

#: Every registration function that owns a verb some lane declares. Listed
#: rather than discovered so the import graph stays readable; a verb whose
#: registrar is missing from here surfaces at registration time as a declared
#: action with no implementation, never as a silently absent action.
_FLAT_REGISTRARS: tuple[Callable[[FastMCP], None], ...] = (
    register_read_tools,
    register_read_lane_tools,
    register_write_tools,
    register_write_lane_tools,
    register_query_tools,
    register_prompt_diff_tools,
    register_status_tools,
    register_status_lane_tools,
    register_suggestions_tools,
    register_gaps_tools,
    register_gaps_lane_tools,
    register_source_ingest_tools,
    register_ingest_tools,
    register_ingest_lane_tools,
    register_notes_tools,
    register_dispatch_loop_tools,
    register_dispatch_branches_tools,
    register_dispatch_compile_tools,
    register_dispatch_datasets_tools,
    register_dispatch_arena_tools,
    register_dispatch_notes_tools,
    register_dispatch_golden_tools,
    register_dispatch_vault_health_tools,
)


class _HandlerCapture:
    """A ``FastMCP`` stand-in that keeps each tool's function and drops its schema.

    Running the real ``register_*_tools`` functions against this is what makes
    lane routing delegate to the *same* function object the flat tool exposes,
    instead of to a re-derived copy of its argument forwarding.
    """

    def __init__(self) -> None:
        self.handlers: dict[str, Callable[..., ToolResult]] = {}

    def tool(
        self, *, name: str, description: str = "", **_ignored: Any
    ) -> Callable[[Callable[..., ToolResult]], Callable[..., ToolResult]]:
        def keep(function: Callable[..., ToolResult]) -> Callable[..., ToolResult]:
            self.handlers[name] = function
            return function

        return keep


_HANDLERS: Mapping[str, Callable[..., ToolResult]] | None = None


def register_verb_handlers(mcp: FastMCP) -> None:
    """Register every verb a lane routes to onto ``mcp``.

    Used here against :class:`_HandlerCapture` to collect the handler functions.
    Registered onto a real server instead, it reconstitutes the verb surface as
    it stood before the lanes absorbed it -- which is what makes a lane call
    comparable to a direct call on the verb it wraps.
    """
    for register in _FLAT_REGISTRARS:
        register(mcp)


def _flat_handlers() -> Mapping[str, Callable[..., ToolResult]]:
    """Every verb handler a lane can route to, by the name it registers under."""
    global _HANDLERS
    if _HANDLERS is None:
        capture = _HandlerCapture()
        register_verb_handlers(cast(FastMCP, capture))
        _HANDLERS = MappingProxyType(dict(capture.handlers))
    return _HANDLERS


# ---------------------------------------------------------------------------
# Projection of the declaration: which actions, in which order, saying what.
# ---------------------------------------------------------------------------


def _lane_narrations(lane: str) -> dict[str, list[tuple[int, str]]]:
    """``{verb: [(rail index, narration), ...]}`` for every verb declared in ``lane``.

    Read live from ``process_model`` on every call rather than captured at import,
    so the table a dispatcher registers always reflects the declaration in force.
    """
    rail = [stage.id for stage in process_model.LANE_STAGES[lane]]
    found: dict[str, list[tuple[int, str]]] = {}
    for (verb, _discriminator), memberships in process_model.LANE_MEMBERSHIP.items():
        for member_lane, stage_id, narration in memberships:
            if member_lane != lane:
                continue
            position = rail.index(stage_id) if stage_id in rail else len(rail)
            found.setdefault(verb, []).append((position, narration))
    return {verb: sorted(set(entries)) for verb, entries in found.items()}


def lane_actions(lane: str) -> tuple[str, ...]:
    """``lane``'s action table, generated from the declaration.

    Ordered by the earliest rail position the verb acts on, then alphabetically,
    so the table reads down the rail the way the lane is worked.
    """
    narrations = _lane_narrations(lane)
    return tuple(sorted(narrations, key=lambda verb: (narrations[verb][0][0], verb)))


def _action_lines(lane: str) -> str:
    """The description's action list -- one line per declared action."""
    narrations = _lane_narrations(lane)
    return "\n".join(
        f"- {verb}: " + "; ".join(narration for _position, narration in narrations[verb])
        for verb in lane_actions(lane)
    )


# ---------------------------------------------------------------------------
# Projection of the declaration onto a call shape.
# ---------------------------------------------------------------------------


def _handler_parameters(verb: str) -> Mapping[str, inspect.Parameter]:
    handler = _flat_handlers().get(verb)
    if handler is None:
        return {}
    return inspect.signature(handler, eval_str=True).parameters


def _lane_parameter_name(verb: str, parameter: str) -> str:
    return f"{verb}{_OWN_ACTION_SUFFIX}" if parameter == _SELECTOR else parameter


def _optional(annotations: list[Any]) -> Any:
    """``X | None``, or ``X | Y | None`` where two verbs type one name differently.

    A parameter every contributing verb types ``str`` stays bare ``str``, and
    that exception is load bearing rather than cosmetic. FastMCP pre-parses a
    string argument as JSON whenever the field's annotation ``is not str``
    (``func_metadata.pre_parse_json`` -- it exists because some hosts send
    lists and objects as JSON strings). Widening a ``str`` parameter to
    ``str | None`` therefore *arms* that pre-parse, and a string whose text
    happens to be valid JSON arrives as a list, dict, bool or ``None`` and
    fails validation: ``paths_json="[]"``, ``accepted_json="[...]"``, or a
    page ``content``/note whose body is a JSON blob. The flat tool, whose
    parameter is a plain ``str``, accepts all of them.

    So the widening broke payload equality -- the one property this module
    exists to hold structurally -- for every all-``str`` parameter. Optionality
    does not depend on the annotation: it comes from ``default=None`` plus
    :func:`_forwarded` dropping unset arguments, both of which are unchanged.
    """
    ordered = sorted({str(annotation): annotation for annotation in annotations}.items())
    distinct = [annotation for _key, annotation in ordered]
    if distinct == [str]:
        return str
    return reduce(or_, distinct) | None


def _selector_annotation(lane: str, actions: tuple[str, ...]) -> Any:
    """``str`` carrying the legal action set as a published schema ``enum``.

    The enum is what a model reads before it calls, so advertising it moves a
    misspelled action out of the round trip -- Bloch's hard-to-misuse row, and
    the whole reason to publish it. It is attached as ``json_schema_extra``
    rather than as a ``Literal``, which would have pydantic *enforce* it: the
    host would then answer an unknown action with a raw validation string,
    losing both the "return an envelope, never raise" contract (no ``code``,
    no ``fix``, no ``retryable``) and the ``record_rejected_action`` signal --
    the only instrument that sees which action a client *meant*. Advisory in
    the schema, enforced at :func:`_reject`, is the shape that keeps all three.

    The set is the live table **plus** the lane's declared superseded names, so
    the deprecation seam is advertised as callable rather than as a typo.
    """
    published: list[Any] = list(actions) + list(_superseded_actions(lane))
    return Annotated[str, Field(json_schema_extra={"enum": published})]


def _lane_signature(lane: str, actions: tuple[str, ...]) -> inspect.Signature:
    """The union of the lane's verbs' own parameters, all optional.

    Every parameter is ``X | None = None`` so that :func:`_forwarded` can tell
    "the caller passed this" from "the caller left it out" and forward only the
    former -- which is what keeps each verb's own defaults intact.

    The ``action`` selector is the exception: it is required, and carries the
    legal set as a schema ``enum`` (see :func:`_selector_annotation`).
    """
    collected: dict[str, list[Any]] = {}
    for verb in actions:
        for parameter in _handler_parameters(verb).values():
            annotation = (
                parameter.annotation if parameter.annotation is not inspect.Parameter.empty else Any
            )
            collected.setdefault(_lane_parameter_name(verb, parameter.name), []).append(annotation)
    parameters = [
        inspect.Parameter(
            _SELECTOR,
            inspect.Parameter.KEYWORD_ONLY,
            annotation=_selector_annotation(lane, actions),
        )
    ] + [
        inspect.Parameter(
            name,
            inspect.Parameter.KEYWORD_ONLY,
            default=None,
            annotation=_optional(annotations),
        )
        for name, annotations in sorted(collected.items())
        if name != _SELECTOR
    ]
    return inspect.Signature(parameters, return_annotation=ToolResult)


def _forwarded(verb: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """The wrapped verb's own kwargs -- unset (``None``) arguments left out."""
    forwarded: dict[str, Any] = {}
    for parameter in _handler_parameters(verb):
        value = arguments.get(_lane_parameter_name(verb, parameter))
        if value is not None:
            forwarded[parameter] = value
    return forwarded


# ---------------------------------------------------------------------------
# Routing.
# ---------------------------------------------------------------------------


def _superseded_actions(lane: str) -> Mapping[str, str]:
    """``lane``'s own ``SUPERSEDED_ACTIONS`` -- empty when the module declares none.

    Resolved by importing the lane module fresh on every dispatch (it is
    already loaded by the time a dispatcher call reaches here, so this is a
    ``sys.modules`` lookup, not a re-import) rather than captured once at
    registration -- the same "always reflects the live declaration" contract
    :func:`_lane_narrations` keeps for the process model.
    """
    module = importlib.import_module(f"{__package__}.tools_dispatch_{lane}")
    return getattr(module, "SUPERSEDED_ACTIONS", {})


def _reject(lane: str, raw_action: str, actions: tuple[str, ...]) -> ToolResult:
    record_rejected_action(lane, raw_action, actions)
    return envelope.error_envelope(
        KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            f"{lane} action must be one of {'|'.join(actions)}, got {raw_action!r}",
            fix=f"Pass action as one of: {', '.join(actions)}.",
        )
    )


def _dispatch(lane: str, actions: tuple[str, ...], arguments: dict[str, Any]) -> ToolResult:
    raw_action = str(arguments.pop(_SELECTOR, "") or "")
    verb = raw_action.strip().lower()
    replacement = _superseded_actions(lane).get(verb)
    if replacement is not None:
        result = _dispatch(lane, actions, {**arguments, _SELECTOR: replacement})
        return envelope.with_deprecation_note(result, replacement)
    if verb not in actions:
        return _reject(lane, raw_action, actions)
    handler = _flat_handlers().get(verb)
    if handler is None:
        return envelope.error_envelope(
            KnoticaError(
                ErrorCode.INVALID_ARGUMENT,
                f"{lane} action {verb!r} is declared in the process model but no tool "
                "implements it on this server",
                fix=f"Use one of the implemented actions: {', '.join(_implemented(actions))}.",
            )
        )
    forwarded = _forwarded(verb, arguments)
    ignored = _ignored_arguments(verb, arguments)
    missing = _missing_required(verb, forwarded)
    if missing:
        return envelope.error_envelope(
            KnoticaError(
                ErrorCode.INVALID_ARGUMENT,
                f"{lane} action={verb} is missing required argument"
                f"{'s' if len(missing) > 1 else ''}: {', '.join(missing)}",
                fix=f"Pass {' and '.join(missing)} with the call.",
            )
        )
    return _with_ignored_arguments(handler(**forwarded), ignored)


def _ignored_arguments(verb: str, arguments: Mapping[str, Any]) -> list[str]:
    """Supplied arguments this lane accepts but the chosen verb does not take.

    A lane's call shape is the *union* of its verbs' parameters (see
    :func:`_lane_signature`), so ``fill`` legitimately advertises the eval
    cadence knobs ``loop`` contributes. Passing one to ``gapfill_discover``
    used to be dropped in silence by :func:`_forwarded` -- the call succeeded
    having ignored an argument the caller believed it had passed, on a billed
    action. The discarded names are already known here; saying them is free.
    """
    accepted = {_lane_parameter_name(verb, name) for name in _handler_parameters(verb)}
    return sorted(
        name for name, value in arguments.items() if value is not None and name not in accepted
    )


def _with_ignored_arguments(result: ToolResult, ignored: list[str]) -> ToolResult:
    """Note the discarded argument names on a success envelope; leave a failure alone."""
    if not ignored or result.isError or result.structuredContent is None:
        return result
    return envelope.success_result({**result.structuredContent, "ignored_arguments": ignored})


def _missing_required(verb: str, forwarded: Mapping[str, Any]) -> list[str]:
    """The wrapped verb's unmet required parameters, under their *lane* names.

    Without this check a call like ``improve action=loop`` (no ``loop_action``)
    reached the handler with the argument absent and surfaced as a raw Python
    ``TypeError`` -- the one path on the lane surface that broke the
    structured-envelope contract. Named after the lane-side parameter
    (``loop_action``, not ``action``) because that is the name the caller can
    actually pass.
    """
    return [
        _lane_parameter_name(verb, name)
        for name, parameter in _handler_parameters(verb).items()
        if parameter.default is inspect.Parameter.empty
        and parameter.kind
        in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        and name not in forwarded
    ]


def _implemented(actions: tuple[str, ...]) -> tuple[str, ...]:
    handlers = _flat_handlers()
    return tuple(verb for verb in actions if verb in handlers)


# ---------------------------------------------------------------------------
# Registration.
# ---------------------------------------------------------------------------


def _home_payload() -> dict[str, Any]:
    """The router's own read: which lanes exist, their rails, and their actions.

    ``home`` declares no lane memberships -- it routes into the other five rather
    than running a process of its own -- so its table cannot be generated the way
    theirs are. It answers the one question the declaration *can* answer for it.
    """
    return envelope.read_ok(
        {
            "lanes": [
                {
                    "id": lane,
                    "stages": [
                        {"id": stage.id, "title": stage.title, "handoff": stage.handoff}
                        for stage in process_model.LANE_STAGES[lane]
                    ],
                    "actions": _lane_action_entries(lane),
                }
                for lane in process_model.LANES
                if lane != "home"
            ]
        }
    )


def _lane_action_entries(lane: str) -> list[dict[str, Any]]:
    """``home``'s per-action rows: the verb, what it does, where on the rail.

    The narration is the same string the lane's own description renders, and it
    is the part a router actually needs -- it is what disambiguates
    ``gapfill_discover`` from ``suggestions_read``, and it is where the billed
    markers live. Emitting bare verb names dropped it one line before
    serialization and sent the caller back to read a lane description instead.
    """
    rail = [stage.id for stage in process_model.LANE_STAGES[lane]]
    narrations = _lane_narrations(lane)
    entries = []
    for verb in lane_actions(lane):
        position = narrations[verb][0][0]
        entries.append(
            {
                "id": verb,
                "narration": "; ".join(narration for _position, narration in narrations[verb]),
                "stage": rail[position] if position < len(rail) else None,
            }
        )
    return entries


def _register_router(mcp: FastMCP, lane: str, purpose: str) -> None:
    """A lane with no declared actions is a router: no selector, no arguments."""

    @mcp.tool(name=lane, description=purpose)
    def route() -> ToolResult:
        return envelope.success_result(_home_payload())


def register_lane_dispatcher(mcp: FastMCP, lane: str, purpose: str) -> None:
    """Register ``lane``'s dispatcher, generated from the process-model declaration.

    Routing prose lives in exactly two places on this surface: ``_INSTRUCTIONS``
    in ``server.py`` (one copy, server-wide) and the description assembled here
    (one copy per lane). No individual tool's description narrates the surface;
    it says what that tool does and names at most two siblings, and only to
    disambiguate.

    Args:
        mcp: The server to register on.
        lane: One of :data:`~knotica.core.process_model.LANES`.
        purpose: The lane's own description prose, in the four-part shape --
            what it does, ``Does NOT``, ``Requires``, ``Returns`` -- with the
            billed actions named in full. The generated action list is appended
            to it, so per-action wording belongs in the declaration's narrations
            (``LANE_MEMBERSHIP``), never hand-copied into a lane module.
    """
    actions = lane_actions(lane)
    if not actions:
        _register_router(mcp, lane, purpose)
        return

    def dispatch(**arguments: Any) -> ToolResult:
        return _dispatch(lane, actions, arguments)

    dispatch.__name__ = lane
    dispatch.__signature__ = _lane_signature(lane, actions)  # type: ignore[attr-defined]
    mcp.tool(name=lane, description=f"{purpose}\nActions:\n{_action_lines(lane)}")(dispatch)
