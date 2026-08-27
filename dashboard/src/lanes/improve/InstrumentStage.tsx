import type { JSX } from "preact";
import { useCallback, useEffect, useState } from "preact/hooks";

import { ArmedButton } from "../ArmedButton";
import type { ToolClient } from "../../toolClient";
import type { DatasetsInventory } from "../../types";

/**
 * `instrument` stage body (`INTERFACE_DESIGN.md §2.4`). Absorbs
 * `DatasetsPane`'s inventory/bootstrap/bootstrap_train/freeze plus
 * `VaultPane`'s "Bootstrap trainset" action into one stage. `Freeze golden`
 * is the one primary control (§2.4's one-primary-control rule) and stays at
 * the top level, outside the single `▸` disclosure that holds everything
 * else.
 *
 * All three spend-immediately billed actions (`Bootstrap`, `Bootstrap
 * trainset`, `Freeze golden`) gate on the shared `ArmedButton` two-click
 * armed→confirm affordance before the billing call fires — the first click
 * arms the control (relabelling it to "Confirm … — bills", with a `Cancel`
 * ghost button to back out), and only the second, explicit click spends. Per
 * the orchestrator's no-native-dialogs ruling (`LEARNINGS.md`): a sandboxed
 * MCP-App iframe has no `allow-modals`, so `window.confirm()` can be silently
 * suppressed and return `false`, bricking the action on Claude Desktop. None
 * of the three mints a server-side `confirm_nonce` (unlike `observe`'s
 * `run_eval`), so this is the honest client-side mirror of "explicit
 * confirmation" for all of them. `Freeze golden` previously kept
 * `window.confirm()` as pre-existing `DatasetsPane` behavior; the ruling is
 * dashboard-wide, not billed-actions-only, so it is converted here too
 * (`LEARNINGS.md`, "Carried to the ImproveLane assembly step") — a suppressed
 * native confirm silently returns `false` in the sandboxed MCP-App mount,
 * making Freeze un-triggerable on Claude Desktop regardless of whether it was
 * "new" spend surface. The reviewed-set-below-floor warning `window.confirm`
 * used to fold into its dialog text now renders as inline copy instead, since
 * there is no dialog left to carry it.
 */

type InstrumentToolClient = Pick<
  ToolClient,
  | "datasetsInventory"
  | "datasetsBootstrap"
  | "datasetsBootstrapTrain"
  | "datasetsFreeze"
>;

type Busy = "inventory" | "bootstrap" | "bootstrap-train" | "freeze" | null;

export function InstrumentStage({
  client,
  topic,
  vault,
}: {
  client: InstrumentToolClient | null;
  topic: string;
  vault: string;
}): JSX.Element {
  const [inventory, setInventory] = useState<DatasetsInventory | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState<Busy>(null);
  const [expanded, setExpanded] = useState(false);
  const [armedBootstrap, setArmedBootstrap] = useState(false);
  const [armedBootstrapTrain, setArmedBootstrapTrain] = useState(false);
  const [armedFreeze, setArmedFreeze] = useState(false);

  const reloadInventory = useCallback(async () => {
    if (!client) return;
    setBusy("inventory");
    setError(null);
    try {
      setInventory(await client.datasetsInventory(topic, vault));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  }, [client, topic, vault]);

  useEffect(() => {
    void reloadInventory();
  }, [reloadInventory]);

  const pipeline = inventory?.pipeline ?? null;
  const floor = inventory?.floor ?? 20;
  const reviewedN = pipeline?.reviewed_n ?? 0;
  // Freeze refuses a reviewed set that overlaps the trainset outright — a
  // held-out question the model was trained on measures nothing.
  const freezeBlocked = (inventory?.overlaps.train_reviewed ?? 0) > 0;
  const belowFloor = reviewedN > 0 && reviewedN < floor;
  const freezeReady = reviewedN > 0 && !freezeBlocked;
  const trainsetRow =
    inventory?.files.find((row) => row.role === "trainset") ?? null;

  async function runBootstrap() {
    if (!client) return;
    setBusy("bootstrap");
    setError(null);
    setNote(null);
    try {
      const result = await client.datasetsBootstrap(topic, vault);
      setNote(
        `Bootstrapped ${result.n_candidates} candidates → ${result.filename}`,
      );
      await reloadInventory();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
      setArmedBootstrap(false);
    }
  }

  async function runBootstrapTrain() {
    if (!client) return;
    setBusy("bootstrap-train");
    setError(null);
    setNote(null);
    try {
      const result = await client.datasetsBootstrapTrain(
        topic,
        undefined,
        vault,
      );
      setNote(
        `Seeded ${result.appended} examples from ${result.pages_read} pages`,
      );
      await reloadInventory();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
      setArmedBootstrapTrain(false);
    }
  }

  async function runFreeze() {
    if (!client) return;
    setBusy("freeze");
    setError(null);
    try {
      const result = await client.datasetsFreeze(topic, vault);
      setNote(
        `Frozen ${result.n_frozen} into held-out` +
          (result.below_floor ? ` (below the recommended ${floor})` : "") +
          ` · ${result.commit_sha.slice(0, 8)}`,
      );
      await reloadInventory();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
      setArmedFreeze(false);
    }
  }

  if (!client) {
    return (
      <section class="pane-main instrument-stage">
        <p class="muted">Connect MCP to inspect datasets.</p>
      </section>
    );
  }

  return (
    <section class="pane-main instrument-stage" aria-label="Instrument">
      <header class="instrument-toolbar">
        <div class="instrument-facts">
          <span>
            Held-out: <strong>{pipeline?.held_out_n ?? 0}</strong>
          </span>
          {trainsetRow ? (
            <span>
              Trainset: <strong>{trainsetRow.count}</strong>
            </span>
          ) : null}
        </div>
        <div class="instrument-actions">
          <ArmedButton
            armed={armedBootstrap}
            busy={busy === "bootstrap"}
            disabled={busy !== null && busy !== "bootstrap"}
            label="Bootstrap"
            armedLabel="Confirm bootstrap — bills"
            busyLabel="Bootstrapping…"
            title="LLM synthesize candidates from entity pages"
            onArm={() => setArmedBootstrap(true)}
            onConfirm={() => void runBootstrap()}
            onCancel={() => setArmedBootstrap(false)}
          />
          <ArmedButton
            armed={armedBootstrapTrain}
            busy={busy === "bootstrap-train"}
            disabled={busy !== null && busy !== "bootstrap-train"}
            label="Bootstrap trainset"
            armedLabel="Confirm bootstrap trainset — bills"
            busyLabel="Bootstrapping trainset…"
            title="LLM crawl and label pages into the trainset"
            onArm={() => setArmedBootstrapTrain(true)}
            onConfirm={() => void runBootstrapTrain()}
            onCancel={() => setArmedBootstrapTrain(false)}
          />
          <ArmedButton
            armed={armedFreeze}
            busy={busy === "freeze"}
            disabled={(busy !== null && busy !== "freeze") || !freezeReady}
            label="Freeze golden"
            armedLabel="Confirm freeze — writes files"
            busyLabel="Freezing…"
            className="primary"
            title={
              freezeBlocked
                ? "Freeze refuses a Reviewed set that overlaps the trainset — clear the overlap first"
                : reviewedN === 0
                  ? "No reviewed candidates to freeze"
                  : "Promote Reviewed → held-out golden.jsonl"
            }
            onArm={() => setArmedFreeze(true)}
            onConfirm={() => void runFreeze()}
            onCancel={() => setArmedFreeze(false)}
          />
        </div>
      </header>

      {belowFloor ? (
        <p class="muted freeze-shortfall-warning">
          Only {reviewedN} reviewed candidate{reviewedN === 1 ? "" : "s"} —
          below the recommended {floor}. The eval scalar will be noisier until
          the set grows.
        </p>
      ) : null}

      {note ? <p class="saved-note">{note}</p> : null}
      {error ? <aside role="alert">{error}</aside> : null}

      <button
        type="button"
        class="instrument-disclosure"
        aria-expanded={expanded}
        onClick={() => setExpanded((wasExpanded) => !wasExpanded)}
      >
        <span aria-hidden="true">▸</span>{" "}
        {expanded ? "Hide details" : "Show details"}
      </button>

      {expanded && inventory ? (
        <div class="instrument-details">
          <p class="muted">
            Candidates {inventory.pipeline.candidates_n} · Reviewed{" "}
            {inventory.pipeline.reviewed_n} · Held-out{" "}
            {inventory.pipeline.held_out_n}
          </p>
          <p class="muted">
            Overlap: {inventory.overlaps.train_held_out} train∩held-out,{" "}
            {inventory.overlaps.train_reviewed} train∩reviewed,{" "}
            {inventory.overlaps.train_candidates} train∩candidates
          </p>
          <table class="instrument-files">
            <thead>
              <tr>
                <th>Role</th>
                <th>Count</th>
                <th>Ready</th>
              </tr>
            </thead>
            <tbody>
              {inventory.files.map((row) => (
                <tr key={row.role}>
                  <td>{row.label}</td>
                  <td>{row.exists ? row.count : "—"}</td>
                  <td>
                    {row.ready ? "ready" : row.exists ? "not ready" : "missing"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
