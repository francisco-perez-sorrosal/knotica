import type { App } from "@modelcontextprotocol/ext-apps";

import { BridgeToolClient } from "../../toolClient";

/**
 * The `ToolClient` prototype walk, shared by the two censuses that are closed
 * over the client surface: `toolNameRegistryCensus.test.ts` (every tool *name*
 * the client reaches the transport with is registered) and
 * `lanes/__tests__/processMeta.test.ts` (every mutating client *method* has
 * lifecycle copy).
 *
 * It lives here rather than in either suite because both need the same
 * answer to the same question — "what is the client's method surface, right
 * now?" — and two copies of a walk is exactly how the second one goes stale.
 *
 * The walk resolves the surface at runtime rather than by reading source, so
 * the six declaration-merged lane groups installed onto the prototype at
 * module load are counted for real.
 */

/** Methods every mount implements differently — not tool calls. */
export const TRANSPORT_METHODS: readonly string[] = [
  "call",
  "sendMessage",
  "updateModelContext",
  "close",
];

/** Every own, function-valued property along `instance`'s prototype chain. */
export function collectPrototypeMethodNames(instance: object): string[] {
  const names = new Set<string>();
  let proto: object | null = Object.getPrototypeOf(instance);
  while (proto && proto !== Object.prototype) {
    for (const name of Object.getOwnPropertyNames(proto)) {
      if (name === "constructor") continue;
      const descriptor = Object.getOwnPropertyDescriptor(proto, name);
      if (typeof descriptor?.value === "function") names.add(name);
    }
    proto = Object.getPrototypeOf(proto);
  }
  return [...names];
}

/**
 * The tool-call method names on a live client — the prototype walk minus the
 * transport surface. This is the set the registry census partitions.
 */
export function toolCallMethodNames(): string[] {
  const app = {
    async callServerTool() {
      return { structuredContent: {} };
    },
  } as unknown as App;
  return collectPrototypeMethodNames(new BridgeToolClient(app)).filter(
    (name) => !TRANSPORT_METHODS.includes(name),
  );
}
