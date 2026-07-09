#!/usr/bin/env python3
"""Reflow hard-wrapped PDF sources in the live vault."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from knotica.core.config import resolve
from knotica.core.operations.reflow_sources import repair_pdf_sources
from knotica.store import LocalFSStore


def main() -> int:
    vault_config = resolve()
    vault = Path(vault_config.path)
    store = LocalFSStore(vault)
    envelope = repair_pdf_sources(store, vault)
    print(json.dumps(envelope, indent=2))
    return 0 if "error" not in envelope else 1


if __name__ == "__main__":
    raise SystemExit(main())
