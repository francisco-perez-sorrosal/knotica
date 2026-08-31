import { useEffect, useState } from "preact/hooks";
import type { JSX } from "preact";

import { CopyBlock } from "../../CopyBlock";
import { EmptyState } from "../../EmptyState";
import { Icon } from "../../icons";
import { InfoPopover } from "../../InfoPopover";
import { startVisibilityPausedPoll } from "../visibilityPausedPoll";
import type { OpenAnchor } from "../../paneRouting";
import type { ToolClient } from "../../toolClient";
import type { AttentionStatus } from "../../types";
import { AttentionTable } from "./AttentionTable";
import { deriveAttentionRows, sortAttentionRows } from "./attentionRows";
import { LaneCardGrid } from "./LaneCardGrid";

/**
 * `HomeLane` (`INTERFACE_DESIGN.md §2.1`, `dec-092`; redesigned per
 * `INTERFACE_DESIGN.md §3.2`) -- the cross-topic attention inbox. No stage
 * rail, no per-stage state: a lane-card grid plus a flat attention queue (or
 * a success state), not a process rail.
 *
 * Self-fetches `client.wikiStatus("", vault, "attention")` on mount and on
 * every tick of its own `startVisibilityPausedPoll` (`§4.2` rule 3, `dec-092`)
 * at 10s -- independent of `App.tsx`'s 2s `view="summary"` poll every other
 * lane's rail reads from. Home is cross-topic, so it owns its own read. The
 * six `LaneCardGrid` cards and the drift row both read from this same
 * `attention` payload -- no new call, no new poll.
 *
 * `onOpenAnchor` is `App.tsx`'s single navigation callback -- the same one
 * every other lane now holds. Home's privilege was never that it *had* the
 * callback; it is that Home **acts on nothing**: it routes and narrates, and
 * has no process of its own (the process registry forbids `lane: "home"`
 * outright). Every card and every queue row calls the callback with a
 * `(lane, stage)` pair sourced from `attentionMeta.ts`, never one it invents.
 *
 * The drift row renders unconditionally as a statement plus an
 * `InfoPopover` (design §3.2) -- the prior `[Check]` button had no click
 * handler ("an affordance that lies," design §1 F3) and is removed, not
 * wired; resolving note anchors is still the one cost `attention` does not
 * pay unconditionally (`§4.2` rule 2), so the popover's remediation slot
 * offers the CLI command instead.
 */
const ATTENTION_POLL_MS = 10_000;

export function HomeLane({
  client,
  vault,
  onOpenAnchor,
}: {
  client: ToolClient | null;
  vault: string;
  onOpenAnchor: OpenAnchor;
}): JSX.Element {
  const [attention, setAttention] = useState<AttentionStatus | null>(null);

  useEffect(() => {
    if (!client) return;
    let cancelled = false;

    function fetchAttention() {
      client!
        .wikiStatus("", vault, "attention")
        .then((next) => {
          // `wikiStatus`'s declared return type is `WikiStatus`, but
          // `view="attention"` returns the disjoint `AttentionStatus` shape
          // at runtime -- the same declared-shape-mismatch cast
          // `ImproveLane.tsx` uses for `ObserveStageStatus`.
          if (!cancelled) setAttention(next as unknown as AttentionStatus);
        })
        .catch(() => {
          // Home is a best-effort inbox: a failed poll leaves the last
          // known rows on screen rather than surfacing an error banner.
        });
    }

    fetchAttention();
    const stop = startVisibilityPausedPoll(fetchAttention, ATTENTION_POLL_MS);
    return () => {
      cancelled = true;
      stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, vault]);

  const rows = attention
    ? sortAttentionRows(deriveAttentionRows(attention))
    : [];

  return (
    <main class="pane-main home">
      <LaneCardGrid attention={attention} onOpenAnchor={onOpenAnchor} />
      {rows.length === 0 ? (
        <EmptyState
          icon="state:complete"
          title="Nothing needs you"
          sentence="Every topic is settled. The loop runs on its own until something wants a decision."
          action={
            <button
              type="button"
              class="primary"
              onClick={() => onOpenAnchor("improve")}
            >
              Open Improve →
            </button>
          }
        />
      ) : (
        <AttentionTable rows={rows} onOpenAnchor={onOpenAnchor} />
      )}
      <DriftRow />
    </main>
  );
}

function DriftRow(): JSX.Element {
  return (
    <p class="home-drift-row muted">
      <Icon name="state:unknown" size={16} />
      Note drift -- not checked. One scan per anchor, so it is never run
      automatically.
      <InfoPopover
        id="home:drift"
        title="Note drift"
        ariaLabel="About note drift"
        align="end"
        whatThisIs="Drift means a note's citation anchor moved, or the page underneath it changed. Checking means re-resolving every note's anchor -- the one cost the attention view does not pay unconditionally."
        whatToDoNext={
          // The MCP call-form, not a CLI command: no `knotica` subcommand runs
          // the drift scan (tend's CLI carries doctor/okf/migrate/guillotine
          // only), so the honest affordance is the read-only call a Claude
          // client can make -- the same copy-for-Claude handoff the ingest
          // panel uses.
          <CopyBlock
            code="tend action=notes notes_action=drift topic=<topic>"
            label="the drift check call for your Claude client"
          />
        }
      />
    </p>
  );
}
