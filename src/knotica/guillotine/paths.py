"""Topic-scoped vault paths for Memory Guillotine artifacts."""

from __future__ import annotations

from pathlib import PurePosixPath


def reports_dir(topic: str) -> str:
    """Vault-relative directory for guillotine report, diff, and JSON sidecars."""
    return f"{topic}/reports/guillotine"


def is_guillotine_report_path(path: str) -> bool:
    """Return whether ``path`` lives under ``{topic}/reports/guillotine/``."""
    parts = PurePosixPath(path.replace("\\", "/")).parts
    if len(parts) < 3:
        return False
    return parts[-3] == "reports" and parts[-2] == "guillotine"
