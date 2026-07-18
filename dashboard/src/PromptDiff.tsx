import { useEffect, useState } from "preact/hooks";

import type { ToolClient } from "./toolClient";
import type { PromptDiffMode, PromptDiffResult } from "./types";

function shortSha(value: string | null | undefined): string | null {
  if (!value) return null;
  return value.length > 12 ? value.slice(0, 12) : value;
}

function artifactBasename(path: string | null | undefined): string | null {
  if (!path) return null;
  const parts = path.split("/");
  return parts[parts.length - 1] ?? path;
}

export function PromptDiff({
  client,
  topic,
  vault,
  branch,
  baseRef,
  headRef,
  historyId,
  mode = "git",
  diffAvailable = true,
  label,
  hideLabel,
  unavailableMessage = "No preserved SHAs for this run — can't rebuild diff",
}: {
  client: ToolClient | null;
  topic: string;
  vault: string;
  branch?: string | null;
  baseRef?: string | null;
  headRef?: string | null;
  historyId?: string | null;
  mode?: PromptDiffMode;
  diffAvailable?: boolean;
  label?: string;
  hideLabel?: string;
  unavailableMessage?: string;
}) {
  const defaultLabel =
    mode === "compiled"
      ? "Show compiled program vs original"
      : "Show query.md diff";
  const resolvedLabel = label ?? defaultLabel;
  const resolvedHideLabel =
    hideLabel ??
    (mode === "compiled" ? "Hide compiled program diff" : "Hide query.md diff");

  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [diff, setDiff] = useState<PromptDiffResult | null>(null);

  useEffect(() => {
    setOpen(false);
    setDiff(null);
    setError(null);
  }, [topic, vault, branch, baseRef, headRef, historyId, mode]);

  async function load() {
    if (!client || !topic || busy) return;
    if (open && diff) {
      setOpen(false);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const next = await client.promptDiff(
        topic,
        branch ?? undefined,
        vault,
        baseRef ?? undefined,
        headRef ?? undefined,
        historyId ?? undefined,
        mode,
      );
      setDiff(next);
      setOpen(true);
    } catch (cause) {
      setDiff(null);
      setOpen(false);
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  const disabled = !client || !topic || !diffAvailable;

  const comparisonCaption =
    mode === "compiled"
      ? "Vault query.md (left) vs full compiled runtime program (right)"
      : null;

  const baseSha = shortSha(diff?.base_sha);
  const headSha = shortSha(diff?.head_sha);
  const mergeSha = shortSha(diff?.merge_sha);
  const artifactName = artifactBasename(diff?.artifact_path);

  return (
    <div class="prompt-diff">
      <button
        type="button"
        class="ghost prompt-diff-toggle"
        disabled={disabled || busy}
        aria-expanded={open}
        onClick={() => void load()}
      >
        {busy ? "Loading diff…" : open ? resolvedHideLabel : resolvedLabel}
      </button>
      {!diffAvailable ? (
        <p class="muted prompt-diff-empty">{unavailableMessage}</p>
      ) : null}
      {error ? (
        <p class="prompt-diff-error" role="alert">
          {error}
        </p>
      ) : null}
      {open && diff ? (
        <div class="prompt-diff-panel" aria-label="Query prompt diff">
          <header class="prompt-diff-meta">
            <code>{diff.path}</code>
            {baseSha && headSha ? (
              <span>
                {baseSha} ↔ {headSha}
              </span>
            ) : (
              <span>
                {diff.base_ref} → {diff.head_ref}
              </span>
            )}
            {mergeSha ? <span class="muted">merge {mergeSha}</span> : null}
            {diff.branch ? (
              <span class="muted">
                branch <code>{diff.branch}</code>
              </span>
            ) : null}
            {diff.source ? <span class="muted">via {diff.source}</span> : null}
            {comparisonCaption ? <span class="muted">{comparisonCaption}</span> : null}
            {mode === "compiled" && diff.demo_count != null && artifactName ? (
              <span class="muted">
                {diff.demo_count} demo{diff.demo_count === 1 ? "" : "s"} · artifact{" "}
                <code>{artifactName}</code>
              </span>
            ) : null}
            {diff.empty ? <span class="muted">No changes</span> : null}
            {diff.truncated ? <span class="health-chip warn">truncated</span> : null}
          </header>
          {diff.empty ? (
            <p class="muted prompt-diff-empty">
              {mode === "compiled"
                ? "Compiled runtime program matches vault query.md — no instruction or demo delta."
                : "The prompt is identical at both refs."}
            </p>
          ) : (
            <div class="prompt-diff-scroll">
              {diff.hunks.map((hunk) => (
                <section key={hunk.header} class="prompt-diff-hunk">
                  <div class="prompt-diff-hunk-header">{hunk.header}</div>
                  <pre class="prompt-diff-lines">
                    {hunk.lines.map((line, index) => (
                      <div
                        key={`${hunk.header}:${index}`}
                        class={`prompt-diff-line prompt-diff-line-${line.type}`}
                      >
                        <span class="prompt-diff-gutter prompt-diff-gutter-old">
                          {line.old_no ?? ""}
                        </span>
                        <span class="prompt-diff-gutter prompt-diff-gutter-new">
                          {line.new_no ?? ""}
                        </span>
                        <span class="prompt-diff-sign">
                          {line.type === "add" ? "+" : line.type === "del" ? "-" : " "}
                        </span>
                        <span class="prompt-diff-text">{line.text || " "}</span>
                      </div>
                    ))}
                  </pre>
                </section>
              ))}
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
