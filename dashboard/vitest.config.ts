import preact from "@preact/preset-vite";
import { defineConfig } from "vitest/config";

/**
 * Test-only config. `vite build` never reads this file, so the committed
 * artifact is unaffected; vitest reads it instead of `vite.config.ts`, which
 * is why the preact preset is repeated here rather than imported from there
 * (that config also carries `viteSingleFile`, which has no place in a test run).
 */
export default defineConfig({
  plugins: [preact()],
  test: {
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
});
