/**
 * Shared internals of the tool-client family: the contract a per-lane method
 * group is written against, the deadline the billed calls use, and the
 * installer that composes the groups onto one prototype.
 *
 * This is a *third* module rather than a section of `toolClient.ts` so the
 * dependency runs strictly one way — `lanes/<lane>/client.ts` imports this,
 * and `toolClient.ts` imports this plus the lane modules. Keeping
 * `LLM_CALL_TIMEOUT_MS` in `toolClient.ts` instead would make every lane
 * module import the very file that imports it, and unlike the type barrel
 * (whose edges are `import type` and erase before any bundler sees them)
 * that cycle would carry a runtime value.
 */

/**
 * What a per-lane method group may assume about `this`.
 *
 * `call` is `protected` on the client class; a method group is never assigned
 * to that class's type, so it sees the transport through this narrower
 * structural contract instead. The group knows the transport exists and
 * nothing else about it.
 */
export interface ToolCaller {
  call<T>(
    name: string,
    args: Record<string, unknown>,
    timeoutMs?: number,
  ): Promise<T>;
}

/**
 * A group of `ToolClient` methods, typed by the interface it satisfies and
 * bound to `ToolCaller` as `this`.
 *
 * The annotation is what keeps the group honest: a method declared in `T` but
 * missing from the literal, or present in the literal but undeclared, is a
 * compile error at the lane module itself rather than a surprise at a call
 * site three directories away.
 */
export type ToolCallGroup<T> = T & ThisType<ToolCaller>;

/**
 * Deadline for the calls that drive a server-side LLM: the billed dispatchers
 * (`compile action=run`, `datasets action=bootstrap|bootstrap_train`,
 * `loop action=run_eval|run_once`, `gapfill_discover`) and `query`.
 *
 * The MCP SDK defaults every request to 60 s, which no real eval finishes inside
 * — a golden set is many answer-plus-judge round trips, and a throttled one adds
 * retry backoff on top. Past the deadline the client aborts, the browser drops
 * the connection, and the server logs a bare `ClientDisconnect` while the run it
 * already billed for keeps going, invisibly. Bounded rather than infinite, so a
 * genuinely wedged call still fails instead of hanging the pane forever.
 *
 * `resetTimeoutOnProgress` is deliberately not used instead: the server sends no
 * MCP progress notifications (it writes progress to disk for the UI to poll), so
 * there is nothing on this channel for it to reset against.
 */
export const LLM_CALL_TIMEOUT_MS = 15 * 60 * 1000;

/**
 * Install method groups onto a class prototype, one flat method set.
 *
 * `Object.defineProperty` rather than `Object.assign`: methods declared in a
 * `class` body are non-enumerable, and an assigned property is not, so
 * assignment would quietly make every moved method show up in `for...in` and
 * `Object.keys` over an instance. The properties are otherwise ordinary —
 * writable and configurable — so a test may still spy on or replace one.
 *
 * A name already on the prototype throws rather than overwriting. This is the
 * one seam in the composition the type system cannot check: two groups that
 * declare the same method with the same signature merge silently at the type
 * level, and the later install would just win at runtime — the exact
 * right-name-wrong-lane failure the split is most exposed to. Module-load
 * time, so a collision fails the first import rather than the call.
 */
export function installToolCallGroups(
  prototype: object,
  ...groups: readonly object[]
): void {
  for (const group of groups) {
    for (const [name, method] of Object.entries(group)) {
      if (Object.hasOwn(prototype, name)) {
        throw new Error(
          `Tool-call group collision: two groups (or a group and the client ` +
            `class itself) both declare "${name}".`,
        );
      }
      Object.defineProperty(prototype, name, {
        value: method,
        writable: true,
        enumerable: false,
        configurable: true,
      });
    }
  }
}
