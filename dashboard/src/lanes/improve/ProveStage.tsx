import type { JSX } from "preact";
import { useState } from "preact/hooks";

import { AnswerCard } from "../../answerPresentation";
import type { ObsidianContext } from "../../obsidianLinks";
import { PromptDiff } from "../../PromptDiff";
import { SectionCard } from "../../SectionCard";
import { Stat, StatGrid } from "../../Stat";
import { TermHint } from "../../TermHint";
import type { ToolClient } from "../../toolClient";
import { findTopicRow } from "../../topicHelpers";
import type { QueryAnswer, WikiStatus } from "../../types";
import { ArmedButton } from "../ArmedButton";
import { ProcessBrief } from "../ProcessBrief";
import { ProcessOutcome } from "../ProcessOutcome";

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
 * `run_eval`), so the probe's preview is the client-side `ArmedButton`
 * arm→confirm rather than a server-minted quote. It is two-phase all the
 * same: the spend grammar is uniform across every billed control, and
 * `AnswerLane`'s own `Ask` arms identically.
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
  /* The probe bills, so the first click only arms it — same grammar as every
     other billed control on this surface. */
  const [armed, setArmed] = useState(false);

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
    setArmed(false);
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
                stays `Probe it`. `query` mints no nonce, so the second click
                is client-side — the chip prices the spend and the armed
                label names what confirming costs. */}
            <ProcessBrief process="improve.probe" term="why probe" align="end" />
            <ArmedButton
              armed={armed}
              busy={busy}
              disabled={!client || !question.trim()}
              label="Probe it"
              armedLabel="Confirm probe — costs tokens"
              busyLabel="Asking…"
              className="primary"
              testId="prove-probe-ask"
              cancelTestId="prove-probe-cancel"
              onArm={() => setArmed(true)}
              onConfirm={() => void ask()}
              onCancel={() => setArmed(false)}
            />
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
              onInput={(event) => {
                setQuestion((event.target as HTMLTextAreaElement).value);
                // Editing the question invalidates what was armed.
                setArmed(false);
              }}
            />
          </label>
          <p class="muted">
            Asks the compiled program directly,{" "}
            <TermHint
              id="prove-probe-cost"
              term="the same way Answer does"
              title="What a probe costs"
              body="A probe calls the model once, right now, and the answer is not stored. It spends tokens, so the first click only arms the control and the second one confirms."
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
          <>
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
            {/* The answer cards above are the outcome, so nothing is announced
                twice here -- what this adds is the sixth answer, which for a
                probe is that there is no seventh. */}
            {result ? <ProcessOutcome process="improve.probe" /> : null}
          </>
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
