import type { JSX } from "preact";
import { useState } from "preact/hooks";

import { Icon } from "./icons";

const CONFIRMATION_DISPLAY_MS = 1400;

/**
 * A mono code block with a copy affordance (design §3.2/§3.5) -- the
 * remediation-hint and drift-check rendering the dashboard already carries
 * as plain prose or a bare `<code>`, upgraded to something a user can act
 * on without retyping.
 *
 * `code` is what the reader *sees*; `copyText` is what lands on the
 * clipboard. They diverge in exactly one place today -- the handoff dispatch
 * line, where the visible line is the bare `/knotica:fill …` invocation but
 * the payload is `dec-091`'s prose-first text, since a non-slash host routes
 * on the prose. Defaulting `copyText` to `code` keeps every other call site
 * unchanged.
 *
 * `actionLabel` turns the 24x24 icon button into a labelled one. When it is
 * present the `aria-label` is dropped deliberately: the visible text is then
 * the accessible name, and carrying both would double-label the control.
 */
export function CopyBlock({
  code,
  label,
  copyText,
  actionLabel,
}: {
  code: string;
  label?: string;
  copyText?: string;
  actionLabel?: string;
}): JSX.Element {
  const [status, setStatus] = useState<"idle" | "copied" | "failed">("idle");

  async function handleCopy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(copyText ?? code);
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
        data-labelled={actionLabel ? "true" : undefined}
        onClick={handleCopy}
        aria-label={actionLabel ? undefined : `Copy ${label ?? code}`}
      >
        <Icon name="copy" size={16} />
        {actionLabel}
      </button>
      <span class="copy-block-status" aria-live="polite">
        {status === "copied" ? "Copied" : status === "failed" ? "Copy failed" : ""}
      </span>
    </div>
  );
}
