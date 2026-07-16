"""The DSPy program adapter -- the one seam where DSPy meets the baseline runner.

The eval harness scores the vault's headless *query* operation by driving a
golden question set through :func:`dspy.Evaluate`. DSPy expects a *program*: a
:class:`dspy.Module` it can call as ``program(**example.inputs())`` for each
example. :func:`BaselineProgram` builds that program by wrapping a
:class:`~knotica.evals.runner.BaselineRunner`, so DSPy's per-example loop,
parallelism, and result collection run over our own retrieve->read->synthesize
runner without DSPy ever owning an LM.

This adapter is the **Phase-3a swap point**: today it wraps
:class:`~knotica.evals.runner.MessagesApiRunner`; a later compiled DSPy program
(itself already a ``dspy.Module``) can take its place behind the same ``program``
seam, scored by the same devset and metric. It is the *only* module in the
package that touches ``dspy``.

Three rules hold this seam:

* **No ``dspy.LM``, no ``dspy.Predict``/``ChainOfThought``.** ``forward`` calls
  only our own ``runner.run``, so ``dspy.settings.lm`` is never required and the
  program runs fully offline -- a ``FakeLLMClient`` behind the runner drives an
  entire ``dspy.Evaluate`` pass with zero network.

* **Lazy ``dspy`` import, class built inside a factory.** ``dspy`` (the heavy
  ``evals`` dependency-group package) is imported lazily, so importing this
  module never forces DSPy's import cost onto a path that does not want it --
  including ``import knotica.evals`` after a wiring step re-exports
  :func:`BaselineProgram` from the package ``__init__``. Because the program
  class must inherit from ``dspy.Module`` (which is only available after the
  lazy import), it is defined *inside* a factory rather than at module scope;
  :func:`BaselineProgram` is exposed as a module-level constructor callable so
  every call site reads ``BaselineProgram(store, topic, runner)`` unchanged. The
  class is built once and cached on first construction.

* **Stateless beyond its bound collaborators.** A program instance holds only
  the ``(store, topic, runner)`` it was constructed with -- all read-only after
  construction -- and ``forward`` mutates no per-instance state (it returns a
  fresh :class:`dspy.Prediction` each call). DSPy shares a *single* program
  instance across threads without deep-copying it, so this immutability is what
  makes the adapter safe at ``num_threads > 1``; any per-run mutable state (the
  judge cache) lives in downstream collaborators that own their own thread
  safety. (v1 pins ``num_threads = 1`` regardless.)

The returned prediction carries the runner's exact fields --
``answer``/``citations``/``usage`` -- as a native :class:`dspy.Prediction`, so
the scorer duck-types on ``.citations`` and the harness reads ``.usage`` off the
DSPy result unchanged.
"""

from typing import TYPE_CHECKING

from knotica.evals.runner import BaselineRunner
from knotica.store import VaultStore

if TYPE_CHECKING:
    import dspy

__all__ = [
    "BaselineProgram",
]

#: Cache for the lazily-defined ``dspy.Module`` subclass. ``None`` until the first
#: :func:`BaselineProgram` call imports ``dspy`` and builds the class; reused for
#: every later construction so all program instances share one class identity.
_program_class: type | None = None


def BaselineProgram(store: VaultStore, topic: str, runner: BaselineRunner) -> "dspy.Module":
    """Construct the baseline DSPy program bound to ``(store, topic, runner)``.

    A module-level *constructor callable* (not a class) so that importing this
    module -- and therefore ``import knotica.evals`` -- never pulls ``dspy``; the
    program class inherits from ``dspy.Module`` and so can only be defined after
    the lazy import (see the module docstring). The returned object is a real
    ``dspy.Module`` instance: ``dspy.Evaluate`` invokes it as
    ``program(**example.inputs())`` and gets back a :class:`dspy.Prediction`.

    Args:
        store: The (clone) vault store the wrapped runner reads from.
        topic: The topic whose ``query.md`` prompt the runner drives.
        runner: The headless baseline query executor to wrap -- Phase 2 passes a
            :class:`~knotica.evals.runner.MessagesApiRunner`; a compiled DSPy
            program can replace it behind the same seam later.
    """
    return _baseline_program_class()(store, topic, runner)


def _baseline_program_class() -> type:
    """Return the ``dspy.Module`` subclass, importing ``dspy`` and building it once."""
    global _program_class
    if _program_class is None:
        _program_class = _build_baseline_program_class()
    return _program_class


def _build_baseline_program_class() -> type:
    """Import ``dspy`` and define the ``BaselineProgram`` module class in its scope.

    Kept in its own function so the lazy ``import dspy`` and the class definition
    that depends on it are colocated and run exactly once (memoized by
    :func:`_baseline_program_class`).
    """
    import dspy

    # Named to match the public constructor so instances report the intended type
    # (``type(program).__name__ == "BaselineProgram"``); the class is lexically
    # scoped here and never bound at module level, so there is no name clash with
    # the factory above.
    class BaselineProgram(dspy.Module):
        """A ``dspy.Module`` that answers via a wrapped :class:`BaselineRunner`.

        Holds only its construction-bound collaborators and does no per-instance
        mutation in ``forward``, so the single instance DSPy shares across threads
        is safe to reuse.
        """

        def __init__(self, store: VaultStore, topic: str, runner: BaselineRunner) -> None:
            super().__init__()
            self.store = store
            self.topic = topic
            self.runner = runner

        def forward(self, question: str) -> dspy.Prediction:
            """Answer ``question`` via the runner, wrapped as a native prediction.

            Calls only ``self.runner.run`` -- no ``dspy.Predict``/LM -- and returns
            a :class:`dspy.Prediction` carrying the runner's exact
            ``answer``/``citations``/``usage`` fields.
            """
            prediction = self.runner.run(self.store, self.topic, question)
            return dspy.Prediction(
                answer=prediction.answer,
                citations=prediction.citations,
                usage=prediction.usage,
            )

    return BaselineProgram
