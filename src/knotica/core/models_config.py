"""``[models]`` config -- the operator-facing worker/judge/query snapshot overrides.

Side-effect-free w.r.t. ``config.toml``, mirroring
:func:`knotica.core.loop_cadence_config.resolve_loop_cadence_config`: reads only
the ``[models]`` table of ``~/.config/knotica/config.toml``, never a socket,
never a module-level cache. A missing file or a missing table is not an error.
Each key is independently optional -- setting only ``worker`` leaves ``judge``
and ``query`` at their packaged defaults.

``query`` names the model that drives conversational-routing surfaces (e.g. the
MCP client-as-brain guidance); it is intentionally excluded from
:meth:`ModelsConfig.to_harness_base`, which folds only ``worker``/``judge`` into
the eval harness's fingerprinted config -- ``query`` never rotates
``harness_version``.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass

from knotica.core.config import config_file_path
from knotica.evals.config import JUDGE_SNAPSHOT, WORKER_SNAPSHOT, HarnessConfig

__all__ = [
    "MODELS_CONFIG_SECTION",
    "QUERY_SNAPSHOT",
    "ModelsConfig",
    "resolve_models_config",
]

#: The ``[models]`` table this module reads from ``config.toml``.
MODELS_CONFIG_SECTION = "models"

#: The packaged default query-model snapshot (conversational-routing surfaces).
QUERY_SNAPSHOT = "claude-sonnet-5"


@dataclass(frozen=True, slots=True)
class ModelsConfig:
    """The resolved ``[models]`` worker/judge/query snapshot overrides.

    Each field defaults to its packaged constant, so ``ModelsConfig()`` is the
    shipped default.
    """

    worker: str = WORKER_SNAPSHOT
    judge: str = JUDGE_SNAPSHOT
    query: str = QUERY_SNAPSHOT

    def to_harness_base(self) -> HarnessConfig:
        """Return the fingerprinted eval-harness base -- ``query`` excluded.

        ``query`` never folds into ``harness_version``; only ``worker``/``judge``
        are eval-harness knobs.
        """
        return HarnessConfig(worker_snapshot=self.worker, judge_snapshot=self.judge)


def resolve_models_config(
    config_path: str | os.PathLike[str] | None = None,
) -> ModelsConfig:
    """Parse ``[models]`` fresh, falling back to packaged defaults.

    Missing file/table returns an all-packaged-defaults instance. Each key
    resolves independently -- a partial ``[models]`` table only overrides the
    keys it names.
    """
    section = _load_models_section(config_path)
    return ModelsConfig(
        worker=_resolve_str(section, "worker", WORKER_SNAPSHOT),
        judge=_resolve_str(section, "judge", JUDGE_SNAPSHOT),
        query=_resolve_str(section, "query", QUERY_SNAPSHOT),
    )


def _resolve_str(section: Mapping[str, object], key: str, default: str) -> str:
    raw = section.get(key, default)
    return raw if isinstance(raw, str) else default


def _load_models_section(config_path: str | os.PathLike[str] | None) -> Mapping[str, object]:
    """Return the ``[models]`` table, or an empty mapping when absent/unreadable."""
    file = config_file_path(config_path)
    try:
        raw = file.read_bytes()
    except OSError:
        return {}
    try:
        config = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return {}
    section = config.get(MODELS_CONFIG_SECTION)
    return section if isinstance(section, Mapping) else {}
