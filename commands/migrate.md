---
description: "[Tend] Migrate the knotica vault to the current schema version (preview first, then apply)."
argument-hint: "[topic]"
allowed-tools:
  - Bash(knotica tend migrate:*)
---
Migration plan (preview only):
!`knotica tend migrate --dry-run --topic "$1"`

Present the diff above. If there is nothing to migrate, say so and stop.
Otherwise, confirm with the user, then apply with `knotica tend migrate --yes --topic "$1"`.
Never apply without showing this preview first — migration never clobbers evolved files.
