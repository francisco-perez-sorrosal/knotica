import { describe, expect, it, beforeAll } from "vitest";

import { PANE_BY_PARAM, resolvePane } from "../paneRouting";

/**
 * `learn`/`answer`/`fill` as reachable panes — mirrors
 * `paneRouting.improveTend.test.ts`'s own shape for the earlier
 * `improve`/`tend` pair exactly. Three things are pinned here:
 *
 * 1. `?pane=learn`, `?pane=answer`, `?pane=fill` each resolve to their own
 *    `PaneId`, self-mapped.
 * 2. The legacy `ingest`/`ask`/`sources` keys degrade onto those same three
 *    lanes, so a bookmark minted before the dissolution still lands
 *    somewhere specific rather than on the generic default.
 * 3. `App.tsx` mounts the three lanes when selected, fed from the app's
 *    own poll state — `status.value`/`client`/the resolved topic and vault/
 *    `obsidianCtx` — not a fresh per-lane fetch, and carries their nav tabs.
 *
 * `App.tsx` renders through Preact, and this file intentionally has a `.ts`
 * (not `.tsx`) extension — `vitest.config.ts`'s "unit" project runs `.test.ts`
 * files under Node, with no DOM, mirroring `paneRouting.test.ts`/
 * `paneRouting.improveTend.test.ts` next to it. The wiring claim is verified
 * by reading `App.tsx`'s own source text, the same technique
 * `paneRouting.improveTend.test.ts` and `m4DissolutionCensus.test.tsx` (group
 * (d)'s nav-tab scan) both already use. `fs`/`path`/`url` are loaded through a
 * dynamic `import()` whose specifier is a variable, not a string literal (no
 * `@types/node` in this project; see those files' own header comments for the
 * full rationale).
 *
 * Load-bearing assumptions about the not-yet-landed wiring (the paired
 * implementation wins on conflict; each independently falsified):
 *
 *   1. Each new pane's conditional render in `App.tsx` follows the file's own
 *      uniform shape, `{pane === "<id>" ? (\n  <Component ... />\n) : null}`.
 *   2. Each new lane is threaded with exactly the prop names its own
 *      already-landed signature declares — not a guess, since Preact prop
 *      destructuring makes any other attribute name a silent no-op:
 *        - `LearnLane.tsx`: `client`/`topic`/`vault`/`obsidianCtx` (identical
 *          to `IngestPane`'s own current call site — the pane it absorbs).
 *        - `AnswerLane.tsx`: `client`/`topic`/`vault`/`obsidianCtx`/`status`
 *          (identical to `AskPane`'s own current call site).
 *        - `FillLane.tsx`: `client`/`topic`/`vault`/`status`/
 *          `onStatusRefresh` (identical to `SourcesPane`'s own current call
 *          site — both optional on `FillLane`, so their absence would not by
 *          itself fail `tsc`, but the *values* assumption below still holds).
 *   3. The *values* handed to those props are the same identifiers every
 *      absorbed pane already reads from the app-level poll — `status.value`,
 *      `client`, `topic`, `resolvedVaultName`, `obsidianCtx`,
 *      `() => refreshStatus(false)` for `FillLane`'s `onStatusRefresh` — the
 *      exact call already wired for `SourcesPane`, the pane it absorbs. This
 *      is the one part of the assumption set that is a genuine guess about
 *      spelling (a differently-named local variable holding the same signal
 *      would still satisfy the behavior); flagged in
 *      `LEARNINGS_test-engineer_step103.md`.
 */

interface FsModule {
  readFileSync(path: string, encoding: string): string;
}
interface PathModule {
  dirname(path: string): string;
  join(...parts: string[]): string;
}
interface UrlModule {
  fileURLToPath(url: string): string;
}

const FS_MODULE_NAME = "fs";
const PATH_MODULE_NAME = "path";
const URL_MODULE_NAME = "url";

let appSource: string;

beforeAll(async () => {
  const fsModule = (await import(FS_MODULE_NAME)) as unknown as FsModule;
  const pathModule = (await import(PATH_MODULE_NAME)) as unknown as PathModule;
  const urlModule = (await import(URL_MODULE_NAME)) as unknown as UrlModule;
  const testDir = pathModule.dirname(urlModule.fileURLToPath(import.meta.url));
  const appPath = pathModule.join(testDir, "..", "App.tsx");
  appSource = fsModule.readFileSync(appPath, "utf-8");
});

/**
 * Extracts the `{pane === "<paneId>" ? ( ... ) : null}` block for `paneId`
 * from `App.tsx`'s source, or `null` if that pane has no such block yet.
 * Anchored on the render-conditional shape, not the bare `pane === "X"`
 * comparison, mirroring `paneRouting.improveTend.test.ts`'s own helper — the
 * nav tab buttons also compare `pane === "X"` (inside a `class={...}` guard),
 * and matching that occurrence first would silently pick up the wrong block.
 */
function extractPaneBlock(paneId: string): string | null {
  const marker = `{pane === "${paneId}" ? (`;
  const start = appSource.indexOf(marker);
  if (start === -1) return null;
  const end = appSource.indexOf(") : null}", start);
  if (end === -1) return null;
  return appSource.slice(start, end);
}

/**
 * Anchored on the `<nav class="pane-tabs" ...> ... </nav>` block specifically
 * (not a bare label match), mirroring `m4DissolutionCensus.test.tsx`'s own
 * helper — a label appearing elsewhere on the page cannot produce a false
 * pass/fail.
 */
function paneTabsBlock(): string {
  const start = appSource.indexOf('<nav class="pane-tabs"');
  const end = appSource.indexOf("</nav>", start);
  return appSource.slice(start, end);
}

describe("resolvePane resolves learn/answer/fill to their own new panes, not the legacy repoint", () => {
  it("resolves ?pane=learn to the new learn pane", () => {
    expect(resolvePane("learn")).toBe("learn");
  });

  it("resolves ?pane=answer to the new answer pane", () => {
    expect(resolvePane("answer")).toBe("answer");
  });

  it("resolves ?pane=fill to the new fill pane", () => {
    expect(resolvePane("fill")).toBe("fill");
  });

  it("adds all three new pane ids to the PANE_BY_PARAM allowlist itself, not just resolvePane's fallback", () => {
    expect(PANE_BY_PARAM.get("learn")).toBe("learn");
    expect(PANE_BY_PARAM.get("answer")).toBe("answer");
    expect(PANE_BY_PARAM.get("fill")).toBe("fill");
  });
});

describe("the legacy ingest/ask/sources keys degrade to the lanes that absorbed them", () => {
  const REPOINTED: ReadonlyArray<readonly [string, string]> = [
    ["ingest", "learn"],
    ["ask", "answer"],
    ["sources", "fill"],
  ];

  it.each(REPOINTED)(
    "resolves the legacy ?pane=%s to %s, so an old bookmark still lands somewhere specific",
    (param, pane) => {
      expect(resolvePane(param)).toBe(pane);
    },
  );

  it("falls back to the default pane for an unrecognised value", () => {
    expect(resolvePane("not-a-real-pane")).toBe("tend");
  });
});

describe("App.tsx mounts LearnLane/AnswerLane/FillLane, fed from the app's own poll state", () => {
  it("imports LearnLane from its landed lane module", () => {
    expect(appSource).toMatch(
      /import\s*\{\s*LearnLane\s*\}\s*from\s*["']\.\/lanes\/learn\/LearnLane["']/,
    );
  });

  it("imports AnswerLane from its landed lane module", () => {
    expect(appSource).toMatch(
      /import\s*\{\s*AnswerLane\s*\}\s*from\s*["']\.\/lanes\/answer\/AnswerLane["']/,
    );
  });

  it("imports FillLane from its landed lane module", () => {
    expect(appSource).toMatch(
      /import\s*\{\s*FillLane\s*\}\s*from\s*["']\.\/lanes\/fill\/FillLane["']/,
    );
  });

  it("renders LearnLane when pane is learn", () => {
    expect(extractPaneBlock("learn")).not.toBeNull();
    expect(extractPaneBlock("learn") ?? "").toContain("<LearnLane");
  });

  it("feeds LearnLane from the app poll's own client/topic/vault/obsidianCtx state, not a fresh fetch", () => {
    const block = extractPaneBlock("learn") ?? "";
    expect(block).toMatch(/client=\{client\}/);
    expect(block).toMatch(/topic=\{topic\}/);
    expect(block).toMatch(/vault=\{resolvedVaultName\}/);
    expect(block).toMatch(/obsidianCtx=\{obsidianCtx\}/);
  });

  it("renders AnswerLane when pane is answer", () => {
    expect(extractPaneBlock("answer")).not.toBeNull();
    expect(extractPaneBlock("answer") ?? "").toContain("<AnswerLane");
  });

  it("feeds AnswerLane from the app poll's own status/client state, not a fresh fetch", () => {
    const block = extractPaneBlock("answer") ?? "";
    expect(block).toMatch(/client=\{client\}/);
    expect(block).toMatch(/vault=\{resolvedVaultName\}/);
    expect(block).toMatch(/obsidianCtx=\{obsidianCtx\}/);
    expect(block).toMatch(/status=\{status\.value\}/);
  });

  it("renders FillLane when pane is fill", () => {
    expect(extractPaneBlock("fill")).not.toBeNull();
    expect(extractPaneBlock("fill") ?? "").toContain("<FillLane");
  });

  it("feeds FillLane from the app poll's own status/client state, not a fresh fetch", () => {
    const block = extractPaneBlock("fill") ?? "";
    expect(block).toMatch(/client=\{client\}/);
    expect(block).toMatch(/vault=\{resolvedVaultName\}/);
    expect(block).toMatch(/status=\{status\.value\}/);
  });

  it("keeps the two earlier lanes' render blocks intact", () => {
    for (const survivor of ["improve", "tend"]) {
      expect(extractPaneBlock(survivor)).not.toBeNull();
    }
  });
});

describe("nav carries the three new lane tabs alongside the two earlier ones", () => {
  it.each(["Learn", "Answer", "Fill"])("nav renders a new %s tab", (label) => {
    expect(paneTabsBlock()).toMatch(new RegExp(`>\\s*${label}\\s*[<{]`));
  });

  it.each(["Improve", "Tend"])(
    "nav still renders the earlier %s tab",
    (label) => {
      expect(paneTabsBlock()).toMatch(new RegExp(`>\\s*${label}\\s*[<{]`));
    },
  );
});
