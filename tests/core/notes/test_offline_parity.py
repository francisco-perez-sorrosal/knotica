"""Standing proof that anchor resolution cannot reach an LLM.

The notes overlay promises that an install with no LLM credentials resolves
anchors *identically* to one with them -- the resolution ladder is deterministic
and lexical throughout, and the optional adjudicator was always scoped to the
human-facing review surface, never to the read path.

Asserting "the statuses are the same offline" cannot fail while no adjudicator
exists: with nothing to omit, the claim is vacuously true either way. So this
module proves the **structural** property instead, which can fail and would fail
loudly the moment someone wired a model call into the read path: the transitive
import closure of the resolution modules contains no LLM client at all.

That is strictly stronger than status parity. Parity says the two paths agree;
import purity says the offline path is the *only* path, because the code that
would call a model is not reachable from here.

A subprocess is not incidental. By the time pytest runs, ``sys.modules`` already
holds most of the tree -- including modules a resolution import never touches --
so an in-process check would pass on imports it did not cause and prove nothing.
Each case imports into a clean interpreter and reports what that import alone
pulled in.
"""

import subprocess
import sys

import pytest

#: Entry points a caller actually reaches the resolution ladder through.
#: ``store`` is the read side, ``reconcile`` the drift-queue derivation; between
#: them they pull ``resolve``, ``scoring``, ``candidates``, ``anchor`` and
#: ``supersession`` transitively.
RESOLUTION_ENTRY_POINTS = (
    "knotica.core.notes.store",
    "knotica.core.notes.reconcile",
    "knotica.core.notes.resolve",
)

#: Any client capable of issuing a model call. Membership is what makes the read
#: path non-deterministic, not whether it is configured -- an unconfigured client
#: on the import graph is one edit away from a call.
LLM_CLIENT_PACKAGES = frozenset({"anthropic", "dspy", "openai", "litellm", "httpx", "requests"})


def _imported_top_level_packages(module: str) -> frozenset[str]:
    """Top-level packages present in ``sys.modules`` after importing ``module`` alone."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import sys; import {module}; "
            "print('\\n'.join(sorted({m.split('.')[0] for m in sys.modules})))",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"importing {module} failed:\n{result.stderr}"
    return frozenset(result.stdout.split())


@pytest.mark.parametrize("module", RESOLUTION_ENTRY_POINTS)
def test_resolution_imports_no_llm_client(module: str) -> None:
    """The read path cannot reach a model, so offline and online resolve identically."""
    reachable = _imported_top_level_packages(module)

    assert not (reachable & LLM_CLIENT_PACKAGES), (
        f"{module} transitively imports an LLM client "
        f"({sorted(reachable & LLM_CLIENT_PACKAGES)}). Anchor resolution must stay "
        "deterministic and offline-identical: an adjudicator belongs on the "
        "human-facing review surface, never on the read path."
    )
