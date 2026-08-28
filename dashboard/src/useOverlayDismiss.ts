import type { RefObject, TargetedFocusEvent } from "preact";
import { useEffect } from "preact/hooks";

export interface UseOverlayDismissOptions<TPanel extends HTMLElement> {
  panelRef: RefObject<TPanel>;
  triggerRef: RefObject<HTMLButtonElement>;
  onClose: () => void;
}

/**
 * Shared Escape / outside-pointerdown / focus-out dismissal semantics for
 * the dashboard's non-modal overlays (`InfoPopover`, `TermHint`). Rule of
 * two going on three (design §2.3) -- a divergence between the two
 * overlays' Escape handling would be an accessibility bug nobody would
 * notice from either component in isolation.
 *
 * `onClose` is the plain close (no focus side effect); Escape additionally
 * returns focus to the trigger, matching the pre-extraction behaviour
 * where only the explicit Escape/close-button paths refocused and a
 * pointerdown-outside or focus-out dismissal did not.
 */
export function useOverlayDismiss<TPanel extends HTMLElement>(
  id: string,
  open: boolean,
  { panelRef, triggerRef, onClose }: UseOverlayDismissOptions<TPanel>,
): (event: TargetedFocusEvent<TPanel>) => void {
  useEffect(() => {
    if (!open) {
      return;
    }
    function handlePointerDown(event: PointerEvent): void {
      const target = event.target as Node;
      if (panelRef.current?.contains(target) || triggerRef.current?.contains(target)) {
        return;
      }
      onClose();
    }
    function handleKeyDown(event: KeyboardEvent): void {
      if (event.key === "Escape") {
        onClose();
        triggerRef.current?.focus();
      }
    }
    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open, id, panelRef, triggerRef, onClose]);

  return function handleFocusOut(event: TargetedFocusEvent<TPanel>): void {
    const next = event.relatedTarget as Node | null;
    if (next && panelRef.current?.contains(next)) {
      return;
    }
    onClose();
  };
}
