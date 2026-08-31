"""Where an eval run's outputs land inside a topic -- the vault-relative path grammar.

Three files are written per topic by the harness: the appended eval history, the
per-run reproducibility manifest, and the once-frozen token budget. Their layout is
declared here rather than beside whichever stage happens to write each one, because
the budget path is *read* during scoring and *written* during persistence, and the
metrics path is read by both the generation counter and the prior-record lookup --
so no single stage owns them.

The three helpers keep their leading underscore and are imported by name across
module boundaries: "private" inside this package reads as package-private, the same
convention ``core/records/fields.py`` established.
"""

#: The topic's hidden per-topic state directory (mirrors ``core.operations.create_topic``).
_KNOTICA_DIR = ".knotica"
#: The topic's eval-history file -- one appended line per generation.
_METRICS_FILENAME = "metrics.jsonl"
#: The frozen per-topic token budget target, written once at generation 0.
_EVAL_TOML_FILENAME = "eval.toml"
#: The per-run reproducibility manifests directory (``<topic>/.knotica/eval-runs/gen-<N>/``).
_EVAL_RUNS_DIRNAME = "eval-runs"
#: The per-run manifest filename inside a generation directory.
_MANIFEST_FILENAME = "manifest.json"


def _metrics_path(topic: str) -> str:
    """Vault-relative path of the topic's eval-history file."""
    return f"{topic}/{_KNOTICA_DIR}/{_METRICS_FILENAME}"


def _eval_toml_path(topic: str) -> str:
    """Vault-relative path of the topic's frozen budget file."""
    return f"{topic}/{_KNOTICA_DIR}/{_EVAL_TOML_FILENAME}"


def _manifest_path(topic: str, generation: int) -> str:
    """Vault-relative path of this run's reproducibility manifest."""
    return f"{topic}/{_KNOTICA_DIR}/{_EVAL_RUNS_DIRNAME}/gen-{generation}/{_MANIFEST_FILENAME}"
