import type { JSX } from "preact";

import { Spinner } from "../icons";

/**
 * The shared armed→confirm control for every nonce-less billed or
 * mutating action (`LEARNINGS.md`'s no-native-dialogs ruling): a sandboxed
 * MCP-App iframe has no `allow-modals`, so `window.confirm()` can be
 * silently suppressed and return `false`, bricking the action on Claude
 * Desktop. The fix is the same shape everywhere -- a first click arms the
 * control (relabelling it to the confirm copy), and only the second,
 * explicit click fires; a separate `Cancel` ghost button un-arms without
 * firing.
 *
 * Extracted once the identical state machine had been inlined
 * independently at three call sites (`InstrumentStage.tsx`'s Bootstrap and
 * Bootstrap-trainset controls, `HealStage.tsx`'s compile-run control, and
 * `TendLane.tsx`'s OKF repair-apply control) -- the rule of three. The
 * `armed` flag stays **controlled** by the caller rather than owned here:
 * some callers (`TendLane.tsx`) need to reset it from an effect keyed on
 * `vault`, which only the caller can express.
 */
export function ArmedButton({
  armed,
  busy,
  disabled = false,
  label,
  armedLabel,
  busyLabel,
  onArm,
  onConfirm,
  onCancel,
  className,
  testId,
  cancelTestId,
  title,
}: {
  armed: boolean;
  busy: boolean;
  disabled?: boolean;
  label: string;
  armedLabel: string;
  busyLabel: string;
  onArm: () => void;
  onConfirm: () => void;
  onCancel: () => void;
  className?: string;
  testId?: string;
  cancelTestId?: string;
  title?: string;
}): JSX.Element {
  function handleClick(): void {
    if (!armed) {
      onArm();
      return;
    }
    onConfirm();
  }

  return (
    <>
      <button
        type="button"
        class={className}
        data-testid={testId}
        title={title}
        disabled={disabled || busy}
        aria-busy={busy || undefined}
        onClick={handleClick}
      >
        {busy ? (
          <>
            <Spinner />
            {busyLabel}
          </>
        ) : armed ? (
          armedLabel
        ) : (
          label
        )}
      </button>
      {armed && !busy ? (
        <button
          type="button"
          class="ghost"
          data-testid={cancelTestId}
          onClick={onCancel}
        >
          Cancel
        </button>
      ) : null}
    </>
  );
}
