import type { JSX } from "preact";
import { useState } from "preact/hooks";

import { AnswerCard } from "../../answerPresentation";
import type { ObsidianContext } from "../../obsidianLinks";
import { PromptDiff } from "../../PromptDiff";
import { SectionCard } from "../../SectionCard";
import { Stat, StatGrid } from "../../Stat";
import { TermHint } from "../../TermHint";
import { Spinner } from "../../icons";
import type { ToolClient } from "../../toolClient";
import { findTopicRow } from "../../topicHelpers";
import type { QueryAnswer, WikiStatus } from "../../types";

/**
 * The `prove` stage body (`INTERFACE_DESIGN.md §2.4`) — the compiled
 * artifact's scalar, `prompt_diff mode=compiled`, and an **embedded probe**:
 * before/after `query` answer cards rendered in-lane. Per `§2.0` clause 2,
 * the probe's terminal state lives inside Improve, so this calls
 * `client.query` directly rather than linking to Answer — the same tool
 * `AnswerLane` itself calls, reusing the shared `AnswerCard`
 * (`answerPresentation.tsx`) for identical markdown-rendering and
 * citation-linking behavior.
 *
 * `query` carries no `confirm`/nonce parameter (unlike `run_once`/
 * `run_eval`), and the established UX for the same tool is a
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
      <SectionCard title="COMPILED PROGRAM" icon="state:complete">
        {compiled?.present ? (
          <>
            <StatGrid>
              <Stat label={hint("version")} value={compiled.version} />
              <Stat
                label={hint("scalar")}
                value={
                  compiled.scalar != null ? compiled.scalar.toFixed(4) : null
                }
              />
            </StatGrid>
            <PromptDiff
              client={client}
              topic={topic}
              vault={vault}
              mode="compiled"
            />
          </>
        ) : (
          <p class="muted">
            No compiled artifact yet — Prove activates once Promote merges one.
          </p>
        )}
      </SectionCard>

      <SectionCard
        title="PROBE"
        icon="lane:answer"
        footer={
          <>
            {result ? (
              <button
                type="button"
                class="ghost"
                data-testid="prove-probe-pin"
                disabled={busy}
                onClick={pinAsBefore}
              >
                Pin as Before
              </button>
            ) : null}
            {/* Sibling of the button, never a child: the accessible name
                stays `Probe it`. `query` mints no nonce and Answer's own
                `Ask` is a single click, so this stays single-click too — the
                chip is the honest marker for that spend, not a gate. */}
            <span class="chip cost">costs tokens</span>
            <button
              type="button"
              class="primary"
              data-testid="prove-probe-ask"
              disabled={!client || busy || !question.trim()}
              aria-busy={busy || undefined}
              onClick={() => void ask()}
            >
              {busy ? (
                <>
                  <Spinner />
                  Asking…
                </>
              ) : (
                "Probe it"
              )}
            </button>
          </>
        }
      >
        <>
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
          <p class="muted">
            Asks the compiled program directly,{" "}
            <TermHint
              id="prove-probe-cost"
              term="the same way Answer does"
              title="What a probe costs"
              body="A probe calls the model once, right now, and the answer is not stored. It is a read, so there is no two-phase confirm — but it does spend tokens."
            />
            .
          </p>
          {error ? (
            <aside role="alert" class="ask-error">
              {error}
            </aside>
          ) : null}
        </>
      </SectionCard>

      {pinned || result ? (
        <SectionCard
          title="BEFORE / AFTER"
          ariaLabel="Before and after probe answers"
        >
          <div class="ask-compare">
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
          </div>
        </SectionCard>
      ) : null}
    </div>
  );
}

/**
 * The explanatory copy behind each stat label's `TermHint`. Every accessible
 * name is `<term> — what this means`, which is why none of them can begin
 * with `open`/`watch` — the shape `ProveStage.test.tsx` forbids.
 */
const PROVE_HINTS = {
  version: {
    term: "VERSION",
    title: "Compiled version",
    body: "Which compiled prompt program this topic is currently answering with. It changes when Promote merges a branch.",
  },
  scalar: {
    term: "SCALAR",
    title: "Compiled scalar",
    body: "The held-out score of the program you are probing — the same number Promote merged on.",
  },
} as const;

function hint(key: keyof typeof PROVE_HINTS): JSX.Element {
  return <TermHint id={`prove-${key}`} {...PROVE_HINTS[key]} />;
}
