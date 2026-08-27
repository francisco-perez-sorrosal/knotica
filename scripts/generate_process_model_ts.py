#!/usr/bin/env python3
"""Regenerate the dashboard's static mirror of the process model.

`src/knotica/core/process_model.py` is the single declaration of the six
process lanes and their stage rails. This script projects it into
`dashboard/src/processModel.ts` -- structure only (lane order, stage ids,
titles, handoff flags), no predicates: a stage's live state is served by the
`wiki_status` tool, not baked into this file. The mirror is the fallback the
dashboard renders a lane's rail from before that call returns.

The declaration is read by **importing** `knotica.core.process_model`, never
by parsing its source -- the same discipline every other generator in this
repo follows for its own source of truth.

Regeneration is deterministic: declaration order is preserved verbatim and no
timestamp is written, so two runs against the same declaration produce a
byte-identical file. That determinism is what lets `make verify` gate the
mirror with a plain regenerate-then-`git diff --exit-code`, the same
instrument already used for the committed dashboard artifact -- a hand edit to
the generated file, or a `process_model.py` change without a regeneration,
both fail that gate.

Exit 0 always; the caller (`make verify`) diffs the file this script writes.
"""

from __future__ import annotations

from pathlib import Path

from knotica.core import process_model

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = REPO_ROOT / "dashboard" / "src" / "processModel.ts"

_HEADER = """\
// GENERATED FILE -- do not edit by hand.
// Source of truth: src/knotica/core/process_model.py
// Regenerate: uv run --extra evals python scripts/generate_process_model_ts.py
//
// Structure only, no predicates: a stage's live state is served by the
// `wiki_status` tool. This mirror is the fallback the dashboard renders a
// lane's rail from before that call returns, or when the server is
// unreachable. Stage order is the array order below.

export interface ProcessStage {
  readonly id: string;
  readonly title: string;
  readonly handoff: boolean;
}
"""


def _quote(value: str) -> str:
    """A double-quoted TS string literal, escaped independently of JSON's rules."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _render_lanes() -> str:
    entries = ",\n".join(f"  {_quote(lane)}" for lane in process_model.LANES)
    return f"export const LANES: readonly string[] = [\n{entries},\n];\n"


def _render_stage(stage: process_model.Stage) -> str:
    handoff = "true" if stage.handoff else "false"
    return f"    {{ id: {_quote(stage.id)}, title: {_quote(stage.title)}, handoff: {handoff} }},"


def _render_lane_stages() -> str:
    lines = ["export const LANE_STAGES: Readonly<Record<string, readonly ProcessStage[]>> = {"]
    for lane in process_model.LANES:
        stages = process_model.LANE_STAGES[lane]
        if not stages:
            lines.append(f"  {_quote(lane)}: [],")
            continue
        lines.append(f"  {_quote(lane)}: [")
        lines.extend(_render_stage(stage) for stage in stages)
        lines.append("  ],")
    lines.append("};")
    return "\n".join(lines)


def render() -> str:
    """The full generated file contents, ending in exactly one trailing newline."""
    return "\n".join([_HEADER, _render_lanes(), _render_lane_stages(), ""])


def main() -> int:
    OUTPUT.write_text(render(), encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
