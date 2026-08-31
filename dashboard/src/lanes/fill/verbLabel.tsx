import type { JSX } from "preact";

import { Spinner } from "../../icons";

/**
 * The busy form of one Fill-lane verb, shared by every mutating confirm in the
 * lane (the four triage verbs in `SuggestionRow.tsx`, the gap dismiss in
 * `GapCard.tsx`).
 *
 * The control already carries `aria-busy`, but a row or card cannot say
 * *which* verb is running -- so the word is kept and the spinner is added
 * beside it, rather than the label being swapped for a bare ellipsis that
 * erases the button's accessible name exactly when a reader most needs it.
 */
export function verbLabel(running: boolean, label: string): JSX.Element {
  return running ? (
    <>
      <Spinner />
      {label}
    </>
  ) : (
    <>{label}</>
  );
}
