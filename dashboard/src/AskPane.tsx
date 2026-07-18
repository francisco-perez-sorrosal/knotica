import type { ComponentChildren } from "preact";
import { useMemo, useState } from "preact/hooks";
import DOMPurify from "dompurify";
import { marked } from "marked";

import { isCompileActive } from "./compileStages";
import {
  ObsidianFileLink,
  sourceRelativePath,
  topicPageRelativePath,
  type ObsidianContext,
} from "./obsidianLinks";
import type { ToolClient } from "./toolClient";
import { findTopicRow, queryTrainCount } from "./topicHelpers";
import type { QueryAnswer, WikiStatus } from "./types";

marked.setOptions({ gfm: true, breaks: true });

function markdownAnswerHtml(source: string): string {
  const withWikilinks = source.replace(
    /\[\[([^\]]+)\]\]/g,
    (_match, label: string) => `\`[[${label}]]\``,
  );
  const html = marked.parse(withWikilinks, { async: false }) as string;
  return DOMPurify.sanitize(html);
}

function MarkdownAnswer({ text }: { text: string }) {
  const html = useMemo(() => markdownAnswerHtml(text), [text]);
  return (
    <div
      class="ask-answer ask-answer-md"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

export function AskPane({
  client,
  topic,
  vault,
  obsidianCtx,
  status,
  onOpenLoop,
  onOpenArena,
}: {
  client: ToolClient | null;
  topic: string;
  vault: string;
  obsidianCtx: ObsidianContext;
  status: WikiStatus | null;
  onOpenLoop?: () => void;
  onOpenArena?: () => void;
}) {
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<QueryAnswer | null>(null);
  const [pinned, setPinned] = useState<QueryAnswer | null>(null);
  const [curateNote, setCurateNote] = useState<string | null>(null);

  const after =
    pinned && result && result.question === pinned.question && result.answer !== pinned.answer
      ? result
      : null;

  const topicRow = findTopicRow(status, topic);
  const compiled = Boolean(topicRow?.compiled?.present);
  const compileReady = Boolean(topicRow?.compile_ready);
  const compileStage = status?.compile?.stage ?? "idle";
  const compiling = isCompileActive(compileStage);

  async function ask() {
    if (!client || !question.trim() || busy) return;
    setBusy(true);
    setError(null);
    setCurateNote(null);
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
    setQuestion(result.question);
  }

  async function curate(verdict: "good" | "bad") {
    if (!client || !result || busy) return;
    setBusy(true);
    setCurateNote(null);
    try {
      await client.curateExample(
        topic,
        result.question,
        result.answer,
        verdict,
        result.pages_used,
        vault,
      );
      setCurateNote(
        verdict === "good"
          ? "Saved as good — Vault curated count ticks toward compile-ready."
          : `Saved as ${verdict} example.`,
      );
    } catch (cause) {
      setCurateNote(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main class="pane-main ask">

      <section class="ingest-hero">
        <div>
          <p class="eyebrow">Ask the wiki</p>
          <h2 class="ingest-heading">Prove the improvement on {topic}</h2>
          <p class="muted">
            Pin <strong>Before</strong>, then either let Arena heal a red gate or Compile when ready
            — re-ask the same question. Engines stay invisible; the answer delta is the proof.
          </p>
        </div>
        <div
          class={`ingest-pulse ${
            after ? "health-ok idle" : compiling ? "health-warn live" : pinned ? "idle" : "empty"
          }`}
        >
          <span class="pulse-dot" aria-hidden="true" />
          <strong>
            {after
              ? "Proved"
              : compiling
                ? "Compiling…"
                : compiled
                  ? "Compiled active"
                  : pinned
                    ? "Before pinned"
                    : "Not pinned"}
          </strong>
          <small>
            {compiled && topicRow?.compiled?.scalar != null
              ? `scalar ${topicRow.compiled.scalar.toFixed(3)}`
              : pinned
                ? `${pinned.citations.length} citation(s)`
                : "ask and pin Before to start"}
          </small>
        </div>
      </section>

      {compileReady && !compiled && !compiling ? (
        <aside class="loop-banner tone-ready">
          <strong>Flywheel ready</strong>
          <span>
            Trainset has {queryTrainCount(topicRow)} query-style examples (compile
            floor met) — compile from Vault, merge the branch, then Ask again.
          </span>
        </aside>
      ) : null}

      {compiled && pinned && !after ? (
        <aside class="loop-banner tone-heal">
          <strong>Compiled engine is live</strong>
          <span>Re-ask the pinned question — After should match or beat Before on grounding.</span>
        </aside>
      ) : null}

      {status?.gate.state === "fail" && !compiling ? (
        <aside class="loop-banner tone-regression">
          <strong>Gate is red</strong>
          <span>Arena can race prompt variants while you keep curating toward compile.</span>
          {onOpenArena ? (
            <button type="button" onClick={onOpenArena}>
              Open Arena
            </button>
          ) : null}
        </aside>
      ) : null}

      <section class="ask-form panel">
        <label class="ask-label">
          <span>Question</span>
          <textarea
            rows={3}
            value={question}
            placeholder="Ask the wiki…"
            disabled={busy || !client}
            onInput={(event) => setQuestion((event.target as HTMLTextAreaElement).value)}
          />
        </label>
        <div class="ask-actions">
          <button
            type="button"
            disabled={!client || busy || !question.trim()}
            onClick={() => void ask()}
          >
            {busy ? "Asking…" : "Ask"}
          </button>
          {result ? (
            <button type="button" disabled={busy} onClick={pinAsBefore}>
              Pin as Before
            </button>
          ) : null}
          {pinned && onOpenLoop ? (
            <button type="button" class="ghost" onClick={onOpenLoop}>
              Watch Loop
            </button>
          ) : null}
        </div>
      </section>

      {error ? (
        <aside role="alert" class="ask-error">
          {error}
        </aside>
      ) : null}

      {pinned || result ? (
        <section class="ask-compare" aria-label="Before and after answers">
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
              actions={
                <div class="ask-curate">
                  <button type="button" disabled={busy} onClick={() => void curate("good")}>
                    Save as good
                  </button>
                  <button type="button" disabled={busy} onClick={() => void curate("bad")}>
                    Save as bad
                  </button>
                  {curateNote ? <span class="muted">{curateNote}</span> : null}
                </div>
              }
            />
          ) : null}
        </section>
      ) : (
        <p class="muted empty-check">
          Ask once and pin it as Before — that is the baseline both heal paths must beat.
        </p>
      )}

      {after ? (
        <p class="ask-delta" role="status">
          Same question, new answer
          {compiled ? " · served after compile/merge" : ""} — compare citations and grounding
          above.
        </p>
      ) : null}
    </main>
  );
}

function AnswerCard({
  title,
  tone,
  answer,
  topic,
  obsidianCtx,
  actions,
}: {
  title: string;
  tone: "before" | "after" | "latest";
  answer: QueryAnswer;
  topic: string;
  obsidianCtx: ObsidianContext;
  actions: ComponentChildren;
}) {
  return (
    <article class={`ask-result panel tone-${tone}`}>
      <header class="ask-card-head">
        <h3>{title}</h3>
        <span class="ask-tone-chip">{tone}</span>
      </header>
      <MarkdownAnswer text={answer.answer} />
      {answer.citations.length > 0 ? (
        <p class="ask-meta">
          Citations:{" "}
          {answer.citations.map((citation) => (
            <ObsidianFileLink
              key={citation}
              ctx={obsidianCtx}
              relativePath={sourceRelativePath(topic, citation)}
              className="ask-ref-link"
            >
              <code>{citation}</code>
            </ObsidianFileLink>
          ))}
        </p>
      ) : (
        <p class="ask-meta muted">No citations returned</p>
      )}
      {answer.pages_used.length > 0 ? (
        <p class="ask-meta muted">
          Pages:{" "}
          {answer.pages_used.map((page, index) => (
            <span key={page}>
              {index > 0 ? " · " : ""}
              <ObsidianFileLink
                ctx={obsidianCtx}
                relativePath={topicPageRelativePath(topic, page)}
                className="ask-ref-link"
              >
                {page}
              </ObsidianFileLink>
            </span>
          ))}
        </p>
      ) : null}
      {actions}
    </article>
  );
}
