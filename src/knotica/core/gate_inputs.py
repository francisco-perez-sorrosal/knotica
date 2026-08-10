"""Fingerprint of everything a source-gate verdict depends on.

A stamped ``gate_outcome`` is replayed rather than recomputed, which is right
for the case it was built for: re-submitting a byte-identical candidate must
not bill a second eval. It is wrong the moment any *input* to that verdict has
moved, and the record carried nothing that could tell the two apart -- the
suggestion id keyed the replay, and a suggestion id does not change when the
corpus, the bar, or the instrument does.

The observed failure: a candidate was rebuilt, the golden set was replaced
(9 -> 21 questions), and the baseline was corrected from 0.9548 to 0.6562. Every
subsequent submit -- in either mode -- returned the *original* verdict, quoting
the *original* baseline, indistinguishable from a fresh evaluation. Three
inputs had changed and none of them could invalidate anything.

This module makes the dependency explicit. Four components, each one a thing a
verdict is a function of:

* ``candidate_tree_sha`` -- what was evaluated. Git's tree object, so a rewrite
  that lands the same bytes correctly compares equal.
* ``golden_manifest_sha`` -- what it was evaluated *against*: the frozen golden
  set's content digest, straight from its ``MANIFEST.json``.
* ``baseline_scalar`` -- the bar it had to clear.
* ``harness_version`` -- the instrument that measured it. Already the codebase's
  authority on scalar comparability (``compute_gate`` returns ``unknown`` rather
  than rank two scalars from different harnesses); a verdict is no more
  replayable across an instrument change than a scalar is comparable across one.

**Unknown is never treated as unchanged.** :meth:`GateInputs.diff` reports a
component as changed when the two sides disagree *or* when exactly one side is
unknown, and :func:`from_record` yields ``None`` for a record that predates this
fingerprint entirely. Both push toward re-evaluating. That asymmetry is
deliberate and the cost is asymmetric too: a needless re-evaluation costs one
eval, while a wrongly-replayed verdict silently reports a measurement that was
never taken -- the incident above.

Cold-start safe: ``knotica.evals.golden`` keeps ``dspy`` behind ``TYPE_CHECKING``
and never touches ``anthropic``, so importing it here costs nothing on the MCP
cold-start path. :func:`current_harness_version` is the one component that can
genuinely be unavailable -- it folds in the installed ``dspy`` version, which a
lean install without the ``evals`` extra does not have -- so it is imported
lazily and degrades to ``None`` rather than breaking an install that can still
run a dry-run perfectly well.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from knotica.evals.golden import golden_manifest_path
from knotica.store import VaultStore

__all__ = [
    "GateInputs",
    "capture",
    "current_harness_version",
    "from_record",
]

#: Key the fingerprint is stamped under inside a ``gate_outcome`` record.
INPUTS_KEY = "inputs"


@dataclass(frozen=True, slots=True)
class GateInputs:
    """The four values a source-gate verdict is a function of.

    Every field is optional because every one of them can genuinely be unknown
    -- an absent golden manifest, an unfrozen baseline, a lean install with no
    ``dspy``. See the module docstring for why unknown resolves toward
    re-evaluating rather than toward replay.
    """

    candidate_tree_sha: str | None = None
    golden_manifest_sha: str | None = None
    baseline_scalar: float | None = None
    harness_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready mapping, stamped into ``gate_outcome[INPUTS_KEY]``."""
        return asdict(self)

    def diff(self, other: "GateInputs") -> tuple[str, ...]:
        """Component names that differ between ``self`` and ``other``.

        A component counts as differing when the two sides hold unequal known
        values, **or** when exactly one side knows its value. Only
        both-sides-unknown compares equal: that is genuinely no evidence of
        change, whereas one-sided knowledge is a change in what can be verified
        and must not be waved through as sameness.
        """
        changed: list[str] = []
        for field in self.to_dict():
            mine = getattr(self, field)
            theirs = getattr(other, field)
            if mine is None and theirs is None:
                continue
            if mine is None or theirs is None or mine != theirs:
                changed.append(field)
        return tuple(changed)


def capture(
    store: VaultStore,
    topic: str,
    *,
    candidate_tree_sha: str | None,
    baseline_scalar: float | None,
) -> GateInputs:
    """Fingerprint the gate inputs as they stand right now.

    ``candidate_tree_sha`` and ``baseline_scalar`` are supplied by the caller
    because only it knows which candidate and which bar it means -- the gate
    stamps the tip it actually evaluated and the baseline it actually compared
    against, while a submit-time check reads whatever is current. The other two
    are read here.
    """
    return GateInputs(
        candidate_tree_sha=candidate_tree_sha,
        golden_manifest_sha=read_golden_manifest_sha(store, topic),
        baseline_scalar=None if baseline_scalar is None else float(baseline_scalar),
        harness_version=current_harness_version(),
    )


def from_record(gate_outcome: object) -> GateInputs | None:
    """Recover the fingerprint a ``gate_outcome`` was stamped with.

    ``None`` when the record carries no fingerprint at all -- a verdict stamped
    before this existed. Such a record cannot be shown to still apply, so its
    caller must re-evaluate; that is precisely the reported incident's record,
    and it should not be replayed.
    """
    if not isinstance(gate_outcome, dict):
        return None
    raw = gate_outcome.get(INPUTS_KEY)
    if not isinstance(raw, dict):
        return None
    baseline = raw.get("baseline_scalar")
    return GateInputs(
        candidate_tree_sha=_as_str(raw.get("candidate_tree_sha")),
        golden_manifest_sha=_as_str(raw.get("golden_manifest_sha")),
        baseline_scalar=float(baseline) if isinstance(baseline, (int, float)) else None,
        harness_version=_as_str(raw.get("harness_version")),
    )


def read_golden_manifest_sha(store: VaultStore, topic: str) -> str | None:
    """The frozen golden set's content digest from its sibling ``MANIFEST.json``.

    ``None`` for an absent, unreadable, or malformed manifest. Deliberately does
    not go through :func:`knotica.evals.golden.load`, which additionally
    verifies the digest against ``golden.jsonl``'s bytes and raises on a
    mismatch: a corrupt golden set is a real problem, but it is the eval's
    problem to report, not something a cache-key read should raise from.
    """
    path = golden_manifest_path(topic)
    if not store.exists(path):
        return None
    try:
        data = json.loads(store.read_text(path))
    except (ValueError, OSError):
        return None
    return _as_str(data.get("sha256")) if isinstance(data, dict) else None


def current_harness_version() -> str | None:
    """The instrument fingerprint an eval would run under right now.

    ``None`` on a lean install: ``harness_version`` folds in the installed
    ``dspy`` version, which the base install does not carry. Imported lazily for
    the same reason -- this module sits under the MCP cold-start path, where the
    ``evals`` extra may legitimately be absent.
    """
    try:
        from knotica.evals.config import harness_version
        from knotica.evals.judge import JUDGE_PROMPT_HASH

        return harness_version(JUDGE_PROMPT_HASH)
    except Exception:  # noqa: BLE001 -- an unavailable instrument is data, not a failure
        return None


def _as_str(value: object) -> str | None:
    """``value`` as a non-empty string, else ``None``."""
    return value if isinstance(value, str) and value else None
