import { useEffect, useState } from "preact/hooks";

import type { ToolClient } from "./toolClient";
import type {
  NoteDecisionEnvelope,
  NoteIntent,
  NotePromoteActionResult,
  NoteRecord,
  PromoteTarget,
} from "./types";

/** Mirrors `promote_note._GAP_ELIGIBLE_INTENTS` exactly -- the intents for
 * which the dispatcher's `target=gap` promotion is not rejected outright. */
const GAP_ELIGIBLE_INTENTS = new Set<NoteIntent>([
  "dispute",
  "gap",
  "question",
]);

/**
 * View 3 (promotion) from the interface design -- a card action, not a
 * route: a modal with the two UI-offered destinations. Training example is
 * the default; the held-out (golden) set is absent entirely, not greyed out
 * (the tool's `target=golden` always rejects; a human at a dashboard does
 * not need to see a closed door). "Knowledge gap" renders only for
 * `intent ∈ {dispute, gap, question}`.
 *
 * Two phases in one dialog: fill fields, then `mode=dry-run` renders the
 * server's own resolved question/grounding pages before an explicit
 * "Promote" applies -- the same defer-to-apply shape every mutating notes
 * action uses (see `ActionConfirm` in `notePresentation.tsx`), just richer
 * because promotion collects its own fields first.
 */
export function NotePromoteDialog({
  client,
  topic,
  vault,
  note,
  onClose,
  onPromoted,
}: {
  client: ToolClient | null;
  topic: string;
  vault: string;
  note: NoteRecord;
  onClose: () => void;
  onPromoted: () => void;
}) {
  const gapEligible = GAP_ELIGIBLE_INTENTS.has(note.intent);
  const [target, setTarget] = useState<PromoteTarget>("trainset");
  const [question, setQuestion] = useState(
    note.intent === "question" ? note.note : "",
  );
  const [answer, setAnswer] = useState("");
  const [verdict, setVerdict] = useState<"good" | "bad">("good");
  const [envelope, setEnvelope] = useState<NoteDecisionEnvelope | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  async function preview() {
    if (!client || busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await client.notesPromote(
        topic,
        note.note_id,
        target,
        "dry-run",
        { question, answer, verdict },
        vault,
      );
      if (result.mode !== "dry-run") return;
      setEnvelope(result);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function apply() {
    if (!client || busy) return;
    setBusy(true);
    setError(null);
    try {
      const result: NotePromoteActionResult | NoteDecisionEnvelope =
        await client.notesPromote(
          topic,
          note.note_id,
          target,
          "apply",
          { question, answer, verdict },
          vault,
        );
      if (result.mode !== "apply") return;
      onPromoted();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  const pagesUsed =
    envelope && Array.isArray(envelope.context.pages_used)
      ? (envelope.context.pages_used as string[])
      : [];
  const resolvedQuestion =
    envelope && typeof envelope.context.question === "string"
      ? envelope.context.question
      : question;

  return (
    <div class="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        class="modal notes-promote-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Promote note"
        onClick={(event) => event.stopPropagation()}
      >
        <h3>Promote note</h3>
        <p class="notes-promote-quote">&ldquo;{note.note}&rdquo;</p>

        {error ? (
          <p class="notes-error" role="alert">
            {error}
          </p>
        ) : null}

        {envelope ? (
          <>
            <p>{envelope.options[0]?.preview ?? envelope.summary}</p>
            <p>
              <strong>Q:</strong> {resolvedQuestion || "—"}
            </p>
            <p class="muted">
              Grounded in: {pagesUsed.length > 0 ? pagesUsed.join(", ") : "—"}
              <br />
              <span class="notes-promote-hint">
                the note&rsquo;s anchored wiki page, never the note itself
              </span>
            </p>
            <p class="muted">
              This crosses out of your private notes. The note itself stays
              where it is and records the promotion.
            </p>
            <div class="notes-promote-actions">
              <button
                type="button"
                class="primary"
                disabled={busy}
                onClick={() => void apply()}
              >
                {busy ? "…" : "Promote"}
              </button>
              <button
                type="button"
                class="ghost"
                disabled={busy}
                onClick={() => setEnvelope(null)}
              >
                Back
              </button>
            </div>
          </>
        ) : (
          <>
            <fieldset class="notes-promote-target">
              <label>
                <input
                  type="radio"
                  name="promote-target"
                  checked={target === "trainset"}
                  onChange={() => setTarget("trainset")}
                />
                Training example — adds a curated (question, pages, answer)
                example to this topic&rsquo;s training set.
              </label>
              {gapEligible ? (
                <label>
                  <input
                    type="radio"
                    name="promote-target"
                    checked={target === "gap"}
                    onChange={() => setTarget("gap")}
                  />
                  Knowledge gap — files it in the research queue.
                  <span class="muted notes-promote-hint">
                    {" "}
                    available because this note&rsquo;s intent is &ldquo;
                    {note.intent}&rdquo;
                  </span>
                </label>
              ) : null}
            </fieldset>

            <label class="notes-promote-field">
              <span>Question the wiki should answer</span>
              <textarea
                rows={2}
                value={question}
                onInput={(event) =>
                  setQuestion((event.target as HTMLTextAreaElement).value)
                }
              />
            </label>

            {target === "trainset" ? (
              <>
                <label class="notes-promote-field">
                  <span>Grounded answer (cited from the anchored pages)</span>
                  <textarea
                    rows={3}
                    value={answer}
                    onInput={(event) =>
                      setAnswer((event.target as HTMLTextAreaElement).value)
                    }
                  />
                </label>
                <fieldset class="notes-promote-verdict">
                  <label>
                    <input
                      type="radio"
                      name="promote-verdict"
                      checked={verdict === "good"}
                      onChange={() => setVerdict("good")}
                    />
                    good answer
                  </label>
                  <label>
                    <input
                      type="radio"
                      name="promote-verdict"
                      checked={verdict === "bad"}
                      onChange={() => setVerdict("bad")}
                    />
                    bad answer
                  </label>
                </fieldset>
              </>
            ) : null}

            <div class="notes-promote-actions">
              <button
                type="button"
                class="primary"
                disabled={
                  busy ||
                  !question.trim() ||
                  (target === "trainset" && !answer.trim())
                }
                onClick={() => void preview()}
              >
                {busy ? "…" : "Continue"}
              </button>
              <button
                type="button"
                class="ghost"
                disabled={busy}
                onClick={onClose}
              >
                Cancel
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
