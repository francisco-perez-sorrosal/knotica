import type { JSX } from "preact";
import { useRef } from "preact/hooks";

import { Icon } from "./icons";
import { closePopover, isPopoverOpen, togglePopover } from "./infoPopoverState";
import { useOverlayDismiss } from "./useOverlayDismiss";

export interface InfoPopoverProps {
  /** Stable identity for the module single-open signal and `aria-controls`. */
  id: string;
  title: string;
  /** Trigger `aria-label`, e.g. `"About Observe"`. */
  ariaLabel: string;
  whatThisIs: JSX.Element | string;
  /** Omitted where the target has no states (design §3.4). */
  whatTheStatesMean?: JSX.Element;
  whatToDoNext?: JSX.Element | string;
  /** One of three static positioning variants -- no measurement, no portal. */
  align?: "start" | "end" | "center";
  /** Extra classes merged onto the trigger button (e.g. card-local z-index). */
  class?: string;
}

/**
 * The non-modal, three-slot overlay primitive (design §3.4/§7.1) -- the
 * direct fix for invisible-on-touch `title=` tooltips. Never a confirmation
 * surface: no destructive action, no primary button, no focus trap.
 * `role="note"`, never `role="dialog"`, encodes that in the a11y tree.
 */
export function InfoPopover({
  id,
  title,
  ariaLabel,
  whatThisIs,
  whatTheStatesMean,
  whatToDoNext,
  align = "start",
  class: className,
}: InfoPopoverProps): JSX.Element {
  const open = isPopoverOpen(id);
  const panelId = `${id}-panel`;
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  function close(): void {
    closePopover(id);
    triggerRef.current?.focus();
  }

  const handlePanelFocusOut = useOverlayDismiss(id, open, {
    panelRef,
    triggerRef,
    onClose: () => closePopover(id),
  });

  return (
    <span class="info-popover" data-align={align}>
      <button
        type="button"
        ref={triggerRef}
        class={["info-trigger", className].filter(Boolean).join(" ")}
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={ariaLabel}
        onClick={() => togglePopover(id)}
      >
        <Icon name="info" size={16} />
      </button>
      {open ? (
        <div
          role="note"
          id={panelId}
          class="info-popover-panel"
          ref={panelRef}
          onFocusOut={handlePanelFocusOut}
        >
          <div class="info-popover-header">
            <span class="microlabel">{title}</span>
            <button
              type="button"
              class="info-popover-close"
              aria-label={`Close ${title}`}
              onClick={close}
            >
              <Icon name="close" size={16} />
            </button>
          </div>
          <div class="info-popover-body">
            <p class="microlabel">What this is</p>
            <p>{whatThisIs}</p>
            {whatTheStatesMean ? (
              <>
                <p class="microlabel">What the states mean</p>
                {whatTheStatesMean}
              </>
            ) : null}
            {whatToDoNext ? (
              <>
                <p class="microlabel">What to do next</p>
                <div>{whatToDoNext}</div>
              </>
            ) : null}
          </div>
        </div>
      ) : null}
    </span>
  );
}
