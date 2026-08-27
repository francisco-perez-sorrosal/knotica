// Pure visibility-aware polling primitive (dec-092 budget rule 3: poll at a
// fixed cadence and pause while the tab is hidden).
//
// Framework-free: no Preact import, no fetch. The first
// `document.visibilitychange`-aware primitive in the dashboard — no existing
// pattern to extend. `doc`/`win` are explicit parameters (not read from the
// globals directly) so callers — and tests — can inject fakes; this is also
// why the module is trivially testable under `.test.ts`'s Node environment,
// which has no real `document`/`window`.

/**
 * Starts polling `callback` every `intervalMs` while `doc` is visible.
 *
 * The underlying interval keeps running even while hidden — cheaper and
 * simpler than tearing it down and recreating it — but `callback` is skipped
 * on any tick where `doc.hidden` is true. On the hidden-to-visible edge
 * (detected via `visibilitychange`), fires `callback` once immediately, with
 * no catch-up burst for ticks missed while hidden, then resumes the normal
 * cadence.
 *
 * Returns a teardown function that clears the interval and removes the
 * `visibilitychange` listener.
 */
export function startVisibilityPausedPoll(
  callback: () => void,
  intervalMs: number,
  doc: Document = document,
  win: Window = window,
): () => void {
  let wasHidden = doc.hidden;

  const intervalId = win.setInterval(() => {
    if (!doc.hidden) {
      callback();
    }
  }, intervalMs);

  function handleVisibilityChange(): void {
    const isHidden = doc.hidden;
    if (wasHidden && !isHidden) {
      callback();
    }
    wasHidden = isHidden;
  }

  doc.addEventListener("visibilitychange", handleVisibilityChange);

  return function stop(): void {
    win.clearInterval(intervalId);
    doc.removeEventListener("visibilitychange", handleVisibilityChange);
  };
}
