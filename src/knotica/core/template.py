"""Read-only locator for the packaged vault template.

The ``vault-template/`` directory ships as wheel package data in an installed
build and lives at the repo root in an editable/dev checkout. This module is the
single source of that resolution so both the setup wizard (``cli.init``) and the
schema migration (``core.operations.migrate``) share one walk instead of each
duplicating the wheel-then-repo lookup. Dependencies point downward: adapters
and operations depend on ``core``, never the reverse.

It performs **no vault writes** -- pure path resolution -- so it sits cleanly
inside the read-only half of ``core`` and never threatens the single-writer
invariant (the sole writer remains ``core.transaction``).
"""

import importlib.resources
from pathlib import Path

#: Directory name of the packaged vault template (wheel data / repo root).
TEMPLATE_DIRNAME = "vault-template"


class TemplateNotFoundError(Exception):
    """The packaged ``vault-template`` could not be located on disk.

    Raised only when neither the installed wheel's package data nor a repo-root
    copy exists -- effectively a broken install. Each caller translates this
    into its own surface-appropriate error grammar (the wizard's ``_InitError``,
    the migration's typed failure envelope) so the message stays consistent with
    the rest of that surface.
    """


def packaged_template_path() -> Path:
    """Locate the packaged ``vault-template`` (wheel data, else the repo-root copy).

    Installed wheels carry the template as package data; editable/dev installs
    resolve the repo-root copy instead (the package tree has no template).

    Raises:
        TemplateNotFoundError: when neither location holds the template.
    """
    resource = importlib.resources.files("knotica") / TEMPLATE_DIRNAME
    if resource.is_dir():
        return Path(str(resource))
    for parent in Path(__file__).resolve().parents:
        candidate = parent / TEMPLATE_DIRNAME
        if candidate.is_dir():
            return candidate
    raise TemplateNotFoundError(
        "the packaged vault-template could not be located (neither as installed "
        "package data nor as a repo-root directory)"
    )
