/**
 * Answer-lane wire shapes: the question-answering read contract.
 *
 * Re-exported verbatim from `src/types.ts`, so
 * `import type { X } from "../../types"` still resolves.
 */

export interface QueryAnswer {
  topic: string;
  question: string;
  answer: string;
  citations: string[];
  pages_used: string[];
}
