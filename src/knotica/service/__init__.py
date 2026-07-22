"""Loop-watcher OS service lifecycle -- install / uninstall / status / supervise.

The self-improvement loop runs as an automatically supervised background service
(launchd on macOS, systemd on Linux) so the user starts no process by hand. This
package owns lifecycle management only; loop *semantics* are unchanged. The
installed unit runs ``python -m knotica.service`` (see :mod:`knotica.service.__main__`),
which supervises every configured topic, resolving the topic set from config
fresh on each cycle.

Public surface is re-exported from :mod:`knotica.service.manager`.
"""

from knotica.service.manager import (
    SERVICE_LABEL,
    SYSTEMD_UNIT_NAME,
    InstallPlan,
    ServiceSpec,
    ServiceStatus,
    UninstallPlan,
    build_spec,
    daemon_argv,
    detect_platform,
    install,
    resolve_watched_topics,
    status,
    supervise,
    uninstall,
)

__all__ = [
    "SERVICE_LABEL",
    "SYSTEMD_UNIT_NAME",
    "InstallPlan",
    "ServiceSpec",
    "ServiceStatus",
    "UninstallPlan",
    "build_spec",
    "daemon_argv",
    "detect_platform",
    "install",
    "resolve_watched_topics",
    "status",
    "supervise",
    "uninstall",
]
