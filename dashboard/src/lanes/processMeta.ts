import type {
  ProcessAnchor,
  ProcessId,
  ProcessMeta,
  ProcessNext,
} from "./processContract";
import { ANSWER_PROCESSES } from "./processRows/answer";
import { FILL_PROCESSES } from "./processRows/fill";
import { IMPROVE_PROCESSES } from "./processRows/improve";
import { LEARN_PROCESSES } from "./processRows/learn";
import { TEND_PROCESSES } from "./processRows/tend";
import { VAULT_PROCESSES } from "./processRows/vault";

/**
 * The registry: the six-phase lifecycle contract, engraved as data.
 *
 * The contract itself -- what the six phases are, what each mode word means,
 * and why `next` has no null member -- lives in `processContract.ts`, which is
 * also where `ProcessId` declares the inventory. This module is the assembly,
 * and `processRows/` holds the rows one id-namespace per file.
 *
 * **Why the rows are split.** The registry crossed the project's 800-line
 * module ceiling as the last migration waves landed, and the exemption list
 * that ceiling is enforced with is closed by design -- so the answer is a
 * split, and the id namespace is the honest axis to split on: rows change
 * together per lane, which is exactly how every wave was scoped. Each row
 * module is keyed by `Extract<ProcessId, "<lane>.*">`, so the `Record` stays
 * exhaustive over its own namespace and the sum below stays exhaustive over
 * `ProcessId` -- a new id is a compile error in precisely one file, and the
 * completeness the census depends on is a type-system property rather than a
 * convention.
 *
 * Migration order was deliberate: the best-served processes migrated first,
 * so the schema was validated against a known-good before it was asked to
 * fill a void. `improve.gate_candidate` seeded it -- it already had a
 * server-quoted preview, a verdict outcome with an honest fallback, and it is
 * the canonical `conditional` Next, so the hardest shape was proven on row
 * one rather than on row thirty.
 */
export const PROCESS_META: Record<ProcessId, ProcessMeta> = {
  ...IMPROVE_PROCESSES,
  ...ANSWER_PROCESSES,
  ...FILL_PROCESSES,
  ...LEARN_PROCESSES,
  ...TEND_PROCESSES,
  ...VAULT_PROCESSES,
};

export type {
  Dispatch,
  OutcomeMode,
  PreviewMode,
  ProcessAnchor,
  ProcessBranch,
  ProcessId,
  ProcessMeta,
  ProcessNext,
  ProgressMode,
  Spend,
} from "./processContract";

/**
 * Resolve a `conditional` next against a discriminant the caller already
 * holds. An unknown or absent discriminant lands on the fallback rather than
 * on nothing: naming a destination is the contract, and a dead end is the
 * failure mode it exists to kill.
 */
export function resolveNextAnchor(
  next: ProcessNext,
  discriminant?: string | null,
): ProcessAnchor | null {
  if (next.kind === "terminal") return null;
  if (next.kind === "always") return next.go;
  const branch = next.branches.find((candidate) => candidate.when === discriminant);
  return branch ? branch.go : next.fallback;
}
