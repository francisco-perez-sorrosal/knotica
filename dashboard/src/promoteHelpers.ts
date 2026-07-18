import type { CompilePromoteResult } from "./types";

/** User-facing preview copy — never echo backend "dry-run only" phrasing. */
export function formatPromotePreview(result: CompilePromoteResult): string {
  const into = result.into ?? "default";
  const branch = result.branch;
  if (result.candidate_branch && result.candidate_branch !== branch) {
    return (
      `Preview: merge ${branch} (from ${result.candidate_branch}) into ${into}. ` +
      "Apply merge to update the live wiki."
    );
  }
  return (
    `Preview: merge ${branch} into ${into}. Apply merge to update the live wiki.`
  );
}

export function formatPromoteApplied(result: CompilePromoteResult): string {
  return result.message || `Merged ${result.branch} into ${result.into ?? "default"}.`;
}
