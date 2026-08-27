/**
 * How one answer to a wiki question is presented — its markdown body, its
 * citations, and its source pages, as a single card. Extracted verbatim from
 * `AskPane.tsx` when the ask pane dissolved into Answer's `ask -> cite ->
 * react` rail: the pane is gone, this card is not — `AnswerLane.tsx` renders
 * it for the answer it just received, and `ProveStage.tsx` renders it for
 * Improve's own in-lane probe.
 *
 * Kept as its own module rather than folded into either caller so neither
 * lane owns the other's rendering: two surfaces read the same `QueryAnswer`
 * shape, so how that shape reads is shared vocabulary, not one lane's
 * private detail.
 */

import type { ComponentChildren } from "preact";
import { useMemo } from "preact/hooks";
import DOMPurify from "dompurify";
import { marked } from "marked";

import {
  ObsidianFileLink,
  sourceRelativePath,
  topicPageRelativePath,
  type ObsidianContext,
} from "./obsidianLinks";
import type { QueryAnswer } from "./types";

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

/** Renders one Before/After/Latest answer card. */
export function AnswerCard({
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
