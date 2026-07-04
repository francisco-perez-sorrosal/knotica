"""``knotica prompt <op>`` -- render a vault-resolved operation prompt body.

The output *is* the payload: the resolved prompt body (markdown) goes straight
to stdout. This subcommand and the MCP ``prompts/get`` handler share the one
resolver ``core.prompts.get_prompt`` (single source of truth), so the two
surfaces serve byte-identical bodies -- the vault ``prompts/`` files are
simultaneously the alias UX surface and the self-improvement substrate.

``--source``/``--question``/``--verdict`` mirror the per-operation MCP prompt
arguments; like that handler, they are surface parity for the client's own
substitution -- only ``--topic`` steers resolution (the body's topic-inference
policy resolves the rest). Config is resolved fresh per invocation. Unconfigured
prints the shared message to stderr and exits 3; a malformed vault surfaces the
resolver's typed fault as an error.
"""

import argparse

from knotica.cli.common import (
    EXIT_ERROR,
    EXIT_SUCCESS,
    common_parent,
    console_from_args,
    unconfigured,
)
from knotica.core.errors import KnoticaError
from knotica.core.prompts import OPERATIONS, get_prompt

__all__ = ["configure", "run"]


def configure(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register the ``prompt`` subcommand and its flags."""
    parser = subparsers.add_parser(
        "prompt",
        parents=[common_parent()],
        help="render a vault-resolved operation prompt body",
        description="Render an operation prompt body to stdout (the alias source of truth).",
    )
    parser.add_argument("operation", choices=OPERATIONS, help="operation prompt to render")
    parser.add_argument("--topic", default="", help="topic whose override to prefer (else root)")
    parser.add_argument("--source", help="source hint (parity with the MCP ingest prompt)")
    parser.add_argument("--question", help="question hint (parity with the MCP query prompt)")
    parser.add_argument("--verdict", help="verdict hint (parity with the MCP curate prompt)")
    return parser


def run(args: argparse.Namespace) -> int:
    """Resolve config fresh, render the body to stdout, or fail per the contract."""
    console = console_from_args(args)
    try:
        resolved = get_prompt(args.operation, args.topic)
    except KnoticaError as error:
        console.error(error.message)
        console.error(f"To fix: {error.fix}")
        return EXIT_ERROR

    if not resolved.configured:
        return unconfigured(console)

    console.data(resolved.body)
    return EXIT_SUCCESS
