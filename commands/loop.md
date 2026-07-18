---
description: Run one self-improvement loop tick — observe new wiki content, gate candidates, heal regressions.
argument-hint: "<topic>"
allowed-tools:
  - Bash(knotica loop:*)
---
One loop tick (observe → gate → heal):
!`knotica loop --topic "$1" --once`

Explain what the tick did (observation scalar vs baseline, any candidate
gated, any arena heal). If it reported that no baseline was frozen or no
runner is watching, suggest starting the watcher in a terminal:
`knotica loop --topic $1` (runs continuously; first observation freezes the
baseline automatically).
