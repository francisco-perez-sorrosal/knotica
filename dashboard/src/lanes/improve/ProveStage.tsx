import type { JSX } from "preact";
import { useState } from "preact/hooks";

import { AnswerCard } from "../../AskPane";
import type { ObsidianContext } from "../../obsidianLinks";
import { PromptDiff } from "../../PromptDiff";
import type { ToolClient } from "../../toolClient";
import { findTopicRow } from "../../topicHelpers";
import type { QueryAnswer, WikiStatus } from "../../types";

/**
 * The `prove` stage body (`INTERFACE_DESIGN.md §2.4`) — the compiled
 * artifact's scalar, `prompt_diff mode=compiled`, and an **embedded probe**:
 * before/after `query` answer cards rendered in-lane. Per `§2.0` clause 2,
 * the probe's terminal state lives inside Improve, so this calls
 * `client.query` directly rather than linking to Answer — the same tool
 * `AskPane.tsx`'s own `ask()` already calls, reusing its `AnswerCard` for
 * identical markdown-rendering and citation-linking behavior.
 *
 * `query` carries no `confirm`/nonce parameter (unlike `run_once`/
 * `run_eval`), and `AskPane`'s own established UX for the same tool is a
 * single "Ask" click — this probe matches that precedent rather than
 * inventing an armed→confirm dialog for a call that isn't two-phase
 * anywhere else on this surface.
 */

export function ProveStage({
  client,
  topic,
  vault,
  status,
  obsidianCtx,
}: {
  client: ToolClient | null;
  topic: string;
  vault: string;
  status: WikiStatus | null;
  obsidianCtx: ObsidianContext;
}): JSX.Element {
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<QueryAnswer | null>(null);
  const [pinned, setPinned] = useState<QueryAnswer | null>(null);

  const after =
    pinned &&
    result &&
    result.question === pinned.question &&
    result.answer !== pinned.answer
      ? result
      : null;

  const topicRow = findTopicRow(status, topic);
  const compiled = topicRow?.compiled ?? null;

  async function ask() {
    if (!client || !question.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      const answer = await client.query(topic, question.trim(), vault);
      setResult(answer);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  function pinAsBefore() {
    if (!result) return;
    setPinned(result);
  }

  return (
    <div class="prove-stage">
      {compiled?.present ? (
        <div class="prove-compiled">
          <p class="muted">
            Compiled <strong>{compiled.version}</strong>
            {compiled.scalar != null
              ? ` · scalar ${compiled.scalar.toFixed(4)}`
              : ""}
          </p>
          <PromptDiff
            client={client}
            topic={topic}
            vault={vault}
            mode="compiled"
          />
        </div>
      ) : (
        <p class="muted">
          No compiled artifact yet — Prove activates once Promote merges one.
        </p>
      )}

      <div class="prove-probe">
        <label class="ask-label">
          <span>Probe question</span>
          <textarea
            rows={2}
            value={question}
            data-testid="prove-probe-question"
            placeholder="Ask the same question the flywheel is meant to improve…"
            disabled={busy || !client}
            onInput={(event) =>
              setQuestion((event.target as HTMLTextAreaElement).value)
            }
          />
        </label>
        <div class="ask-actions">
          <button
            type="button"
            data-testid="prove-probe-ask"
            disabled={!client || busy || !question.trim()}
            onClick={() => void ask()}
          >
            {busy ? "Asking…" : "Probe it"}
          </button>
          {result ? (
            <button
              type="button"
              data-testid="prove-probe-pin"
              disabled={busy}
              onClick={pinAsBefore}
            >
              Pin as Before
            </button>
          ) : null}
        </div>
      </div>

      {error ? (
        <aside role="alert" class="ask-error">
          {error}
        </aside>
      ) : null}

      {pinned || result ? (
        <section
          class="ask-compare"
          aria-label="Before and after probe answers"
        >
          {pinned ? (
            <AnswerCard
              title="Before"
              tone="before"
              answer={pinned}
              topic={topic}
              obsidianCtx={obsidianCtx}
              actions={null}
            />
          ) : null}
          {result ? (
            <AnswerCard
              title={after ? "After" : "Latest"}
              tone={after ? "after" : "latest"}
              answer={result}
              topic={topic}
              obsidianCtx={obsidianCtx}
              actions={null}
            />
          ) : null}
        </section>
      ) : null}
    </div>
  );
}
