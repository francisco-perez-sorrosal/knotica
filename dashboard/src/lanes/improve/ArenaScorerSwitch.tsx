import type { JSX } from "preact";
import { useState } from "preact/hooks";

import { ArmedButton } from "../ArmedButton";
import { ProcessBrief } from "../ProcessBrief";
import { ProcessOutcome } from "../ProcessOutcome";
import { Spinner } from "../../icons";
import type { ToolClient } from "../../toolClient";
import type { LoopCadenceConfig, LoopCadencePreview } from "../../types";

/**
 * The one control that switches `[loop] arena_scorer` from the dashboard.
 *
 * It rides the existing `loop action=cadence` rail — the same read-or-
 * additively-write call `ObserveStage` already makes on mount — so no new
 * tool, no new client method, and every other `[loop]` key round-trips
 * untouched. The server validates the value before writing, so a rejected
 * value never lands on disk and surfaces here as the typed error.
 *
 * Asymmetric by design: switching **to** `eval` is two clicks, because it arms
 * one full golden-set eval **per variant** on every future race — the click
 * itself bills nothing, but the consequence is a spending decision and gets
 * the deliberate treatment. Switching back to `heuristic` is a single quiet
 * click: going free needs no guard.
 *
 * The two clicks are the **server's** two phases, not a client-side dialog on
 * top of one call: the first click makes the free `arena_scorer="eval"` call,
 * which writes nothing and returns a quote plus a short-lived `confirm_nonce`;
 * the second redeems that nonce. So the arm state is the server's preview
 * envelope rather than a local boolean, and the estimate the confirm is
 * offered against is the server's own words.
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
  /** The server's phase-1 envelope, or `null` when nothing is armed. */
  const [armed, setArmed] = useState<LoopCadencePreview | null>(null);
  const [busy, setBusy] = useState<"preview" | "confirm" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [savedAs, setSavedAs] = useState<string | null>(null);

  const target =
    current === ARENA_SCORER_EVAL ? ARENA_SCORER_HEURISTIC : ARENA_SCORER_EVAL;

  /**
   * One call expression for both legs, so the confirm leg is provably the
   * preview leg plus the nonce. `confirm` is empty on every free call —
   * including every `heuristic` write, which the server does not gate.
   */
  async function callCadence(confirm: string): Promise<void> {
    if (!client || busy) return;
    // Only the gated write has a free leg; a `heuristic` write applies at once
    // and is therefore never "previewing".
    setBusy(
      target === ARENA_SCORER_EVAL && !confirm ? "preview" : "confirm",
    );
    setError(null);
    setSavedAs(null);
    try {
      const result = await client.loopCadence(
        topic,
        { arenaScorer: target },
        vault,
        confirm,
      );
      if ("confirm_nonce" in result) {
        // Nothing was written; hold the quote and wait for the second click.
        setArmed(result);
        return;
      }
      setArmed(null);
      // The server echoes the resolved config back — confirm from that, never
      // from the value we sent.
      setSavedAs(result.arena_scorer);
      await onSwitched?.(result);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <div class="card-inline-actions">
        {target === ARENA_SCORER_EVAL ? (
          <>
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
              armed={armed !== null}
              busy={busy !== null}
              disabled={!client}
              label="Use eval scorer"
              armedLabel="Confirm — future races bill per variant"
              busyLabel={busy === "preview" ? "Checking…" : "Switching…"}
              testId={testId}
              onArm={() => void callCadence("")}
              onConfirm={() => void callCadence(armed?.confirm_nonce ?? "")}
              onCancel={() => setArmed(null)}
            />
          </>
        ) : (
          <button
            type="button"
            class="ghost"
            data-testid={testId}
            disabled={!client || busy !== null}
            aria-busy={busy !== null || undefined}
            onClick={() => void callCadence("")}
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
      {armed ? (
        /* The server's own quote, not a restatement of it: the estimate is
           what the free leg exists to fetch, and dropping it on the floor
           would make the round trip pure ceremony. */
        <p class="muted saved-note">{armed.estimated_cost}</p>
      ) : null}
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
