import { formatPromotePreview } from "./promoteHelpers";
import type { CompilePromoteResult } from "./types";

export function PromotePreviewBanner({
  preview,
  busy,
  applyLabel = "Apply merge",
  onApply,
  onDismiss,
}: {
  preview: CompilePromoteResult | null;
  busy: boolean;
  applyLabel?: string;
  onApply: () => void;
  onDismiss: () => void;
}) {
  if (!preview) return null;
  return (
    <aside class="promote-preview" role="status" aria-live="polite">
      <p>{formatPromotePreview(preview)}</p>
      <div class="promote-preview-actions">
        <button type="button" class="primary" disabled={busy} onClick={() => void onApply()}>
          {busy ? "Applying…" : applyLabel}
        </button>
        <button type="button" class="ghost" disabled={busy} onClick={onDismiss}>
          Dismiss
        </button>
      </div>
    </aside>
  );
}
