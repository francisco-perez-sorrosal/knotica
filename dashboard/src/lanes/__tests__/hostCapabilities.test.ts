import { beforeAll, describe, expect, it } from "vitest";

/**
 * Pure tier derivation from host capabilities, pinned from
 * `INTERFACE_DESIGN.md §3.4`'s dispatch table before the module exists.
 *
 * `deriveDispatchTier` turns a mount kind (`"http"` | `"bridge"`) plus
 * whatever capability object the host actually advertised into one of the
 * four dispatch tiers `"A" | "B" | "C" | "D"` — dispatch is progressive
 * enhancement down to the copyable-text floor (`dec-091`), and every field
 * on the real `McpUiHostCapabilities` the host sends is optional, so the
 * function must never assume a field, or the object itself, is present.
 *
 * The module under test does not exist yet — this is the RED half of a
 * paired step. A literal `import { ... } from "../hostCapabilities"` would
 * fail `tsc --noEmit` for the whole project the moment this file is added,
 * not just this suite, so the specifier is loaded through a non-literal
 * binding below: TypeScript does not resolve a dynamic `import()` whose
 * argument isn't a string literal, so the rest of the tree keeps
 * type-checking while this file still fails at runtime with the
 * missing-module error the paired implementation step is gated on. The
 * type below is this suite's own mirror of the expected surface, not an
 * import of the real one — once the module lands, sibling files that
 * `import type` it directly are what actually proves its exports exist.
 */

type DispatchTier = "A" | "B" | "C" | "D";
type Mount = "http" | "bridge";

// Mirrors only the fields `deriveDispatchTier` reads from the real
// `McpUiHostCapabilities` (both optional there, per the spec) — minimal to
// the behavior under test, not a full re-declaration of the host's capability
// surface.
interface HostCapabilities {
  message?: unknown;
  updateModelContext?: unknown;
}

interface HostCapabilitiesModule {
  deriveDispatchTier(
    caps: HostCapabilities | undefined,
    mount: Mount,
  ): DispatchTier;
}

const HOST_CAPABILITIES_MODULE_PATH = "../hostCapabilities";

let hostCapabilities: HostCapabilitiesModule;

beforeAll(async () => {
  hostCapabilities = (await import(
    HOST_CAPABILITIES_MODULE_PATH
  )) as HostCapabilitiesModule;
});

describe("deriveDispatchTier — the four dispatch tiers of INTERFACE_DESIGN.md §3.4", () => {
  it("resolves tier A when the bridge host advertises `message` — a turn can happen", () => {
    const result = hostCapabilities.deriveDispatchTier(
      { message: {} },
      "bridge",
    );

    expect(result).toBe("A");
  });

  it("resolves tier B when the bridge host advertises `updateModelContext` but not `message`", () => {
    const result = hostCapabilities.deriveDispatchTier(
      { updateModelContext: {} },
      "bridge",
    );

    expect(result).toBe("B");
  });

  it("resolves tier C when the bridge host advertises neither `message` nor `updateModelContext`", () => {
    const result = hostCapabilities.deriveDispatchTier(
      { message: undefined, updateModelContext: undefined },
      "bridge",
    );

    expect(result).toBe("C");
  });

  it("resolves tier D on the HTTP mount — no host, so no programmatic dispatch is possible", () => {
    const result = hostCapabilities.deriveDispatchTier({}, "http");

    expect(result).toBe("D");
  });

  it("prefers `message` over `updateModelContext` when a bridge host advertises both — A outranks B", () => {
    const result = hostCapabilities.deriveDispatchTier(
      { message: {}, updateModelContext: {} },
      "bridge",
    );

    expect(result).toBe("A");
  });
});

describe("deriveDispatchTier — edge cases the table implies but doesn't spell out", () => {
  it("resolves D on the HTTP mount even when the capabilities object is non-empty — HTTP has no host to advertise from, so the mount alone decides", () => {
    const result = hostCapabilities.deriveDispatchTier(
      { message: {}, updateModelContext: {} },
      "http",
    );

    expect(result).toBe("D");
  });

  it("resolves C on the bridge mount when no capabilities were advertised at all", () => {
    const result = hostCapabilities.deriveDispatchTier({}, "bridge");

    expect(result).toBe("C");
  });
});

describe("deriveDispatchTier — absence tolerance (every McpUiHostCapabilities field is optional)", () => {
  it("does not throw and resolves C when the bridge host object itself is `undefined`", () => {
    expect(() =>
      hostCapabilities.deriveDispatchTier(undefined, "bridge"),
    ).not.toThrow();
    expect(hostCapabilities.deriveDispatchTier(undefined, "bridge")).toBe("C");
  });

  it("does not throw and resolves D when the HTTP mount's host object is `undefined`", () => {
    expect(() =>
      hostCapabilities.deriveDispatchTier(undefined, "http"),
    ).not.toThrow();
    expect(hostCapabilities.deriveDispatchTier(undefined, "http")).toBe("D");
  });
});

describe("deriveDispatchTier — stable, pure ordering across all mount/capability combinations", () => {
  it.each<[string, HostCapabilities | undefined, Mount, DispatchTier]>([
    ["message only", { message: {} }, "bridge", "A"],
    [
      "message + updateModelContext",
      { message: {}, updateModelContext: {} },
      "bridge",
      "A",
    ],
    ["updateModelContext only", { updateModelContext: {} }, "bridge", "B"],
    ["neither", {}, "bridge", "C"],
    ["undefined caps", undefined, "bridge", "C"],
    ["http, empty caps", {}, "http", "D"],
    ["http, full caps", { message: {}, updateModelContext: {} }, "http", "D"],
    ["http, undefined caps", undefined, "http", "D"],
  ])(
    "resolves tier %s -> %s deterministically for mount=%s",
    (_label, caps, mount, expected) => {
      const result = hostCapabilities.deriveDispatchTier(caps, mount);

      expect(result).toBe(expected);
    },
  );

  it("is a pure function of its own arguments — the same inputs always derive the same tier", () => {
    const caps: HostCapabilities = { updateModelContext: {} };

    const first = hostCapabilities.deriveDispatchTier(caps, "bridge");
    const second = hostCapabilities.deriveDispatchTier(caps, "bridge");

    expect(second).toBe(first);
  });

  it("never mutates the capabilities object it was given", () => {
    const caps: HostCapabilities = { message: {} };
    const snapshot = JSON.stringify(caps);

    hostCapabilities.deriveDispatchTier(caps, "bridge");

    expect(JSON.stringify(caps)).toBe(snapshot);
  });

  it("carries no memory between calls — a tier-D call sandwiched between tier-A calls changes neither", () => {
    const capsWithMessage: HostCapabilities = { message: {} };

    const before = hostCapabilities.deriveDispatchTier(
      capsWithMessage,
      "bridge",
    );
    hostCapabilities.deriveDispatchTier({}, "http");
    const after = hostCapabilities.deriveDispatchTier(
      capsWithMessage,
      "bridge",
    );

    expect(before).toBe("A");
    expect(after).toBe("A");
  });
});
