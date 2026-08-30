import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";

import { Spinner } from "../icons";
import type { ToolClient } from "../toolClient";
import type { SessionStatus } from "../types";
import { deriveDispatchTier, type DispatchTier } from "./hostCapabilities";

/**
 * The shared handoff stage (`INTERFACE_DESIGN.md §3`, `dec-091`) -- the ASK /
 * DISPATCH / WATCH shell every lane that hands a step to Claude embeds
 * (Learn's `pages`, Fill's `ingest`).
 *
 * Built on observation, with dispatch as progressive enhancement (§3.2): even
 * at the weakest capability tier ("copy this text") the stage still watches
 * `fill(action="session_status")` and self-advances when the client writes,
 * so a lane can never dead-end just because dispatch is unavailable or
 * unverified on the host.
 *
 * `next.actor` is the anti-dead-end guarantee and drives everything rendered
 * below: `claude` shows the tier-gated dispatch control; `you` calls
 * `renderYouControl` -- the in-lane control (Submit / Rework / Open a
 * session) is never invented here, only ever passed in by the embedding
 * stage; `system`/`none` show status text only.
 */

const POLL_INTERVAL_MS = 3000;

export interface HandoffStageProps {
  client: ToolClient;
  topic: string;
  suggestionId: string;
  vault: string;
  /** The slash-command name to render, e.g. `"ingest"` or `"fill"`. */
  command: string;
  /** The one-sentence ASK prose (`INTERFACE_DESIGN.md §3.2`). */
  ask: string;
  /** Only the expanded/selected item polls (`§3.3`'s cost discipline). */
  active: boolean;
  /** The in-lane control for every `next.actor === "you"` state. */
  renderYouControl: (status: SessionStatus) => JSX.Element | null;
}

export function HandoffStage({
  client,
  topic,
  suggestionId,
  vault,
  command,
  ask,
  active,
  renderYouControl,
}: HandoffStageProps): JSX.Element {
  const [status, setStatus] = useState<SessionStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;

    async function tick() {
      try {
        const next = await client.sessionStatus(topic, suggestionId, vault);
        if (cancelled) return;
        setStatus(next);
        setError(null);
      } catch (cause) {
        if (cancelled) return;
        setError(cause instanceof Error ? cause.message : String(cause));
      }
    }

    void tick();
    const interval = window.setInterval(() => void tick(), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [client, topic, suggestionId, vault, active]);

  const dispatchLine = `/knotica:${command} ${suggestionId} ${topic}`;
  const dispatchText = `${ask}\n\n${dispatchLine}`;

  function copyInstruction() {
    void navigator.clipboard?.writeText(dispatchText);
  }

  async function dispatchViaMessage() {
    await client.sendMessage(dispatchText);
  }

  async function dispatchViaModelContext() {
    await client.updateModelContext(dispatchText);
  }

  return (
    <div class="handoff-stage">
      <p class="handoff-ask">{ask}</p>

      {error ? (
        <p role="alert" class="handoff-error">
          {error}
        </p>
      ) : null}

      {status ? (
        <>
          {status.next.actor === "claude" ? (
            <DispatchControl
              tier={deriveDispatchTier(client.hostCapabilities, client.mount)}
              dispatchLine={dispatchLine}
              onSendMessage={() => void dispatchViaMessage()}
              onUpdateModelContext={() => void dispatchViaModelContext()}
              onCopy={copyInstruction}
            />
          ) : null}

          {status.next.actor === "you" ? renderYouControl(status) : null}

          <p class="handoff-status muted">{status.next.do}</p>

          {status.gate_outcome?.reason ? (
            <p class="handoff-dilution-reason muted">
              {status.gate_outcome.reason}
            </p>
          ) : null}
        </>
      ) : (
        <p class="muted">
          <Spinner />
          Watching…
        </p>
      )}
    </div>
  );
}

/**
 * Four capability tiers, one honest label each (`INTERFACE_DESIGN.md §3.4`).
 * The literal dispatch line renders at every tier, including A and B -- a
 * user who does not trust the button, or whose host silently drops the
 * request, is never stranded.
 */
function DispatchControl({
  tier,
  dispatchLine,
  onSendMessage,
  onUpdateModelContext,
  onCopy,
}: {
  tier: DispatchTier;
  dispatchLine: string;
  onSendMessage: () => void;
  onUpdateModelContext: () => void;
  onCopy: () => void;
}): JSX.Element {
  return (
    <div class="handoff-dispatch">
      {tier === "A" ? (
        <>
          <p class="muted">Claude may ask you to confirm.</p>
          <button type="button" onClick={onSendMessage}>
            Send to Claude
          </button>
        </>
      ) : tier === "B" ? (
        <>
          <p class="muted">
            Then tell Claude to continue — this does not start a turn.
          </p>
          <button type="button" onClick={onUpdateModelContext}>
            Queue for Claude
          </button>
        </>
      ) : (
        <button type="button" onClick={onCopy}>
          Copy the instruction
        </button>
      )}
      <pre class="handoff-dispatch-line">{dispatchLine}</pre>
    </div>
  );
}
