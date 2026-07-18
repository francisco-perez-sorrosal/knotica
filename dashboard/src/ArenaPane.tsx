import { useEffect, useState } from "preact/hooks";

import type { ToolClient } from "./toolClient";
import type { ArenaHistory, ArenaStatus, ArenaVariant, WikiStatus } from "./types";

export function ArenaPane({
  client,
  topic,
  vault,
  status: wikiStatus,
  onOpenAsk,
  onOpenLoop,
}: {
  client: ToolClient | null;
  topic: string;
  vault: string;
  status: WikiStatus | null;
  onOpenAsk?: () => void;
  onOpenLoop?: () => void;
}) {
  const [status, setStatus] = useState<ArenaStatus | null>(null);
  const [history, setHistory] = useState<ArenaHistory | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function refresh() {
    if (!client) return;
    setBusy(true);
    setError(null);
    try {
      const [nextStatus, nextHistory] = await Promise.all([
        client.arenaStatus(topic, vault),
        client.arenaHistory(topic, vault, 12),
      ]);
      setStatus(nextStatus);
      setHistory(nextHistory);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void refresh();
    if (!client) return;
    const id = window.setInterval(() => void refresh(), 2500);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, topic, vault]);

  const variants = [...(status?.variants ?? [])].sort(
    (a, b) => (b.scalar ?? -1) - (a.scalar ?? -1),
  );
  const racing = status?.stage === "racing" || status?.stage === "promoting";
  const completed = status?.stage === "completed";
  const reverted = status?.stage === "reverted";
  const baseline = status?.baseline_scalar;
  const maxScalar = Math.max(
    0.0001,
    baseline ?? 0,
    ...variants.map((v) => v.scalar ?? 0),
  );

  return (
    <main class="pane-main arena">

      <section class="ingest-hero">
        <div>
          <p class="eyebrow">Prompt arena</p>
          <h2 class="ingest-heading">The argument</h2>
          <p class="muted">
            Reactive heal path: variants of <code>query.md</code> race the golden bar. Compile is
            the other path (Vault) — both end at Ask.
          </p>
        </div>
        <div
          class={`ingest-pulse health-${completed ? "ok" : reverted ? "bad" : racing ? "warn" : "ok"} ${
            racing ? "live" : status ? "idle" : "empty"
          }`}
        >
          <span class="pulse-dot" aria-hidden="true" />
          <strong>{status ? `Arena · ${status.stage}` : "Waiting"}</strong>
          <small>
            {status?.race_id ? `race ${status.race_id}` : busy ? "loading…" : "no race yet"}
          </small>
        </div>
      </section>

      {completed ? (
        <aside class="loop-banner tone-heal">
          <strong>Winner promoted</strong>
          <span>
            {status?.winner_id} cleared baseline
            {status?.winner_scalar != null ? ` at ${status.winner_scalar.toFixed(4)}` : ""}.
            Ask the same question again to prove the heal.
          </span>
          {onOpenAsk ? (
            <button type="button" onClick={onOpenAsk}>
              Prove in Ask
            </button>
          ) : null}
        </aside>
      ) : null}

      {reverted ? (
        <aside class="loop-banner tone-regression">
          <strong>No winner</strong>
          <span>
            Best variant stayed under baseline — the regression was reverted. Honest outcome; try
            another candidate or inspect history.
          </span>
          {onOpenLoop ? (
            <button type="button" onClick={onOpenLoop}>
              Back to Loop
            </button>
          ) : null}
        </aside>
      ) : null}

      <div class="arena-toolbar">
        <button type="button" disabled={!client || busy} onClick={() => void refresh()}>
          {busy ? "Refreshing…" : "Refresh"}
        </button>
        {onOpenAsk ? (
          <button type="button" onClick={onOpenAsk}>
            Try Ask again
          </button>
        ) : null}
      </div>

      {error ? (
        <aside role="alert" class="ask-error">
          {error}
        </aside>
      ) : null}

      <section class="panel arena-board">
        <header>
          <div>
            <h2>Leaderboard</h2>
            <p>
              {baseline != null ? `Baseline ${baseline.toFixed(4)}` : "Baseline unset"}
              {status?.message ? ` · ${status.message}` : ""}
            </p>
          </div>
          {baseline != null ? (
            <span class="baseline-pill">gate {baseline.toFixed(4)}</span>
          ) : null}
        </header>
        {variants.length === 0 ? (
          <p class="muted empty-check">No variants yet — a red gate on Loop triggers a race.</p>
        ) : (
          <ul class="arena-lanes">
            {variants.map((variant, index) => (
              <ArenaLane
                key={variant.id}
                rank={index + 1}
                variant={variant}
                maxScalar={maxScalar}
                baseline={baseline}
              />
            ))}
          </ul>
        )}
      </section>

      <section class="panel">
        <header>
          <h2>History</h2>
          <p>{history?.races.length ?? 0} recent race(s)</p>
        </header>
        {(history?.races.length ?? 0) === 0 ? (
          <p class="muted empty-check">No finished races recorded.</p>
        ) : (
          <ul class="arena-history">
            {history!.races
              .slice()
              .reverse()
              .map((race) => (
                <li key={String(race.race_id)} class={`hist-${String(race.stage)}`}>
                  <code>{String(race.race_id)}</code>
                  <span>{String(race.stage)}</span>
                  <span>
                    {race.winner_id ? `winner ${String(race.winner_id)}` : "no winner"}
                    {typeof race.winner_scalar === "number"
                      ? ` · ${race.winner_scalar.toFixed(4)}`
                      : ""}
                  </span>
                </li>
              ))}
          </ul>
        )}
      </section>
    </main>
  );
}

function ArenaLane({
  rank,
  variant,
  maxScalar,
  baseline,
}: {
  rank: number;
  variant: ArenaVariant;
  maxScalar: number;
  baseline: number | null | undefined;
}) {
  const width =
    variant.scalar == null ? 8 : Math.max(8, Math.round((variant.scalar / maxScalar) * 100));
  const baselinePct =
    baseline == null ? null : Math.max(4, Math.min(96, Math.round((baseline / maxScalar) * 100)));
  const clears =
    variant.scalar != null && baseline != null ? variant.scalar >= baseline - 1e-9 : null;
  const delta =
    variant.scalar != null && baseline != null ? variant.scalar - baseline : null;

  return (
    <li class={`arena-lane status-${variant.status}`}>
      <div class="lane-meta">
        <strong>
          <span class="lane-rank">#{rank}</span> {variant.label}
        </strong>
        <span class={`lane-badge ${clears === true ? "clears" : clears === false ? "below" : ""}`}>
          {variant.status}
          {delta != null ? ` · ${delta >= 0 ? "+" : ""}${delta.toFixed(3)}` : ""}
        </span>
        <em>{variant.scalar == null ? "…" : variant.scalar.toFixed(4)}</em>
      </div>
      <div class="lane-track" aria-hidden="true">
        {baselinePct != null ? (
          <span class="lane-baseline" style={{ left: `${baselinePct}%` }} title="baseline" />
        ) : null}
        <div class="lane-fill" style={{ width: `${width}%` }} />
      </div>
    </li>
  );
}
