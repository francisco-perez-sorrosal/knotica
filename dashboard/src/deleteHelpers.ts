import type { BranchDeleteResult } from "./types";

export function formatDeletePreview(result: BranchDeleteResult): string {
  return result.message;
}

export function formatDeleteApplied(result: BranchDeleteResult): string {
  return result.message;
}
