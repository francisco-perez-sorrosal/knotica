import type { JSX } from "preact";
import { useState } from "preact/hooks";

import { ArmedButton } from "../ArmedButton";
import { ProcessBrief } from "../ProcessBrief";
import { ProcessOutcome } from "../ProcessOutcome";
import { Spinner } from "../../icons";
import type { ToolClient } from "../../toolClient";
import type { LoopCadenceConfig } from "../../types";

/**
 * The one control that switches `[loop] arena_scorer` from the dashboard.
 *
 * It rides the existing `loop action=cadence` rail — the same read-or-
 * additively-write call `ObserveStage` already makes on mount — so no new
 * tool, no new client method, and every other `[loop]` key round-trips
 * untouched. The server validates the value before writing, so a rejected
 * value never lands on disk and surfaces here as the typed error.
 *
 * Asymmetric by design: switching **to** `eval` is a two-click
 * armed→confirm, because it arms one full golden-set eval **per variant** on
 * every future race — the click itself bills nothing, but the consequence is
 * a spending decision and gets the deliberate treatment. Switching back to
 * `heuristic` is a single quiet click: going free needs no guard.
 */

/** Mirrors `core/loop_cadence_config.py`'s `ARENA_SCORERS`; the server is the
 *  authority and rejects anything else, so these are not re-validated here. */
const ARENA_SCORER_EVAL = "eval";
const ARENA_SCORER_HEURISTIC = "heuristic";

type ArenaScorerClient = Pick<ToolClient, "loopCadence">;

export function ArenaScorerSwitch({
  client,
  topic,
  vault,
  current,
  testId,
  onSwitched,
}: {
  client: ArenaScorerClient | null;
  topic: string;
  vault: string;
  /**
   * The resolved `[loop] arena_scorer`, or `null` when the caller has not
   * read it. `null` offers the switch **to** `eval` — every caller that
   * passes `null` does so from a surface that only exists because the race
   * was not gate-comparable.
   */
  current: string | null;
  testId?: string;
  onSwitched?: (config: LoopCadenceConfig) => void | Promise<void>;
}): JSX.Element {
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAs, setSavedAs] = useState<string | null>(null);

  const target =
    current === ARENA_SCORER_EVAL ? ARENA_SCORER_HEURISTIC : ARENA_SCORER_EVAL;

  async function applyScorer(): Promise<void> {
    if (!client || busy) return;
    setBusy(true);
    setError(null);
    setSavedAs(null);
    try {
      const config = await client.loopCadence(
        topic,
        { arenaScorer: target },
        vault,
      );
      // The server echoes the resolved config back — confirm from that, never
      // from the value we sent.
      setSavedAs(config.arena_scorer);
      await onSwitched?.(config);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
      setArmed(false);
    }
  }

  return (
    <>
      <div class="card-inline-actions">
        {target === ARENA_SCORER_EVAL ? (
          <>
            {/* Sibling of the button, never a child: the accessible name
                stays `Use eval scorer`. `warn`, not `cost` — this click
                spends nothing; it arms what the *next* race will spend. */}
            {/* Sibling of the button, never a child: the accessible name
                stays `Use eval scorer`. The brief carries the `arms billing`
                chip -- this click spends nothing; it arms what the *next*
                race will spend, and the registry states both halves in that
                order. */}
            <ProcessBrief
              process="improve.arena_scorer_switch"
              term="why swap it"
            />
            <ArmedButton
              armed={armed}
              busy={busy}
              disabled={!client}
              label="Use eval scorer"
              armedLabel="Confirm — future races bill per variant"
              busyLabel="Switching…"
              testId={testId}
              onArm={() => setArmed(true)}
              onConfirm={() => void applyScorer()}
              onCancel={() => setArmed(false)}
            />
          </>
        ) : (
          <button
            type="button"
            class="ghost"
            data-testid={testId}
            disabled={!client || busy}
            aria-busy={busy || undefined}
            onClick={() => void applyScorer()}
          >
            {busy ? (
              <>
                <Spinner />
                Switching…
              </>
            ) : (
              "Use heuristic scorer"
            )}
          </button>
        )}
      </div>
      {savedAs ? (
        /* Timing is the load-bearing fact here: both runners (the supervised
           service and the CLI watcher) rebuild from config every tick, so the
           switch needs no restart — but the numbers already on this card
           belong to the LAST race and will not change until a new one runs. */
        <p role="status" class="saved-note">
          {savedAs === ARENA_SCORER_EVAL
            ? "✓ Saved — the watcher reads config every tick, so the next race " +
              "scores with the eval scorer and bills per variant. No restart " +
              "needed; this card still shows the last race until then."
            : "✓ Saved — the next race scores with the free heuristic (not " +
              "gate-comparable, so it cannot pass the gate). No restart needed."}
        </p>
      ) : null}
      {/* The saved-note above is the outcome and owns the live region; this
          adds only the sixth answer, which here is that nothing further is
          owed -- both runners rebuild from config on their own tick. */}
      {savedAs ? <ProcessOutcome process="improve.arena_scorer_switch" /> : null}
      {error ? (
        <p role="alert" class="ask-error">
          {error}
        </p>
      ) : null}
    </>
  );
}
