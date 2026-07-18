import { useCallback, useEffect, useMemo, useState } from "preact/hooks";

import type { ToolClient } from "./toolClient";
import type {
  DatasetFileRow,
  DatasetRecords,
  DatasetRole,
  DatasetsInventory,
  GoldenCandidate,
  GoldenReview,
} from "./types";

type Busy = "inventory" | "bootstrap" | "save" | "freeze" | "records" | null;

export function DatasetsPane({
  client,
  topic,
  vault,
}: {
  client: ToolClient | null;
  topic: string;
  vault: string;
}) {
  const [inventory, setInventory] = useState<DatasetsInventory | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState<Busy>(null);
  const [expanded, setExpanded] = useState<DatasetRole | null>(null);
  const [records, setRecords] = useState<DatasetRecords | null>(null);
  const [review, setReview] = useState<GoldenReview | null>(null);
  const [candidates, setCandidates] = useState<GoldenCandidate[]>([]);
  const [dirty, setDirty] = useState(false);

  const reloadInventory = useCallback(async () => {
    if (!client) return;
    setBusy("inventory");
    setError(null);
    try {
      const payload = await client.datasetsInventory(topic, vault);
      setInventory(payload);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  }, [client, topic, vault]);

  useEffect(() => {
    void reloadInventory();
    setExpanded(null);
    setRecords(null);
    setReview(null);
    setCandidates([]);
    setDirty(false);
    setNote(null);
  }, [reloadInventory]);

  const loopFiles = useMemo(
    () => inventory?.files.filter((row) => row.group === "loop_corpora") ?? [],
    [inventory],
  );
  const pipelineFiles = useMemo(
    () => inventory?.files.filter((row) => row.group === "golden_pipeline") ?? [],
    [inventory],
  );

  const kept = useMemo(
    () => candidates.filter((row) => row._kept !== false),
    [candidates],
  );
  const floor = inventory?.floor ?? 20;
  const freezeReady =
    (inventory?.pipeline.reviewed_n ?? 0) >= floor &&
    (inventory?.overlaps.train_reviewed ?? 0) === 0;
  const contamination =
    (inventory?.overlaps.train_held_out ?? 0) +
    (inventory?.overlaps.train_reviewed ?? 0) +
    (inventory?.overlaps.train_candidates ?? 0);

  async function toggleExpand(role: DatasetRole) {
    if (expanded === role) {
      setExpanded(null);
      setRecords(null);
      return;
    }
    if (!client) return;
    setExpanded(role);
    setBusy("records");
    setError(null);
    try {
      const payload = await client.datasetsRecords(topic, role, vault);
      setRecords(payload);
      if (role === "candidates" || role === "reviewed") {
        try {
          const board = await client.goldenReviewLoad(topic, vault);
          setReview(board);
          setCandidates(board.candidates.map((row) => ({ ...row, _kept: true })));
          setDirty(false);
        } catch {
          setReview(null);
          setCandidates([]);
        }
      } else {
        setReview(null);
        setCandidates([]);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      setRecords(null);
    } finally {
      setBusy(null);
    }
  }

  async function runBootstrap() {
    if (!client) return;
    setBusy("bootstrap");
    setError(null);
    setNote(null);
    try {
      const result = await client.datasetsBootstrap(topic, vault);
      setNote(`Bootstrapped ${result.n_candidates} candidates → ${result.filename}`);
      await reloadInventory();
      setExpanded("candidates");
      const payload = await client.datasetsRecords(topic, "candidates", vault);
      setRecords(payload);
      const board = await client.goldenReviewLoad(topic, vault);
      setReview(board);
      setCandidates(board.candidates.map((row) => ({ ...row, _kept: true })));
      setDirty(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  }

  async function saveReviewed() {
    if (!client || !review) return;
    setBusy("save");
    setError(null);
    try {
      const accepted = kept.map(({ _kept: _flag, ...row }) => row);
      const result = await client.goldenReviewSave(topic, accepted, vault);
      setDirty(false);
      setNote(
        `Saved ${result.count} reviewed → golden.staging.reviewed.jsonl` +
          (result.commit_sha ? ` · ${result.commit_sha.slice(0, 8)}` : ""),
      );
      await reloadInventory();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  }

  async function runFreeze() {
    if (!client) return;
    if (
      !window.confirm(
        `Freeze ${inventory?.pipeline.reviewed_n ?? "?"} reviewed candidates into held-out golden.jsonl + MANIFEST.json?`,
      )
    ) {
      return;
    }
    setBusy("freeze");
    setError(null);
    try {
      const result = await client.datasetsFreeze(topic, vault);
      setNote(
        `Frozen ${result.n_frozen} into held-out` +
          (result.below_floor ? " (below floor — still wrote)" : "") +
          ` · ${result.commit_sha.slice(0, 8)}`,
      );
      await reloadInventory();
      setExpanded("held_out");
      setRecords(await client.datasetsRecords(topic, "held_out", vault));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  }

  function updateCandidate(index: number, patch: Partial<GoldenCandidate>) {
    setCandidates((rows) => rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
    setDirty(true);
  }

  if (!client) {
    return (
      <main class="pane-main">
        <p class="muted">Connect MCP to inspect datasets.</p>
      </main>
    );
  }

  return (
    <main class="pane-main datasets">
      <header class="datasets-toolbar">
        <div>
          <p class="eyebrow">Datasets</p>
          <h2 class="ingest-heading">{topic}</h2>
        </div>
        <div class="datasets-pipeline" aria-label="Golden pipeline">
          <span class={inventory?.pipeline.candidates_n ? "ready" : ""}>Bootstrap</span>
          <span aria-hidden="true">→</span>
          <span class={inventory && inventory.pipeline.reviewed_n >= floor ? "ready" : ""}>
            Review
          </span>
          <span aria-hidden="true">→</span>
          <span class={inventory?.pipeline.seal_ok ? "ready" : ""}>Freeze</span>
        </div>
        <div class="datasets-actions">
          <button
            type="button"
            disabled={busy !== null}
            onClick={() => void runBootstrap()}
            title="LLM synthesize candidates from entity pages"
          >
            {busy === "bootstrap" ? "Bootstrapping…" : "Bootstrap"}
          </button>
          <button
            type="button"
            disabled={busy !== null || !review || !dirty}
            onClick={() => void saveReviewed()}
          >
            {busy === "save" ? "Saving…" : "Save reviewed"}
          </button>
          <button
            type="button"
            class="primary"
            disabled={busy !== null || !freezeReady}
            title={
              !freezeReady
                ? `Need Reviewed ≥ ${floor} and zero train overlap`
                : "Promote Reviewed → held-out golden.jsonl"
            }
            onClick={() => void runFreeze()}
          >
            {busy === "freeze" ? "Freezing…" : "Freeze"}
          </button>
        </div>
      </header>

      {contamination > 0 ? (
        <aside class="datasets-contam" role="status">
          Contamination: {inventory!.overlaps.train_held_out} train∩held-out,{" "}
          {inventory!.overlaps.train_reviewed} train∩reviewed,{" "}
          {inventory!.overlaps.train_candidates} train∩candidates. Freeze refuses overlap.
        </aside>
      ) : (
        <p class="muted datasets-contam-ok">No train ↔ held-out/reviewed question overlap.</p>
      )}

      {note ? <p class="saved-note">{note}</p> : null}
      {error ? <aside role="alert">{error}</aside> : null}

      <section class="panel datasets-section">
        <header>
          <h3>Loop corpora</h3>
          <p class="muted">Required for compile / eval. Disk names unchanged.</p>
        </header>
        <DatasetTable
          rows={loopFiles}
          expanded={expanded}
          busy={busy === "records"}
          onToggle={(role) => void toggleExpand(role)}
        />
        {expanded && loopFiles.some((row) => row.role === expanded) && records ? (
          <RecordsPanel
            records={records}
            review={null}
            candidates={[]}
            onUpdateCandidate={() => undefined}
            readOnly
            askHint={expanded === "trainset"}
          />
        ) : null}
      </section>

      <section class="panel datasets-section">
        <header>
          <h3>Golden pipeline</h3>
          <p class="muted">
            Candidates → Reviewed → Freeze into held-out. Expand a row to inspect or curate.
          </p>
        </header>
        <DatasetTable
          rows={pipelineFiles}
          expanded={expanded}
          busy={busy === "records"}
          onToggle={(role) => void toggleExpand(role)}
        />
        {expanded &&
        pipelineFiles.some((row) => row.role === expanded) &&
        (records || review) ? (
          <RecordsPanel
            records={records}
            review={review}
            candidates={candidates}
            onUpdateCandidate={updateCandidate}
            readOnly={false}
            askHint={false}
          />
        ) : null}
      </section>
    </main>
  );
}

function DatasetTable({
  rows,
  expanded,
  busy,
  onToggle,
}: {
  rows: DatasetFileRow[];
  expanded: DatasetRole | null;
  busy: boolean;
  onToggle: (role: DatasetRole) => void;
}) {
  return (
    <table class="datasets-table">
      <thead>
        <tr>
          <th>Role</th>
          <th>File</th>
          <th>Count</th>
          <th>Ready</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => {
          const open = expanded === row.role;
          return (
            <tr
              key={row.role}
              class={`datasets-row ${open ? "open" : ""} ${row.exists ? "" : "missing"}`}
              onClick={() => onToggle(row.role)}
              title={`${row.purpose}\n${row.path}`}
            >
              <td>
                <strong>{row.label}</strong>
                <span class="datasets-role-file"> · {row.filename}</span>
              </td>
              <td>
                <code class="datasets-path">{row.path}</code>
              </td>
              <td>
                {row.exists
                  ? row.role === "trainset" && row.query_train_n != null
                    ? `${row.query_train_n} query / ${row.count} total`
                    : row.count
                  : "—"}
                {busy && open ? " …" : ""}
              </td>
              <td>
                <span class={`datasets-ready ${row.ready ? "ok" : "no"}`}>
                  {row.ready ? "ready" : row.exists ? "not ready" : "missing"}
                </span>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function RecordsPanel({
  records,
  review,
  candidates,
  onUpdateCandidate,
  readOnly,
  askHint,
}: {
  records: DatasetRecords | null;
  review: GoldenReview | null;
  candidates: GoldenCandidate[];
  onUpdateCandidate: (index: number, patch: Partial<GoldenCandidate>) => void;
  readOnly: boolean;
  askHint: boolean;
}) {
  if (review && candidates.length > 0 && !readOnly) {
    return (
      <div class="datasets-expand">
        <p class="muted">
          Editing <strong>{review.resumed ? "Reviewed" : "Candidates"}</strong> loaded from{" "}
          <code>{review.loaded_from}</code>. Discard/restore, then Save reviewed.
        </p>
        <div class="golden-cards">
          {candidates.map((cand, index) => (
            <MiniCandidate
              key={index}
              index={index}
              total={candidates.length}
              candidate={cand}
              review={review}
              onChange={(patch) => onUpdateCandidate(index, patch)}
            />
          ))}
        </div>
      </div>
    );
  }

  if (!records) return null;

  if (records.role === "seal") {
    const seal = records.records[0] as Record<string, unknown> | undefined;
    return (
      <div class="datasets-expand">
        <h4>
          {records.label} · <code>{records.filename}</code>
        </h4>
        {!seal ? (
          <p class="muted">No MANIFEST.json yet — Freeze to create the seal.</p>
        ) : (
          <dl class="datasets-seal">
            {Object.entries(seal).map(([key, value]) => (
              <div key={key}>
                <dt>{key}</dt>
                <dd>
                  <code>{String(value ?? "")}</code>
                </dd>
              </div>
            ))}
          </dl>
        )}
      </div>
    );
  }

  return (
    <div class="datasets-expand">
      <h4>
        {records.label} · <code>{records.filename}</code>
        <span class="muted">
          {" "}
          · {records.total} row{records.total === 1 ? "" : "s"}
          {records.truncated ? " (truncated)" : ""}
        </span>
      </h4>
      {askHint ? (
        <p class="muted">
          Trainset curation stays in Ask / <code>curate_example</code>. This view is inspect-only.
        </p>
      ) : null}
      {!records.exists || records.records.length === 0 ? (
        <p class="muted">Empty or missing.</p>
      ) : (
        <table class="datasets-records">
          <thead>
            <tr>
              <th>#</th>
              <th>Question / query</th>
              <th>Answer / verdict</th>
            </tr>
          </thead>
          <tbody>
            {records.records.map((row, index) => {
              const q = String(row.query ?? row.question ?? "");
              const a = String(
                row.corrected_answer || row.answer || row.reference_answer || row.verdict || "",
              );
              const verdict = row.verdict != null ? String(row.verdict) : "";
              return (
                <tr key={index}>
                  <td>{index + 1}</td>
                  <td>{q}</td>
                  <td>
                    {verdict ? <span class="datasets-verdict">{verdict}</span> : null}{" "}
                    {a.slice(0, 160)}
                    {a.length > 160 ? "…" : ""}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

function MiniCandidate({
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
  return (
    <article class={`gcard ${kept ? "" : "discarded"}`}>
      <div class="gcard-top">
        <span class="idx">
          {index + 1} / {total}
        </span>
        {duplicate ? <span class="dup-flag">duplicate of trainset</span> : null}
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
        Reference answer
        <textarea
          class="a"
          value={candidate.reference_answer}
          onInput={(event) =>
            onChange({ reference_answer: (event.target as HTMLTextAreaElement).value })
          }
        />
      </label>
      <div class="gcard-actions">
        <button type="button" class="toggle" onClick={() => onChange({ _kept: !kept })}>
          {kept ? "Discard" : "Restore"}
        </button>
      </div>
    </article>
  );
}
