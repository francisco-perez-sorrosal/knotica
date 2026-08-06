#!/usr/bin/env bash
# diagram_regen.sh — Regenerate the architecture SVGs from the LikeC4 model.
#
# Run by the `diagram-regen` pre-commit hook when a staged path matches `\.c4$`,
# and safe to run by hand at any time.
#
# WHY THIS EXISTS. `.ai-state/DESIGN.md` § 3a is defined as one row per `component`
# element in the model, and both architecture documents embed the rendered views.
# Nothing kept the renders in step with the model, and the cost of that was already
# paid once: the committed model sat at five components -- one of them named `mcp/`,
# a package `dec-009` renamed away the day the model was written -- while § 3 had
# grown to describe sixteen. A rendered SVG with no regeneration path is a second
# copy of the architecture that drifts exactly like the first one did (td-045).
#
# WHY NOT THE UPSTREAM SCRIPT. Praxion ships `scripts/diagram-regen-hook.sh` and
# wires it into its own pre-commit config, but it is not part of
# `claude/project-baseline/`, so `/onboard-project` never installs it -- while both
# architecture templates tell every managed project to run it. This is the local
# equivalent, narrowed to what this repo has: one model, one output directory.
#
# TWO THINGS THAT ARE NOT OBVIOUS AND COST AN HOUR EACH:
#
#   1. `likec4 gen` takes the *directory* holding the sources, not the `.c4` file.
#      Passing the file exits with "no views found", which reads like a model
#      defect rather than a CLI-usage one.
#   2. The layout engine is load-bearing, not cosmetic. d2's default (dagre) packs
#      the sixteen-component view into ~2800px of overlapping edge labels; `elk`
#      routes it orthogonally and legibly. This is why `--layout=elk` is pinned
#      here rather than left to whatever a caller's d2 defaults to.
#
# Skips gracefully (exit 0, warning on stderr) when likec4 or d2 is absent, so a
# contributor without the toolchain is never blocked from committing.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_DIR="${REPO_ROOT}/docs/diagrams/architecture/src"
OUT_DIR="${REPO_ROOT}/docs/diagrams/architecture/rendered"
NORMALIZE="${REPO_ROOT}/scripts/normalize_d2_svg.sh"

#: d2 layout engine. See note 2 above -- do not drop this to the default.
D2_LAYOUT="${D2_LAYOUT:-elk}"

#: Padding around the rendered graph, in px.
D2_PAD="${D2_PAD:-20}"

for binary in likec4 d2; do
    if ! command -v "${binary}" >/dev/null 2>&1; then
        echo "diagram_regen: ${binary} not found; skipping diagram regeneration." >&2
        echo "diagram_regen: install with 'npm i -g likec4' and 'brew install d2'." >&2
        exit 0
    fi
done

if [ ! -d "${SRC_DIR}" ]; then
    echo "diagram_regen: no model directory at ${SRC_DIR}; nothing to do." >&2
    exit 0
fi

mkdir -p "${OUT_DIR}"

# likec4 emits one .d2 per view, plus an index.d2 that is a navigation stub with no
# view behind it. Rendering the stub produces an empty SVG, so it is removed rather
# than rendered.
#
# `likec4 gen` EXITS 0 ON A BROKEN MODEL. An unresolvable reference or a parse error
# is reported on stderr and the generator still writes every .d2 it managed to
# build -- silently dropping the edges it could not resolve. Trusting its exit code
# means committing a diagram with edges missing and no signal that anything failed,
# which is worse than not rendering at all. So stderr is the real exit status here:
# scan it, and fail on any diagnostic. Verified by canary -- pointing one edge at a
# nonexistent element makes `likec4 gen` exit 0 and makes this script exit 1.
gen_log="$(mktemp)"
trap 'rm -f "${gen_log}"' EXIT
likec4 gen d2 "${SRC_DIR}" -o "${OUT_DIR}" >/dev/null 2>"${gen_log}"
if grep -qE 'Could not resolve|Validation failed|Expecting:|ERROR' "${gen_log}"; then
    echo "diagram_regen: the model has errors -- refusing to render a diagram that would" >&2
    echo "diagram_regen: silently drop the parts likec4 could not resolve." >&2
    sed 's/^/  /' "${gen_log}" >&2
    exit 1
fi
rm -f "${OUT_DIR}/index.d2"

rendered=0
for d2_file in "${OUT_DIR}"/*.d2; do
    [ -e "${d2_file}" ] || continue
    svg="${d2_file%.d2}.svg"
    if ! d2 --layout="${D2_LAYOUT}" --pad "${D2_PAD}" "${d2_file}" "${svg}" >/dev/null 2>&1; then
        echo "diagram_regen: d2 failed rendering ${d2_file}" >&2
        d2 --layout="${D2_LAYOUT}" --pad "${D2_PAD}" "${d2_file}" "${svg}" >&2 || true
        exit 1
    fi
    "${NORMALIZE}" "${svg}"
    rendered=$((rendered + 1))
done

if [ "${rendered}" -eq 0 ]; then
    echo "diagram_regen: the model produced no views -- check it declares a 'views' block." >&2
    exit 1
fi

# Stage the artifacts only when running inside a commit, so a manual invocation
# leaves the index alone.
if [ -n "${PRE_COMMIT:-}" ]; then
    git add "${OUT_DIR}"
fi

echo "diagram_regen: rendered ${rendered} view(s) to ${OUT_DIR#"${REPO_ROOT}"/} (layout=${D2_LAYOUT})"
