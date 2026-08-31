import type { ComponentChildren } from "preact";
import { useRef, useState } from "preact/hooks";

import { Spinner } from "./icons";

/**
 * The one client-side billing boundary, in one place.
 *
 * Every billed tool on this surface follows the same server protocol: call it
 * with no `confirm` to get a free quote plus a short-lived `confirm_nonce`,
 * then call it again passing that nonce to actually bill and run. This module
 * owns that dance so a pane never re-implements it — three panes had already
 * grown their own copy, and the copy that forgot the second leg minted billed
 * previews and rendered them as outcomes.
 *
 * The state functions below are pure and framework-free: they take the current
 * state, the caller's two legs, and an emitter, and drive the transitions.
 * `runConfirm` is the *only* place that reaches a billing call, and it refuses
 * to unless phase 1 handed it a nonce.
 */

/** The one field the primitive itself reads: phase 1 mints it, phase 2 redeems it. */
export interface TwoPhaseEnvelope {
  confirm_nonce?: string;
}

export type TwoPhaseBusy = "preview" | "confirm" | null;

/** Idle is all three nulls; the quote and the outcome are never both present. */
export interface TwoPhaseState<T extends TwoPhaseEnvelope> {
  preview: T | null;
  outcome: T | null;
  busy: TwoPhaseBusy;
}

export interface TwoPhaseHandlers<T extends TwoPhaseEnvelope> {
  /** Phase 1 — free. Called with no nonce; the server quotes and mints. */
  preview: () => Promise<T>;
  /** Phase 2 — the billing boundary. Only ever reached with a minted nonce. */
  confirm: (nonce: string, quoted: T) => Promise<T>;
  /** Where the pane already shows its failures; the primitive keeps none. */
  onError?: (message: string) => void;
}

export type TwoPhaseEmit<T extends TwoPhaseEnvelope> = (
  next: TwoPhaseState<T>,
) => void;

export function idleTwoPhase<T extends TwoPhaseEnvelope>(): TwoPhaseState<T> {
  return { preview: null, outcome: null, busy: null };
}

function describeCause(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

/** Phase 1. Free by construction — this function cannot reach `handlers.confirm`. */
export async function runPreview<T extends TwoPhaseEnvelope>(
  state: TwoPhaseState<T>,
  handlers: TwoPhaseHandlers<T>,
  emit: TwoPhaseEmit<T>,
): Promise<void> {
  if (state.busy) return;
  emit({ preview: null, outcome: null, busy: "preview" });
  try {
    emit({ preview: await handlers.preview(), outcome: null, busy: null });
  } catch (cause) {
    emit(idleTwoPhase<T>());
    handlers.onError?.(describeCause(cause));
  }
}

/**
 * Phase 2. Refuses without a nonce from phase 1, so a single click can never
 * bill: with nothing to redeem, `handlers.confirm` is not called at all.
 * A failure keeps the quote — the nonce may still be live, so the pane can
 * offer the same confirm again rather than silently losing the estimate.
 */
export async function runConfirm<T extends TwoPhaseEnvelope>(
  state: TwoPhaseState<T>,
  handlers: TwoPhaseHandlers<T>,
  emit: TwoPhaseEmit<T>,
): Promise<void> {
  const quoted = state.preview;
  const nonce = quoted?.confirm_nonce;
  if (state.busy || !quoted || !nonce) return;
  emit({ preview: quoted, outcome: null, busy: "confirm" });
  try {
    emit({
      preview: null,
      outcome: await handlers.confirm(nonce, quoted),
      busy: null,
    });
  } catch (cause) {
    emit({ preview: quoted, outcome: null, busy: null });
    handlers.onError?.(describeCause(cause));
  }
}

export interface TwoPhaseController<T extends TwoPhaseEnvelope> {
  state: TwoPhaseState<T>;
  preview: () => Promise<void>;
  confirm: () => Promise<void>;
  reset: () => void;
}

/**
 * Binds the state functions above to component state.
 *
 * `onBusyChange` exists for panes that already own a pane-wide busy flag and
 * need the rest of their controls disabled while a billed call is in flight.
 */
export function useTwoPhaseAction<T extends TwoPhaseEnvelope>(
  handlers: TwoPhaseHandlers<T>,
  onBusyChange?: (busy: TwoPhaseBusy) => void,
): TwoPhaseController<T> {
  const [state, setState] = useState<TwoPhaseState<T>>(idleTwoPhase<T>);
  // The async legs read the state they were started from, not the one the last
  // render closed over, so a settle that lands after a re-render still sees it.
  const latest = useRef(state);
  latest.current = state;
  const emit: TwoPhaseEmit<T> = (next) => {
    latest.current = next;
    setState(next);
    onBusyChange?.(next.busy);
  };
  return {
    state,
    preview: () => runPreview(latest.current, handlers, emit),
    confirm: () => runConfirm(latest.current, handlers, emit),
    reset: () => emit(idleTwoPhase<T>()),
  };
}

/** The quote, with the only control that bills. `children` is the pane's estimate prose. */
export function TwoPhaseConfirm({
  children,
  busy,
  busyLabel = "Working",
  disabled = false,
  extraClass = "",
  onConfirm,
  onCancel,
}: {
  children: ComponentChildren;
  busy: TwoPhaseBusy;
  busyLabel?: string;
  disabled?: boolean;
  extraClass?: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div
      class={`heal-policy-controls heal-run-eval-confirm ${extraClass}`.trimEnd()}
    >
      <p class="heal-step-body">{children}</p>
      <button
        type="button"
        class="heal-freeze-primary"
        disabled={disabled}
        aria-busy={busy === "confirm" || undefined}
        onClick={() => void onConfirm()}
      >
        {busy === "confirm" ? (
          <>
            <Spinner />
            {`${busyLabel}…`}
          </>
        ) : (
          "Confirm — run and bill"
        )}
      </button>
      <button
        type="button"
        class="ghost"
        disabled={busy !== null}
        onClick={onCancel}
      >
        Cancel
      </button>
    </div>
  );
}

/**
 * The answer, reported where the question was asked.
 *
 * A confirm can legitimately bill nothing — a hold or an unchanged HEAD answers
 * instantly — and when the only sign of that was a line hundreds of pixels
 * below the button, the action read as broken. `tone` carries the no-charge
 * class so "nothing was spent" is visible, not just readable.
 */
export function TwoPhaseOutcome({
  children,
  tone = "",
  onDismiss,
}: {
  children: ComponentChildren;
  tone?: string;
  onDismiss: () => void;
}) {
  return (
    <div
      class={`heal-policy-controls heal-run-eval-outcome ${tone}`.trimEnd()}
      role="status"
    >
      <p class="heal-step-body">{children}</p>
      <button type="button" class="ghost" onClick={onDismiss}>
        Dismiss
      </button>
    </div>
  );
}
