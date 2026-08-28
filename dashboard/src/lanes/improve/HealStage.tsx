import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";

import { ArmedButton } from "../ArmedButton";
import { SectionCard } from "../../SectionCard";
import { Stat, StatGrid } from "../../Stat";
import { StateList } from "../../StateList";
import type { StateListRow } from "../../StateList";
import { TermHint } from "../../TermHint";
import type { IconName } from "../../icons";
import type { SectionTone } from "../../SectionCard";
import type { ToolClient } from "../../toolClient";
import type { ArenaHistory, ArenaStatus, ArenaVariant, WikiStatus } from "../../types";

/**
 * The `heal` stage body — absorbs `ArenaPane`'s status/history/leaderboard and
 * `CompilePanel.tsx`'s `compile action=run`. Never independently reachable: it
 * opens (fetches arena data, offers the compile control) only once
 * `status.gate.state === "fail"` — exactly today's `LoopPane` wiring, where
 * the Heal step only turns "ready" after a gate refusal.
 *
 * `compile action=run` is a single billed call with no free preview leg
 * (unlike `gate`'s `run_once`, which mints a server-side nonce). Per the
 * orchestrator's no-native-dialogs ruling (`LEARNINGS.md`), a spend-immediately
 * control with no nonce cycle gates on the shared `ArmedButton` two-click
 * affordance instead of `window.confirm()` — never native, and never a single
 * click.
 *
 * The open body is two `SectionCard`s: ARENA (what the race is doing) and
 * COMPILE (the one billed action, in the footer of the card that explains it).
 * The variant race is a `StateList`, so each variant's state word sits as
 * visible text next to its icon with its scalar in a right-aligned tabular
 * column — the unstyled `.arena-lane` `<strong>/<span>/<em>` triple it
 * replaces ran the label and the state word together into one unreadable
 * string. The loose "N recent race(s)" counter becomes a labelled `Stat`.
 */

export function HealStage({
  client,
  topic,
  vault,
  status,
  onStatusRefresh,
}: {
  client: ToolClient | null;
  topic: string;
  vault: string;
  status: WikiStatus | null;
  onStatusRefresh?: () => void | Promise<void>;
}): JSX.Element {
  const open = status?.gate.state === "fail";
  const [arenaStatus, setArenaStatus] = useState<ArenaStatus | null>(null);
  const [arenaHistory, setArenaHistory] = useState<ArenaHistory | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open || !client) return;
    let cancelled = false;
    void (async () => {
      try {
        const [nextStatus, nextHistory] = await Promise.all([
          client.arenaStatus(topic, vault),
          client.arenaHistory(topic, vault, 12),
        ]);
        if (cancelled) return;
        setArenaStatus(nextStatus);
        setArenaHistory(nextHistory);
      } catch (cause) {
        if (cancelled) return;
        setError(cause instanceof Error ? cause.message : String(cause));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, client, topic, vault]);

  useEffect(() => {
    setArmed(false);
  }, [open]);

  if (!open) {
    return (
      <p class="muted">
        Opens after a gate fail — the watcher races prompt variants in the arena
        until one clears baseline, or reverts if none do.
      </p>
    );
  }

  async function runCompile() {
    if (!client || busy) return;
    setBusy(true);
    setError(null);
    try {
      await client.compileRun(topic, vault);
      await onStatusRefresh?.();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
      setArmed(false);
    }
  }

  const variants = [...(arenaStatus?.variants ?? [])].sort(
    (a, b) => (b.scalar ?? -1) - (a.scalar ?? -1),
  );

  return (
    <div class="heal-stage">
      <SectionCard
        title="ARENA"
        icon="stage:heal"
        headerActions={stageChip(arenaStatus)}
      >
        <>
          <StatGrid>
            <Stat label="VARIANTS" value={arenaStatus?.variants.length} />
            <Stat
              label={
                <TermHint
                  id="heal-recent-races"
                  term="RECENT RACES"
                  title="Recent races"
                  body="How many variant races this topic has run lately. History only; it does not gate anything."
                />
              }
              value={arenaHistory?.races.length}
            />
          </StatGrid>
          <p class="muted">
            {arenaStatus
              ? (arenaStatus.message ??
                "The watcher races prompt variants against each other and keeps the one that clears the gate baseline.")
              : "Reading the arena…"}
          </p>
          {variants.length > 0 ? (
            <StateList label="Arena variants" rows={variants.map(variantRow)} />
          ) : null}
          {error ? (
            <p role="alert" class="ask-error">
              {error}
            </p>
          ) : null}
        </>
      </SectionCard>

      <SectionCard
        title="COMPILE"
        icon="refresh"
        footer={
          <>
            {/* Sibling of the button, never a child: the accessible name
                stays `Compile now` and the armed→confirm contract is
                untouched. */}
            <span class="chip cost">billed</span>
            <ArmedButton
              armed={armed}
              busy={busy}
              disabled={!client}
              label="Compile now"
              armedLabel="Confirm compile — bills"
              busyLabel="Compiling…"
              className="heal-freeze-primary"
              testId="heal-compile-run"
              onArm={() => setArmed(true)}
              onConfirm={() => void runCompile()}
              onCancel={() => setArmed(false)}
            />
          </>
        }
      >
        <p class="muted">
          A fresh compile re-optimises the prompt program against the trainset
          and writes a new candidate branch. It is billed, and the first click
          only arms the control.
        </p>
      </SectionCard>
    </div>
  );
}

/**
 * The race's own stage word, with the explanation of what it means. The word
 * comes from the payload verbatim — the arena names its own stages and this
 * stage never re-labels them.
 */
function stageChip(arenaStatus: ArenaStatus | null): JSX.Element {
  if (!arenaStatus) return <span class="chip">reading…</span>;
  return (
    <span class="chip">
      <TermHint
        id="heal-arena-stage"
        term={arenaStatus.stage}
        title="Arena stage"
        body="Where the variant race is. Aborted means the race stopped before ranking anything — the message below says why. It is not a failed compile."
        align="end"
      />
    </span>
  );
}

/**
 * Per-variant icon and tone. The state *word* is always the payload's own
 * `status` — the icon and the colour are redundancy on top of it, never the
 * only carrier of the verdict.
 */
const VARIANT_PRESENTATION: Record<string, { icon: IconName; tone: SectionTone }> = {
  pending: { icon: "state:pending", tone: "neutral" },
  scored: { icon: "state:running", tone: "neutral" },
  winner: { icon: "state:complete", tone: "good" },
  lost: { icon: "state:blocked", tone: "bad" },
};

function variantRow(variant: ArenaVariant): StateListRow {
  const presentation = VARIANT_PRESENTATION[variant.status] ?? {
    icon: "state:unknown" as IconName,
    tone: "neutral" as SectionTone,
  };
  return {
    id: variant.id,
    state: variant.status,
    icon: presentation.icon,
    name: (
      <TermHint
        id={`heal-variant-${variant.id}`}
        term={variant.label}
        title="Prompt variant"
        body="One candidate rewrite of the operation prompt. The arena races variants against each other and keeps the one that clears the baseline — no model weights are ever touched."
      />
    ),
    stateLabel: variant.status,
    tone: presentation.tone,
    // A variant with no scalar yet is an absence, not an ellipsis that reads
    // as "still loading" — `StateList` renders it as the neutral `—`.
    value: variant.scalar == null ? null : variant.scalar.toFixed(4),
  };
}
