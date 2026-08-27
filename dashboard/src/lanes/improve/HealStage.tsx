import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";

import { ArmedButton } from "../ArmedButton";
import type { ToolClient } from "../../toolClient";
import type { ArenaHistory, ArenaStatus, WikiStatus } from "../../types";

/**
 * The `heal` stage body (`INTERFACE_DESIGN.md §2.4`) — absorbs `ArenaPane`'s
 * status/history/leaderboard and `CompilePanel.tsx`'s `compile action=run`.
 * Never independently reachable: it opens (fetches arena data, offers the
 * compile control) only once `status.gate.state === "fail"` — exactly today's
 * `LoopPane` wiring, where the Heal step only turns "ready" after a gate
 * refusal.
 *
 * `compile action=run` is a single billed call with no free preview leg
 * (unlike `gate`'s `run_once`, which mints a server-side nonce). Per the
 * orchestrator's no-native-dialogs ruling (`LEARNINGS.md`), a spend-immediately
 * control with no nonce cycle gates on the shared `ArmedButton` two-click
 * affordance instead of `window.confirm()` — never native, and never a single
 * click.
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
      <p class="heal-step-body">
        {arenaStatus ? (
          <>
            Stage <strong>{arenaStatus.stage}</strong>
            {arenaStatus.message ? (
              <>
                <br />
                <span class="muted">{arenaStatus.message}</span>
              </>
            ) : null}
          </>
        ) : (
          <span class="muted">Loading arena status…</span>
        )}
      </p>

      {error ? (
        <p role="alert" class="ask-error">
          {error}
        </p>
      ) : null}

      {variants.length > 0 ? (
        <ul class="arena-lanes">
          {variants.map((variant) => (
            <li key={variant.id} class={`arena-lane status-${variant.status}`}>
              <strong>{variant.label}</strong>
              <span>{variant.status}</span>
              <em>
                {variant.scalar == null ? "…" : variant.scalar.toFixed(4)}
              </em>
            </li>
          ))}
        </ul>
      ) : null}

      {(arenaHistory?.races.length ?? 0) > 0 ? (
        <p class="muted">{arenaHistory!.races.length} recent race(s)</p>
      ) : null}

      <div class="heal-step-actions">
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
      </div>
    </div>
  );
}
