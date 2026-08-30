import type { JSX } from "preact";
import { useCallback, useEffect, useState } from "preact/hooks";

import { ArmedButton } from "../ArmedButton";
import { Icon } from "../../icons";
import { SectionCard } from "../../SectionCard";
import { Stat, StatGrid } from "../../Stat";
import { TermHint } from "../../TermHint";
import type { ToolClient } from "../../toolClient";
import type { DatasetFileRow, DatasetsInventory } from "../../types";

/**
 * `instrument` stage body. Absorbs `DatasetsPane`'s
 * inventory/bootstrap/bootstrap_train/freeze plus `VaultPane`'s "Bootstrap
 * trainset" action into one stage.
 *
 * The body is three `SectionCard`s, and each control sits in the footer of
 * the card holding the numbers it changes: `Bootstrap` with the candidate
 * count it creates, `Freeze golden` with the reviewed → held-out counts it
 * moves, `Bootstrap trainset` with the trainset it appends to. `Freeze
 * golden` stays a top-level control, outside any `aria-expanded`; the
 * per-role breakdown is the only thing behind the single disclosure.
 *
 * That breakdown is grouped, not flat. A five-row Role/Count/Ready table
 * answered neither of the two questions a reader actually has — what each
 * role *is*, and which role is made out of which — so the rows are split
 * into the families the wire already declares (`row.group`), ordered as the
 * pipeline runs rather than as the payload happens to list them, and every
 * role name carries the wire's own `purpose` string as its `TermHint` body.
 * The client never restates that copy: one source of truth, server-side.
 *
 * No control carries a `title=` any more. A tooltip is invisible on touch,
 * needs a hover dwell, and is unreachable by keyboard — so the three that
 * explained what a button does became their card's visible explanation
 * line, and Freeze's disabled reason became a visible
 * `.section-card-note`, which is the one place it actually needs to be
 * readable.
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
      <SectionCard
        title="PIPELINE"
        icon="stage:instrument"
        headerActions={sealChip(inventory)}
        footer={
          <>
            <span class="chip cost">billed</span>
            <ArmedButton
              armed={armedBootstrap}
              busy={busy === "bootstrap"}
              disabled={busy !== null && busy !== "bootstrap"}
              label="Bootstrap"
              armedLabel="Confirm bootstrap — bills"
              busyLabel="Bootstrapping…"
              onArm={() => setArmedBootstrap(true)}
              onConfirm={() => void runBootstrap()}
              onCancel={() => setArmedBootstrap(false)}
            />
            <span class="chip" data-tone="warn">
              writes files
            </span>
            <ArmedButton
              armed={armedFreeze}
              busy={busy === "freeze"}
              disabled={(busy !== null && busy !== "freeze") || !freezeReady}
              label="Freeze golden"
              armedLabel="Confirm freeze — writes files"
              busyLabel="Freezing…"
              className="primary"
              onArm={() => setArmedFreeze(true)}
              onConfirm={() => void runFreeze()}
              onCancel={() => setArmedFreeze(false)}
            />
            {freezeReady ? null : (
              <p class="section-card-note">
                {freezeBlocked
                  ? "Freeze refuses a Reviewed set that overlaps the trainset — clear the overlap first."
                  : "No reviewed candidates to freeze yet."}
              </p>
            )}
          </>
        }
      >
        <>
          <StatGrid>
            <Stat label={hint("candidates")} value={pipeline?.candidates_n} />
            <Stat label={hint("reviewed")} value={pipeline?.reviewed_n} />
            <Stat label={hint("heldOut")} value={pipeline?.held_out_n} />
            <Stat label={hint("floor")} value={inventory?.floor} />
          </StatGrid>
          <p class="muted">
            Candidates are synthesised from this topic's entity pages, reviewed
            by you, then frozen into the held-out set the eval scores against.
          </p>
          {belowFloor ? (
            <p class="muted section-card-status freeze-shortfall-warning">
              <Icon name="info" size={16} />
              {`Only ${reviewedN} reviewed candidate${
                reviewedN === 1 ? "" : "s"
              } — below the recommended ${floor}. The eval scalar will be noisier until the set grows.`}
            </p>
          ) : null}
          {note ? (
            <p class="saved-note" role="status">
              {note}
            </p>
          ) : null}
          {error ? <aside role="alert">{error}</aside> : null}
        </>
      </SectionCard>

      <SectionCard
        title="TRAINSET"
        icon="lane:learn"
        footer={
          <>
            <span class="chip cost">billed</span>
            <ArmedButton
              armed={armedBootstrapTrain}
              busy={busy === "bootstrap-train"}
              disabled={busy !== null && busy !== "bootstrap-train"}
              label="Bootstrap trainset"
              armedLabel="Confirm bootstrap trainset — bills"
              busyLabel="Bootstrapping trainset…"
              onArm={() => setArmedBootstrapTrain(true)}
              onConfirm={() => void runBootstrapTrain()}
              onCancel={() => setArmedBootstrapTrain(false)}
            />
          </>
        }
      >
        <>
          <StatGrid>
            <Stat label={hint("trainset")} value={trainsetRow?.count} />
          </StatGrid>
          <p class="muted">
            Crawled and labelled pages the compiler optimises the prompt
            program against. Separate from held-out on purpose: a question the
            model trained on measures nothing.
          </p>
        </>
      </SectionCard>

      <SectionCard
        title="FILES & OVERLAPS"
        headerActions={
          <button
            type="button"
            class="instrument-disclosure"
            aria-expanded={expanded}
            onClick={() => setExpanded((wasExpanded) => !wasExpanded)}
          >
            <span aria-hidden="true">▸</span>{" "}
            {expanded ? "Hide details" : "Show details"}
          </button>
        }
      >
        <>
          <StatGrid>
            <Stat
              label={overlapHint("held-out")}
              value={inventory?.overlaps.train_held_out}
            />
            <Stat
              label={overlapHint("reviewed")}
              value={inventory?.overlaps.train_reviewed}
            />
            <Stat
              label={overlapHint("candidates")}
              value={inventory?.overlaps.train_candidates}
            />
          </StatGrid>
          {expanded && inventory ? datasetFamilies(inventory) : null}
        </>
      </SectionCard>
    </section>
  );
}

/** The seal verdict, as a word — never a colour or a bare number. */
function sealChip(inventory: DatasetsInventory | null): JSX.Element {
  if (!inventory) return <span class="chip">reading…</span>;
  const overlapping =
    inventory.overlaps.train_held_out +
    inventory.overlaps.train_reviewed +
    inventory.overlaps.train_candidates;
  return inventory.pipeline.seal_ok ? (
    <span class="chip" data-tone="good">
      seal clean
    </span>
  ) : (
    <span class="chip" data-tone="bad">{`${overlapping} overlapping`}</span>
  );
}

/**
 * Reading order inside a family — the order the pipeline runs in, which is
 * not the order `gather_datasets_inventory` lists the roles in. `seal` is
 * ranked so a payload that keeps it as its own row still places it sensibly.
 */
const ROLE_ORDER: readonly string[] = [
  "candidates",
  "reviewed",
  "held_out",
  "seal",
  "trainset",
];

/** Production before consumption, so the chain reads top to bottom. */
const FAMILY_ORDER: readonly string[] = ["golden_pipeline", "loop_corpora"];

/**
 * The roles that *continue* the candidates → reviewed → held-out chain. The
 * chain's origin carries no mark, and `trainset` carries none because it is
 * deliberately disjoint from the chain — which is the whole reason an
 * overlap above it means contamination.
 */
const FLOW_CONTINUATION: readonly string[] = ["reviewed", "held_out"];

/**
 * Stated once, under the family that owns the chain's origin — never
 * repeated per row. `held_out` and its seal belong to `loop_corpora` on the
 * wire even though `Freeze` is what produces them, so this sentence is what
 * carries the step across the family boundary.
 */
const FLOW_NOTE =
  "Bootstrap synthesises Candidates; you keep the good ones as Reviewed; " +
  "Freeze writes those into the held-out exam set and seals it. The trainset " +
  "is a separate corpus the compiler trains on — which is why any overlap " +
  "above means the exam is scoring the model on something it trained on.";

interface DatasetFamily {
  readonly key: string;
  readonly label: string;
  readonly rows: readonly DatasetFileRow[];
}

/**
 * The per-role breakdown, grouped into the wire's own families and drawn as
 * a chain. The seal is folded onto the held-out row it guards rather than
 * standing as a peer step, since nothing is ever produced *from* it.
 */
function datasetFamilies(inventory: DatasetsInventory): JSX.Element {
  const seal = inventory.files.find((row) => row.role === "seal") ?? null;
  const heldOut = inventory.files.find((row) => row.role === "held_out") ?? null;
  // A seal with no held-out row to guard stays a row of its own — losing it
  // silently would be worse than an odd-looking one.
  const guard = seal && heldOut ? seal : null;
  const listed = guard
    ? inventory.files.filter((row) => row !== guard)
    : inventory.files;

  return (
    <div class="dataset-families">
      {groupByFamily(listed).map((family) => (
        <section
          key={family.key}
          class="dataset-family"
          aria-label={family.label}
        >
          <p class="microlabel">{family.label}</p>
          <ul class="dataset-roles">
            {family.rows.map((row) =>
              datasetRoleRow(row, row === heldOut ? guard : null),
            )}
          </ul>
          {family.key === "golden_pipeline" ? (
            <p class="muted dataset-flow-note">{FLOW_NOTE}</p>
          ) : null}
        </section>
      ))}
    </div>
  );
}

/**
 * Buckets by the wire's `group`. An unrecognised group gets its own family
 * (labelled from the raw value) rather than being dropped — the wire type
 * admits any string, so a new server-side family must stay visible here
 * without a client release.
 */
function groupByFamily(files: readonly DatasetFileRow[]): DatasetFamily[] {
  const buckets = new Map<string, DatasetFileRow[]>();
  for (const row of files) {
    const key = row.group || "other";
    const bucket = buckets.get(key);
    if (bucket) bucket.push(row);
    else buckets.set(key, [row]);
  }
  // `Array.prototype.sort` is stable, so unranked families and roles keep
  // the order the payload sent them in.
  return [...buckets.keys()]
    .sort((a, b) => rank(FAMILY_ORDER, a) - rank(FAMILY_ORDER, b))
    .map((key) => ({
      key,
      label: key.replace(/_/g, " ").toUpperCase(),
      rows: [...(buckets.get(key) ?? [])].sort(
        (a, b) => rank(ROLE_ORDER, a.role) - rank(ROLE_ORDER, b.role),
      ),
    }));
}

function rank(order: readonly string[], value: string): number {
  const index = order.indexOf(value);
  return index === -1 ? order.length : index;
}

function datasetRoleRow(
  row: DatasetFileRow,
  guard: DatasetFileRow | null,
): JSX.Element {
  return (
    <li key={row.role} class="dataset-role">
      {/* Always rendered, glyph or not: the empty cell is what keeps every
          role name aligned on the same rail. */}
      <span class="dataset-flow-mark" aria-hidden="true">
        {FLOW_CONTINUATION.includes(row.role) ? "↳" : ""}
      </span>
      <span class="dataset-role-name">{roleTerm(row)}</span>
      <span class="dataset-role-count">{row.exists ? row.count : "—"}</span>
      {readyChip(row)}
      {guard ? (
        <span class="dataset-role-guard">
          {roleTerm(guard)}
          {readyChip(guard)}
        </span>
      ) : null}
    </li>
  );
}

function readyChip(row: DatasetFileRow): JSX.Element {
  return (
    <span
      class="chip"
      data-tone={row.ready ? "good" : row.exists ? "warn" : "neutral"}
    >
      {row.ready ? "ready" : row.exists ? "not ready" : "missing"}
    </span>
  );
}

/**
 * The role name, explained by the server's own `purpose` string. A payload
 * that carries no purpose falls back to the bare label — the client has no
 * business inventing an explanation the server declined to give.
 */
function roleTerm(row: DatasetFileRow): JSX.Element | string {
  if (!row.purpose) return row.label;
  return (
    <TermHint
      id={`instrument-file-${row.role}`}
      term={row.label}
      title={row.label}
      body={row.purpose}
    />
  );
}

/**
 * The explanatory copy behind each stat label's `TermHint` — the two
 * retired `title=` tooltips that explained what `Bootstrap` and `Bootstrap
 * trainset` do now live in their cards' visible explanation lines instead,
 * so nothing here duplicates them.
 */
const INSTRUMENT_HINTS = {
  candidates: {
    term: "CANDIDATES",
    title: "Candidates",
    body: "Question/answer pairs the model synthesised from this topic's pages. Nothing is measured against them until you review them.",
  },
  reviewed: {
    term: "REVIEWED",
    title: "Reviewed",
    body: "Candidates you kept. Freezing promotes these into the held-out set.",
  },
  heldOut: {
    term: "HELD-OUT",
    title: "Held-out",
    body: "The frozen golden set every eval scores against. Sealed: nothing the compiler trains on may appear here.",
  },
  floor: {
    term: "FLOOR",
    title: "Floor",
    body: "The smallest held-out set that gives a stable scalar. Below it the number still computes — it is just noisier, and the warning says so rather than blocking you.",
  },
  trainset: {
    term: "EXAMPLES",
    title: "Trainset",
    body: "The examples the compiler optimises the prompt program against. Overlap with held-out would make the eval meaningless, which is what the seal checks.",
  },
} as const;

const OVERLAP_HINT_BODY =
  "How many items appear in both sets. Anything above zero means the eval is scoring the model on something it trained on. Freeze refuses while train ∩ reviewed is non-zero.";

function hint(key: keyof typeof INSTRUMENT_HINTS): JSX.Element {
  return <TermHint id={`instrument-${key}`} {...INSTRUMENT_HINTS[key]} />;
}

function overlapHint(against: string): JSX.Element {
  return (
    <TermHint
      id={`instrument-overlap-${against}`}
      term={`TRAIN ∩ ${against.toUpperCase()}`}
      title="Overlap"
      body={OVERLAP_HINT_BODY}
    />
  );
}
