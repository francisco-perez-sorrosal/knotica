import type { JSX } from "preact";
import { useRef } from "preact/hooks";

import { closePopover, isPopoverOpen, togglePopover } from "./infoPopoverState";
import { useOverlayDismiss } from "./useOverlayDismiss";

export interface TermHintProps {
  /**
   * Stable identity for the module single-open signal and `aria-controls`
   * -- shares `InfoPopover`'s signal: at most one overlay,
   * `InfoPopover` or `TermHint`, is ever open.
   */
  id: string;
  /** The dotted-underline visible term. */
  term: string;
  title: string;
  body: JSX.Element | string;
  /** Static positioning variant -- no measurement, no portal. */
  align?: "start" | "end";
}

/**
 * The inline dotted-underline explanatory overlay -- a second
 * overlay *class* sharing round 1's single-open signal, never a second
 * overlay *system* (B3). Always a real `<button>` trigger, never
 * hover-only; `role="note"`, never a confirmation surface -- `ArmedButton`
 * remains the sole confirm grammar.
 *
 * Forbidden placements: never inside a `<button>`, never
 * inside any element carrying `aria-expanded`, never inside a `title=`.
 */
export function TermHint({
  id,
  term,
  title,
  body,
  align = "start",
}: TermHintProps): JSX.Element {
  const open = isPopoverOpen(id);
  const panelId = `${id}-panel`;
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLSpanElement>(null);

  const handleFocusOut = useOverlayDismiss(id, open, {
    panelRef,
    triggerRef,
    onClose: () => closePopover(id),
  });

  return (
    <span class="term-hint" data-align={align}>
      <button
        type="button"
        ref={triggerRef}
        class="term-hint-trigger"
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={`${term} — what this means`}
        onClick={() => togglePopover(id)}
      >
        {term}
      </button>
      {open ? (
        <span
          role="note"
          id={panelId}
          class="term-hint-panel"
          ref={panelRef}
          onFocusOut={handleFocusOut}
        >
          <span class="microlabel">{title}</span>
          <span class="term-hint-body">{body}</span>
        </span>
      ) : null}
    </span>
  );
}
