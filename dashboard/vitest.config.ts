import preact from "@preact/preset-vite";
import { defineConfig } from "vitest/config";

/**
 * Test-only config. `vite build` never reads this file, so the committed
 * artifact is unaffected; vitest reads it instead of `vite.config.ts`, which
 * is why the preact preset is repeated here rather than imported from there
 * (that config also carries `viteSingleFile`, which has no place in a test run).
 *
 * Split into two `test.projects` -- Vitest 4's mechanism for varying config
 * (here, `environment`) by file pattern. `environmentMatchGlobs` and the
 * per-file `@vitest-environment` docblock, the two lighter-weight
 * alternatives, were both removed by Vitest 4; `projects` is the supported
 * replacement. Pure-logic tests (`.test.ts`) keep the "unit" project's
 * default "node" environment -- one of them (`paneRouting.test.ts`) asserts
 * `typeof globalThis.window === "undefined"` as a load-bearing invariant
 * (proof that `resolvePane` reads no browser global), which a blanket
 * `environment: "jsdom"` would silently break. Only `.test.tsx` files --
 * which import JSX to render a component -- run in the "dom" project's
 * `jsdom` environment.
 */
export default defineConfig({
  plugins: [preact()],
  test: {
    projects: [
      {
        extends: true,
        test: {
          name: "unit",
          include: ["src/**/*.test.ts"],
          environment: "node",
        },
      },
      {
        extends: true,
        test: {
          name: "dom",
          include: ["src/**/*.test.tsx"],
          environment: "jsdom",
        },
      },
    ],
  },
});
