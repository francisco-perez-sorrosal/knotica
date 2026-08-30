import type { JSX } from "preact";
import { useState } from "preact/hooks";

import { Icon } from "./icons";

const CONFIRMATION_DISPLAY_MS = 1400;

/**
 * A mono code block with a copy affordance (design §3.2/§3.5) -- the
 * remediation-hint and drift-check rendering the dashboard already carries
 * as plain prose or a bare `<code>`, upgraded to something a user can act
 * on without retyping.
 */
export function CopyBlock({
  code,
  label,
}: {
  code: string;
  label?: string;
}): JSX.Element {
  const [status, setStatus] = useState<"idle" | "copied" | "failed">("idle");

  async function handleCopy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(code);
      setStatus("copied");
    } catch {
      setStatus("failed");
    }
    setTimeout(() => setStatus("idle"), CONFIRMATION_DISPLAY_MS);
  }

  return (
    <div class="copy-block">
      <code class="copy-block-code">{code}</code>
      <button
        type="button"
        class="copy-block-action"
        onClick={handleCopy}
        aria-label={`Copy ${label ?? code}`}
      >
        <Icon name="copy" size={16} />
      </button>
      <span class="copy-block-status" aria-live="polite">
        {status === "copied" ? "Copied" : status === "failed" ? "Copy failed" : ""}
      </span>
    </div>
  );
}
