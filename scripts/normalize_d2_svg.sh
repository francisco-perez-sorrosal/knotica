#!/usr/bin/env bash
# normalize_d2_svg.sh — Scrub the volatile d2 version stamp from rendered SVGs.
#
# WHY: d2 stamps `data-d2-version="<ver>"` into every SVG it renders. That string
# varies by *build provenance*, not just by version number — a Homebrew d2 0.7.1
# emits `0.7.1`, while the standalone release tarball emits `v0.7.1`. So two people
# on different d2 builds regenerate a byte-identical diagram and still produce a
# diff, which trains everyone to ignore diagram diffs — the exact habit that lets a
# real one through. Normalizing the attribute to a fixed constant makes the stamp
# non-load-bearing and kills the whole drift class. Knotica renders
# from one place today -- scripts/diagram_regen.sh, run by the pre-commit hook -- so
# there is one caller; the script stays separate anyway, because the moment a CI
# render is added it must apply the identical rule, and a rule inlined in one
# renderer is a rule the second renderer will not have.
#
# Idempotent. Operates in place via a temp-file swap so it is portable across
# BSD (macOS) and GNU (Linux/CI) sed without relying on `sed -i` flag differences.
# Files lacking the attribute (e.g. likec4-native SVGs) pass through untouched.
#
# Usage: normalize_d2_svg.sh <svg-file>...
set -euo pipefail

# Synthetic, build-independent placeholder. Any constant works; this one reads as
# deliberately scrubbed. The renderer's real version is a property of whoever ran
# it, not of the diagram, so it has no business in the committed artifact.
NORMALIZED_VERSION="pinned"

for svg in "$@"; do
    [ -f "${svg}" ] || continue
    tmp="${svg}.normtmp"
    sed -E "s/data-d2-version=\"[^\"]*\"/data-d2-version=\"${NORMALIZED_VERSION}\"/g" \
        "${svg}" > "${tmp}"
    mv "${tmp}" "${svg}"
done
