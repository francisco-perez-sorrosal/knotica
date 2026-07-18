import { formatDeletePreview } from "./deleteHelpers";
import type { BranchDeleteResult } from "./types";

export function DeletePreviewBanner({
  preview,
  busy,
  onApply,
  onDismiss,
}: {
  preview: BranchDeleteResult | null;
  busy: boolean;
  onApply: () => void;
  onDismiss: () => void;
}) {
  if (!preview) return null;
  return (
    <aside class="delete-preview" role="status" aria-live="polite">
      <p>{formatDeletePreview(preview)}</p>
      <div class="promote-preview-actions">
        <button type="button" class="danger" disabled={busy} onClick={() => void onApply()}>
          {busy ? "Deleting…" : "Apply delete"}
        </button>
        <button type="button" class="ghost" disabled={busy} onClick={onDismiss}>
          Dismiss
        </button>
      </div>
    </aside>
  );
}
