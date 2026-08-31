import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";

import { CopyBlock } from "../CopyBlock";
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

/**
 * Everything an embedding stage needs to render the dispatch affordance
 * itself, computed once by `HandoffStage`. Passing this rather than the raw
 * client keeps the dec-091 prose-first payload and the tier derivation in
 * exactly one place.
 */
export interface HandoffDispatch {
  tier: DispatchTier;
  /** The literal `/knotica:<command> <suggestion-id> <topic>` line. */
  dispatchLine: string;
  /** The prose-first payload a non-slash host routes on. */
  dispatchText: string;
  sendMessage: () => Promise<void>;
  updateModelContext: () => Promise<void>;
}

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
  /**
   * The in-lane control for every `next.actor === "you"` state. Receives the
   * dispatch context so a you-state can offer the same dispatch affordance
   * the `claude` states get -- the user clicking `Open a session` *is* the
   * `you` actor taking their turn, and `/knotica:fill` branches on the
   * session state to serve it.
   */
  renderYouControl: (
    status: SessionStatus,
    dispatch: HandoffDispatch,
  ) => JSX.Element | null;
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

  /* Computed once, here, and handed to both mount points: the embedding
     stage cannot re-derive the tier or the prose-first payload without
     duplicating this template. */
  const dispatch: HandoffDispatch = {
    tier: deriveDispatchTier(client.hostCapabilities, client.mount),
    dispatchLine,
    dispatchText,
    sendMessage: () => client.sendMessage(dispatchText),
    updateModelContext: () => client.updateModelContext(dispatchText),
  };

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
            <HandoffDispatchPanel dispatch={dispatch} />
          ) : null}

          {status.next.actor === "you"
            ? renderYouControl(status, dispatch)
            : null}

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
 * request, is never stranded. It is a `CopyBlock` rather than a bare `<pre>`
 * so "never stranded" is operational and not merely visual: at A and B the
 * copy sits beside a working button under a distinct, honest label; at C and
 * D it *is* the affordance.
 *
 * Exported because two mount points share it -- `HandoffStage` itself for
 * `next.actor === "claude"`, and an embedding stage's own you-panel for the
 * states where the user is the one who acts. One dispatch surface, never a
 * reimplementation.
 */
export function HandoffDispatchPanel({
  dispatch,
  dispatched = false,
  onDispatched,
}: {
  dispatch: HandoffDispatch;
  /** Replaces the button with the sent-confirmation; the copy stays. */
  dispatched?: boolean;
  onDispatched?: () => void;
}): JSX.Element {
  const { tier, dispatchLine, dispatchText } = dispatch;
  const [dispatching, setDispatching] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  /**
   * Held here as well as lifted, and ORed with the prop: an uncontrolled call
   * site (`HandoffStage`'s own `claude`-actor mount passes neither `dispatched`
   * nor `onDispatched`) otherwise returned to the pre-click state on success --
   * indistinguishable from a click that did nothing, whose likely recovery is
   * a second click that sends the payload twice. A controlled call site keeps
   * its lift-to-parent survival across the actor flip.
   */
  const [locallySent, setLocallySent] = useState(false);
  const programmatic = tier === "A" || tier === "B";
  const sent = dispatched || locallySent;

  function markSent(): void {
    setLocallySent(true);
    onDispatched?.();
  }

  async function run(send: () => Promise<void>): Promise<void> {
    setDispatching(true);
    setFailure(null);
    try {
      await send();
      markSent();
    } catch (cause) {
      // A host can reject or silently drop the request; saying so is what
      // keeps the copy affordance below an honest fallback rather than a
      // decoration.
      setFailure(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setDispatching(false);
    }
  }

  return (
    <div class="handoff-dispatch">
      {sent ? (
        // Two honest claims, one per floor. At A/B the host took the payload,
        // so "sent" is a fact; at C/D nothing was dispatched — the user holds
        // the text — so the only fact is that it is on the clipboard. Neither
        // claims progress the panel cannot see; both name the poll.
        <p class="handoff-dispatched" role="status">
          <Spinner />
          {programmatic ? (
            <>
              <strong>Sent to your Claude session.</strong> Continue there —
              this list updates as the session writes.
            </>
          ) : (
            <>
              <strong>Copied.</strong> Paste it into your Claude session — this
              list updates as the session writes.
            </>
          )}
        </p>
      ) : programmatic ? (
        <>
          <p class="muted">
            {tier === "A"
              ? "Claude may ask you to confirm."
              : "Then tell Claude to continue — this does not start a turn."}
          </p>
          <button
            type="button"
            disabled={dispatching}
            aria-busy={dispatching || undefined}
            onClick={() =>
              void run(
                tier === "A"
                  ? dispatch.sendMessage
                  : dispatch.updateModelContext,
              )
            }
          >
            {dispatching ? <Spinner /> : null}
            {tier === "A" ? "Send to Claude" : "Queue for Claude"}
          </button>
        </>
      ) : null}

      {failure ? (
        <p role="alert" class="handoff-dispatch-error">
          {failure}
        </p>
      ) : null}

      {/* At C/D the copy IS the dispatch — there is no button to press, so a
          successful clipboard write is the only signal the payload ever left,
          and without it the phase-6 follow-up would be unreachable on the
          mount most users are on. At A/B the copy is the fallback beside a
          working button and must not claim the send happened. */}
      <CopyBlock
        code={dispatchLine}
        copyText={dispatchText}
        actionLabel={programmatic ? "Copy it instead" : "Copy the instruction"}
        onCopied={programmatic ? undefined : markSent}
      />
    </div>
  );
}
