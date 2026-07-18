"""Packaged, single-file dashboard artifact for both HTTP and ``ui://`` mounts."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

__all__ = ["dashboard_html"]


def dashboard_html() -> str:
    """Load the wheel-packaged dashboard, with a source-tree fallback for authors."""
    packaged = resources.files("knotica.dashboard").joinpath("app.html")
    if packaged.is_file():
        return packaged.read_text(encoding="utf-8")

    source_artifact = Path(__file__).resolve().parents[3] / "dashboard" / "dist" / "index.html"
    if source_artifact.is_file():
        return source_artifact.read_text(encoding="utf-8")
    raise FileNotFoundError(
        "dashboard artifact is missing; run `npm --prefix dashboard run build` before packaging"
    )
