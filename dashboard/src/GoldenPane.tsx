import { useEffect, useMemo, useState } from "preact/hooks";

import type { ToolClient } from "./toolClient";
import type { GoldenCandidate, GoldenReview } from "./types";

export function GoldenPane({
  client,
  topic,
  vault,
}: {
  client: ToolClient | null;
  topic: string;
  vault: string;
}) {
  const [review, setReview] = useState<GoldenReview | null>(null);
  const [candidates, setCandidates] = useState<GoldenCandidate[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedNote, setSavedNote] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!client) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setSavedNote(null);
    void client
      .goldenReviewLoad(topic, vault)
      .then((payload) => {
        if (cancelled) return;
        const rows = payload.candidates.map((row) => ({ ...row, _kept: true }));
        setReview(payload);
        setCandidates(rows);
        setDirty(false);
        if (payload.resumed) {
          setSavedNote(`Resumed from a previous review (${payload.loaded_from}).`);
        }
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setReview(null);
          setCandidates([]);
          setError(cause instanceof Error ? cause.message : String(cause));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [client, topic, vault]);

  const kept = useMemo(() => candidates.filter((row) => row._kept !== false), [candidates]);
  const kpiClass =
    kept.length < (review?.floor ?? 20)
      ? "low"
      : kept.length <= (review?.target_high ?? 30)
        ? "good"
        : "high";

  function update(index: number, patch: Partial<GoldenCandidate>) {
    setCandidates((rows) => rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
    setDirty(true);
  }

  async function save() {
    if (!client || !review) return;
    setSaving(true);
    setError(null);
    try {
      const accepted = kept.map(({ _kept: _flag, ...row }) => row);
      const result = await client.goldenReviewSave(topic, accepted, vault);
      setDirty(false);
      setSavedNote(
        `Saved ${result.count} candidates to ${result.written}` +
          (result.commit_sha ? ` · ${result.commit_sha.slice(0, 8)}` : ""),
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <main class="pane-main">
        <p class="muted">Loading golden staging…</p>
      </main>
    );
  }

  if (error && !review) {
    return (
      <main class="pane-main">
        <section class="panel">
          <h2>Golden set</h2>
          <p class="muted">{error}</p>
          <p class="muted">
            Bootstrap first with <code>knotica eval --bootstrap --topic {topic}</code>, then return
            here.
          </p>
        </section>
      </main>
    );
  }

  if (!review) return null;

  return (
    <main class="pane-main golden">
      <header class="golden-toolbar">
        <div>
          <p class="eyebrow">Golden-set review</p>
          <h2 class="ingest-heading">
            {review.topic}{" "}
            <span class="muted-inline">· {review.vault_name}</span>
          </h2>
        </div>
        <div class={`kpi kpi-${kpiClass}`} title={`${review.floor}–${review.target_high} target`}>
          <strong>
            {kept.length} / {candidates.length}
          </strong>
          <span>kept</span>
        </div>
        {dirty ? <span class="dirty-flag">unsaved changes</span> : <span />}
        <button type="button" class="primary" disabled={saving || !dirty} onClick={() => void save()}>
          {saving ? "Saving…" : "Save reviewed set"}
        </button>
      </header>

      <p class="lesson">
        Keep <strong>{review.floor}–{review.target_high}</strong> strong candidates. Check that each
        question is answerable from the wiki, the reference answer is judge ground truth, citations
        resolve (green), and supporting quotes still exist. An orange <strong>duplicate</strong> flag
        means the question already appears in <code>qa.jsonl</code>.
      </p>

      {savedNote ? <p class="saved-note">{savedNote}</p> : null}
      {error ? <aside role="alert">Save failed: {error}</aside> : null}

      <div class="golden-cards">
        {candidates.map((cand, index) => (
          <CandidateCard
            key={index}
            index={index}
            total={candidates.length}
            candidate={cand}
            review={review}
            onChange={(patch) => update(index, patch)}
          />
        ))}
      </div>
    </main>
  );
}

function CandidateCard({
  index,
  total,
  candidate,
  review,
  onChange,
}: {
  index: number;
  total: number;
  candidate: GoldenCandidate;
  review: GoldenReview;
  onChange: (patch: Partial<GoldenCandidate>) => void;
}) {
  const kept = candidate._kept !== false;
  const duplicate = review.qa_questions.includes(candidate.question.trim().toLowerCase());
  const citationsText = candidate.citations.join(", ");

  return (
    <article class={`gcard ${kept ? "" : "discarded"}`}>
      <div class="gcard-top">
        <span class="idx">
          candidate {index + 1} / {total}
        </span>
        {duplicate ? <span class="dup-flag">duplicate of a qa.jsonl question</span> : null}
      </div>

      <label>
        Question
        <textarea
          class="q"
          value={candidate.question}
          onInput={(event) => onChange({ question: (event.target as HTMLTextAreaElement).value })}
        />
      </label>

      <label>
        Reference answer (judge ground truth)
        <textarea
          class="a"
          value={candidate.reference_answer}
          onInput={(event) =>
            onChange({ reference_answer: (event.target as HTMLTextAreaElement).value })
          }
        />
      </label>

      <label>
        Citations (comma-separated source keys)
        <input
          type="text"
          value={citationsText}
          onInput={(event) => {
            const value = (event.target as HTMLInputElement).value;
            onChange({
              citations: value
                .split(",")
                .map((part) => part.trim())
                .filter(Boolean),
            });
          }}
        />
      </label>
      <div class="badges">
        {candidate.citations.map((key) => {
          const ok = review.source_keys.includes(key);
          return ok ? (
            <a class="cite ok" href={review.citation_links[key]} title="Open source in Obsidian">
              ✓ {key}
            </a>
          ) : (
            <span class="cite bad" title="No such stored source">
              ✗ {key}
            </span>
          );
        })}
      </div>

      <p class="field-label">Pages used</p>
      <div class="chips">
        {candidate.pages_used.map((page) => {
          const info = review.pages[page];
          return info?.exists ? (
            <a class="chip ok" href={info.obsidian_uri} title="Open page in Obsidian">
              ✓ {page}
            </a>
          ) : (
            <span class="chip bad" title="Missing page">
              ✗ {page}
            </span>
          );
        })}
      </div>

      {(candidate.support?.length ?? 0) > 0 ? (
        <>
          <p class="field-label">Supporting quotes</p>
          <div class="quotes">
            {candidate.support!.map((entry, quoteIndex) => (
              <div class="quote" key={quoteIndex}>
                <blockquote>{entry.quote}</blockquote>
                <div class="meta">
                  {entry.page}
                  {entry.current
                    ? ` · lines ${entry.current.line_start}–${entry.current.line_end}`
                    : entry.line_start
                      ? ` · lines ${entry.line_start}–${entry.line_end ?? entry.line_start}`
                      : ""}
                  {entry.page && review.pages[entry.page]?.exists ? (
                    <>
                      {" · "}
                      <a href={review.pages[entry.page].obsidian_uri}>open page</a>
                    </>
                  ) : (
                    <span class="unlocated"> · quote not verified in vault</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </>
      ) : null}

      <div class="gcard-actions">
        <button
          type="button"
          class="toggle"
          onClick={() => onChange({ _kept: !kept })}
        >
          {kept ? "Discard" : "Restore"}
        </button>
      </div>
    </article>
  );
}
