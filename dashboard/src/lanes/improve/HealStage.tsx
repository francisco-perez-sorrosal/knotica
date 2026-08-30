import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";

import { ArenaScorerSwitch } from "./ArenaScorerSwitch";
import { ProcessBrief } from "../ProcessBrief";
import { ProcessOutcome } from "../ProcessOutcome";
import { ArmedButton } from "../ArmedButton";
import { SectionCard } from "../../SectionCard";
import { Stat, StatGrid } from "../../Stat";
import { StateList } from "../../StateList";
import type { StateListRow } from "../../StateList";
import { TermHint } from "../../TermHint";
import type { IconName } from "../../icons";
import type { SectionTone } from "../../SectionCard";
import type { ToolClient } from "../../toolClient";
import type { ArenaHistory, ArenaStatus, ArenaVariant, WikiStatus } from "../../types";

/**
 * The `heal` stage body — absorbs `ArenaPane`'s status/history/leaderboard and
 * `CompilePanel.tsx`'s `compile action=run`. Never independently reachable: it
 * opens (fetches arena data, offers the compile control) only once
 * `status.gate.state === "fail"` — exactly today's `LoopPane` wiring, where
 * the Heal step only turns "ready" after a gate refusal.
 *
 * `compile action=run` is a single billed call with no free preview leg
 * (unlike `gate`'s `run_once`, which mints a server-side nonce). Per the
 * orchestrator's no-native-dialogs ruling (`LEARNINGS.md`), a spend-immediately
 * control with no nonce cycle gates on the shared `ArmedButton` two-click
 * affordance instead of `window.confirm()` — never native, and never a single
 * click.
 *
 * The open body is two `SectionCard`s — ARENA (what the race is doing) and
 * COMPILE (the one billed action, in the footer of the card that explains it)
 * — plus, only when the race aborted, a warn-toned card between them that
 * carries the server's own abort reason verbatim and the next step as a
 * control rather than as instructions: the same two-click `Use eval scorer`
 * switch `ObserveStage` offers, so the fix is exercised where the problem is
 * reported. The hand-edit it performs (`arena_scorer = "eval"` under
 * `[loop]`) stays named on a muted line, and the prerequisites stay, because
 * a switch made without them falls back to the heuristic and aborts again.
 * The variant race is a `StateList`, so each variant's
 * state word sits as visible text next to its icon with its scalar in a
 * right-aligned tabular column; each variant's `TermHint` carries the
 * scalar's provenance (`scorer_id` / `n_examples`) and what it tried
 * (`change_summary`, a diff-derived line — `null` says so honestly rather
 * than rendering nothing), which the wire already ships per variant. A row
 * whose wire also carries a `diff` gets a quiet row action to inspect it
 * inline. The loose "N recent race(s)" counter is a labelled `Stat`, joined
 * by BASELINE and SCORER so the race's measuring stick and instrument are
 * readable without leaving the card.
 */

export function HealStage({
  client,
  topic,
  vault,
  status,
  onStatusRefresh,
}: {
  client: ToolClient | null;
  topic: string;
  vault: string;
  status: WikiStatus | null;
  onStatusRefresh?: () => void | Promise<void>;
}): JSX.Element {
  const open = status?.gate.state === "fail";
  const [arenaStatus, setArenaStatus] = useState<ArenaStatus | null>(null);
  const [arenaHistory, setArenaHistory] = useState<ArenaHistory | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  // The CONFIGURED `[loop] arena_scorer` — distinct from the historical
  // scorer the last race carries. Read when the stage opens (a read, so the
  // mount-effect audit's reads-only contract holds) and updated from the
  // server's echo after a switch, so the control and its surrounding copy
  // reflect the real standing state, not the stale race.
  const [configuredScorer, setConfiguredScorer] = useState<string | null>(null);
  // Bumped to re-run the arena read after something that could change what
  // the next race does — cheaper and more honest than caching a derived copy.
  const [arenaReloads, setArenaReloads] = useState(0);
  // At most one variant's diff panel open at a time — a global toggle, not
  // per-row local state, so opening one closes any other already open.
  const [openDiffId, setOpenDiffId] = useState<string | null>(null);
  /* Whether the last compile actually landed. `compile action=run` returns
     the branch it wrote and no sentence about it, and the only visible
     effect was a status re-read -- so a finished compile and a click that
     did nothing looked identical. Held in the stage so it survives the
     arena re-read that follows. */
  const [compiled, setCompiled] = useState(false);

  useEffect(() => {
    if (!open || !client) return;
    let cancelled = false;
    void (async () => {
      try {
        const [nextStatus, nextHistory, cadence] = await Promise.all([
          client.arenaStatus(topic, vault),
          client.arenaHistory(topic, vault, 12),
          // A read (no overrides): what `[loop] arena_scorer` stands at NOW,
          // so the abort card's next-step reflects standing config rather
          // than urging a switch that already happened. Failure is
          // non-fatal — the switch then simply offers eval.
          client.loopCadence(topic, undefined, vault).catch(() => null),
        ]);
        if (cancelled) return;
        setArenaStatus(nextStatus);
        setArenaHistory(nextHistory);
        if (cadence) setConfiguredScorer(cadence.arena_scorer ?? null);
      } catch (cause) {
        if (cancelled) return;
        setError(cause instanceof Error ? cause.message : String(cause));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, client, topic, vault, arenaReloads]);

  useEffect(() => {
    setArmed(false);
  }, [open]);

  if (!open) {
    return (
      <p class="muted">
        Opens after a gate fail — the watcher races prompt variants in the arena
        until one clears baseline, or reverts if none do.
      </p>
    );
  }

  async function runCompile() {
    if (!client || busy) return;
    setBusy(true);
    setError(null);
    setCompiled(false);
    try {
      await client.compileRun(topic, vault);
      setCompiled(true);
      await onStatusRefresh?.();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
      setArmed(false);
    }
  }

  const variants = [...(arenaStatus?.variants ?? [])].sort(
    (a, b) => (b.scalar ?? -1) - (a.scalar ?? -1),
  );
  const aborted = arenaStatus?.stage === "aborted";

  function toggleDiff(variantId: string) {
    setOpenDiffId((current) => (current === variantId ? null : variantId));
  }

  return (
    <div class="heal-stage">
      <SectionCard
        title="ARENA"
        icon="stage:heal"
        headerActions={stageChip(arenaStatus)}
      >
        <>
          <StatGrid>
            <Stat label="VARIANTS" value={arenaStatus?.variants.length} />
            <Stat
              label={
                <TermHint
                  id="heal-baseline"
                  term="BASELINE"
                  title="Gate baseline"
                  body="The frozen eval scalar a winning variant must clear. Only a scorer that shares the baseline's scale can be ranked against it — a race scored on a different instrument cannot clear it by definition."
                />
              }
              value={arenaStatus?.baseline_scalar?.toFixed(4)}
            />
            <Stat
              label={
                <TermHint
                  id="heal-scorer"
                  term="SCORER"
                  title="Race scorer"
                  body="What produced the variants' scalars. heuristic-keyword is free and network-free but shares no scale with the eval-derived gate baseline, so the arena refuses to rank its races against the gate. The eval scorer runs the golden-set harness per variant — gate-comparable, and billed per variant."
                />
              }
              value={scorerValue(arenaStatus)}
            />
            <Stat
              label={
                <TermHint
                  id="heal-recent-races"
                  term="RECENT RACES"
                  title="Recent races"
                  body="How many variant races this topic has run lately. History only; it does not gate anything."
                />
              }
              value={arenaHistory?.races.length}
            />
          </StatGrid>
          <p class="muted">
            {arenaStatus
              ? aborted
                ? "The watcher races prompt variants against each other and keeps the one that clears the gate baseline."
                : (arenaStatus.message ??
                  "The watcher races prompt variants against each other and keeps the one that clears the gate baseline.")
              : "Reading the arena…"}
          </p>
          {variants.length > 0 ? (
            <StateList
              label="Arena variants"
              rows={variants.map((variant) =>
                variantRow(variant, openDiffId, toggleDiff),
              )}
            />
          ) : null}
          {error ? (
            <p role="alert" class="ask-error">
              {error}
            </p>
          ) : null}
        </>
      </SectionCard>

      {aborted ? (
        <SectionCard title="WHY THE RACE ABORTED" icon="state:blocked" tone="warn">
          <>
            {arenaStatus?.message ? (
              // The server's own reason, verbatim — this card never re-words it.
              <p data-testid="heal-abort-reason">{arenaStatus.message}</p>
            ) : null}
            <p class="muted">
              Aborted means the race stopped before ranking anything: the
              scorer and the gate baseline are different instruments, so no
              ranking between them would mean anything. The variants above
              were generated but never judged against the gate — nothing was
              promoted and nothing was lost.
            </p>
            <span class="microlabel">NEXT STEP</span>
            {configuredScorer === "eval" ? (
              /* The switch already happened (here earlier, in Observe, or by
                 hand) — urging it again would read as the click having done
                 nothing. State the standing config instead. */
              <p
                role="status"
                class="saved-note"
                data-testid="heal-scorer-configured"
              >
                ✓ The eval scorer is already configured — the next race scores
                with it and bills per variant. This card still shows the last
                race until that race runs.
              </p>
            ) : (
              <p class="muted">
                Make races gate-comparable by switching the arena to the
                eval-backed scorer. The next race reads the config
                automatically — no restart needed.
              </p>
            )}
            {/* `current` is the standing config: while it is not `eval` the
                offered direction is → eval; once switched, the control flips
                to the quiet revert — the visible state change that makes the
                click legible. */}
            <ArenaScorerSwitch
              client={client}
              topic={topic}
              vault={vault}
              current={configuredScorer}
              testId="heal-arena-scorer"
              onSwitched={(config) => {
                setConfiguredScorer(config.arena_scorer ?? null);
                setArenaReloads((count) => count + 1);
                return onStatusRefresh?.();
              }}
            />
            <p class="muted">
              The button writes <code>{'arena_scorer = "eval"'}</code> under{" "}
              <code>[loop]</code> in{" "}
              <code>~/.config/knotica/config.toml</code> — the same edit by
              hand does the same thing.
            </p>
            <p class="muted">
              Prerequisites: a frozen golden set (Instrument → Freeze) and the{" "}
              <code>evals</code> extra (<code>uv sync --extra evals</code>).
              The eval scorer bills one golden-set eval per variant. Without
              the prerequisites the runner falls back to the heuristic and the
              race aborts again rather than scoring on the wrong instrument.
            </p>
          </>
        </SectionCard>
      ) : null}

      <SectionCard
        title="COMPILE"
        icon="refresh"
        footer={
          <>
            {/* Sibling of the button, never a child: the accessible name
                stays `Compile now` and the armed→confirm contract is
                untouched. The brief carries the chip plus the two answers the
                chip alone cannot give -- why now, and what it writes. */}
            <ProcessBrief process="improve.compile_run" term="why re-optimise" />
            <ArmedButton
              armed={armed}
              busy={busy}
              disabled={!client}
              label="Compile now"
              armedLabel="Confirm compile — bills"
              busyLabel="Compiling…"
              className="heal-freeze-primary"
              testId="heal-compile-run"
              onArm={() => setArmed(true)}
              onConfirm={() => void runCompile()}
              onCancel={() => setArmed(false)}
            />
          </>
        }
      >
        <>
          <p class="muted">
            A fresh compile re-optimises the prompt program against the trainset
            and writes a new candidate branch. It is billed, and the first click
            only arms the control.
          </p>
          {compiled ? <ProcessOutcome process="improve.compile_run" /> : null}
        </>
      </SectionCard>
    </div>
  );
}

/**
 * The race's own stage word, with the explanation of what it means. The word
 * comes from the payload verbatim — the arena names its own stages and this
 * stage never re-labels them.
 */
function stageChip(arenaStatus: ArenaStatus | null): JSX.Element {
  if (!arenaStatus) return <span class="chip">reading…</span>;
  return (
    <span class="chip">
      <TermHint
        id="heal-arena-stage"
        term={arenaStatus.stage}
        title="Arena stage"
        body="Where the variant race is. Aborted means the race stopped before ranking anything — the message below says why. It is not a failed compile."
        align="end"
      />
    </span>
  );
}

/**
 * Per-variant icon and tone. The state *word* is always the payload's own
 * `status` — the icon and the colour are redundancy on top of it, never the
 * only carrier of the verdict.
 */
const VARIANT_PRESENTATION: Record<string, { icon: IconName; tone: SectionTone }> = {
  pending: { icon: "state:pending", tone: "neutral" },
  scored: { icon: "state:running", tone: "neutral" },
  winner: { icon: "state:complete", tone: "good" },
  lost: { icon: "state:blocked", tone: "bad" },
};

/** The SCORER stat's value: the id, with the question count when recorded. */
function scorerValue(arenaStatus: ArenaStatus | null): string | null {
  if (!arenaStatus?.scorer_id) return null;
  return arenaStatus.n_examples != null
    ? `${arenaStatus.scorer_id} · ${arenaStatus.n_examples} q`
    : arenaStatus.scorer_id;
}

/**
 * What this variant tried, derived from the server's diff-against-base
 * summary (`core/arena.py::_variant_change_fields`) — never the variant's
 * own self-description. `null` on races recorded before change tracking
 * existed (including a race in flight when this shipped): said plainly
 * rather than rendered as an empty line.
 */
function variantChangeLead(variant: ArenaVariant): string {
  if (variant.change_summary != null) {
    return `Tries: ${variant.change_summary}.`;
  }
  return (
    "Recorded before change tracking — what this variant tried was not " +
    "kept; the next race will record it."
  );
}

/**
 * Each variant's overlay leads with what it tried, then carries the
 * scalar's provenance — the wire ships `scorer_id`/`n_examples` per variant
 * precisely so a bare number stays interpretable — followed by what a
 * variant is at all.
 */
function variantHintBody(variant: ArenaVariant): string {
  const provenance =
    variant.scalar == null
      ? variant.status === "pending"
        ? "Not scored yet — the race stopped (or has not reached it)."
        : "No scalar recorded."
      : `Scored ${variant.scalar.toFixed(4)} by ${variant.scorer_id ?? "an unrecorded scorer"}${
          variant.n_examples != null
            ? ` over ${variant.n_examples} golden question${variant.n_examples === 1 ? "" : "s"}`
            : ""
        }.`;
  return (
    `${variantChangeLead(variant)} ${provenance} ` +
    "The arena races variants against each other and keeps one only if it " +
    "clears the gate baseline — no model weights are ever touched."
  );
}

function variantRow(
  variant: ArenaVariant,
  openDiffId: string | null,
  onToggleDiff: (variantId: string) => void,
): StateListRow {
  const presentation = VARIANT_PRESENTATION[variant.status] ?? {
    icon: "state:unknown" as IconName,
    tone: "neutral" as SectionTone,
  };
  return {
    id: variant.id,
    state: variant.status,
    icon: presentation.icon,
    name: (
      <TermHint
        id={`heal-variant-${variant.id}`}
        term={variant.label}
        title="Prompt variant"
        body={variantHintBody(variant)}
      />
    ),
    stateLabel: variant.status,
    tone: presentation.tone,
    // A variant with no scalar yet is an absence, not an ellipsis that reads
    // as "still loading" — `StateList` renders it as the neutral `—`.
    value: variant.scalar == null ? null : variant.scalar.toFixed(4),
    action: variant.diff ? (
      <VariantDiffToggle
        label={variant.label}
        diff={variant.diff}
        open={openDiffId === variant.id}
        onToggle={() => onToggleDiff(variant.id)}
      />
    ) : undefined,
  };
}

/**
 * A quiet row action toggling this variant's diff against the base prompt —
 * the wire already ships the capped unified diff, so opening the panel costs
 * no call. Reuses `PromptDiff`'s panel classes (`.prompt-diff`,
 * `.prompt-diff-toggle`, `.prompt-diff-panel`) for visual consistency and so
 * the shared `state-list-row:has(.state-list-action .prompt-diff-panel)`
 * full-width rule applies unmodified; the raw text has no line-by-line
 * add/del structure to grid, so it renders as a plain monospace block rather
 * than `PromptDiff`'s hunk grid.
 */
function VariantDiffToggle({
  label,
  diff,
  open,
  onToggle,
}: {
  label: string;
  diff: string;
  open: boolean;
  onToggle: () => void;
}): JSX.Element {
  return (
    <div class="prompt-diff">
      <button
        type="button"
        class="ghost prompt-diff-toggle"
        aria-expanded={open}
        aria-label={`${open ? "Hide" : "Show"} ${label}'s diff`}
        onClick={onToggle}
      >
        {open ? "▾ diff" : "▸ diff"}
      </button>
      {open ? (
        <div class="prompt-diff-panel" aria-label={`${label}'s diff`}>
          <pre class="variant-diff-body">{diff}</pre>
        </div>
      ) : null}
    </div>
  );
}
