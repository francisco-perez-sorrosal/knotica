/** Compile progress stages shared by Vault / Ask / Loop / chrome. */

export type CompileStageName =
  | "idle"
  | "running"
  | "optimizing"
  | "evaluating"
  | "completed"
  | "failed";

const ACTIVE = new Set<string>(["running", "optimizing", "evaluating"]);

export function isCompileActive(stage: string | null | undefined): boolean {
  return ACTIVE.has(stage ?? "idle");
}

export function flywheelLabel(input: {
  compiledPresent?: boolean;
  compileReady?: boolean;
  stage?: string | null;
}): "Compiled" | "Compiling" | "Ready" | "Curating" {
  if (input.compiledPresent) return "Compiled";
  if (isCompileActive(input.stage)) return "Compiling";
  if (input.compileReady) return "Ready";
  return "Curating";
}

/**
 * `Curating` and `Ready` are not failures. A fresh
 * topic that has not been compiled yet is the honest absence of a comparison,
 * not a red alarm — it reads `neutral`. `Ready` is actionable rather than
 * worrying, so it reads `info`. Only a genuinely in-flight compile keeps
 * `warn`; no label maps to `bad` any more, which is why `bad` has left the
 * return type rather than lingering as an unreachable member.
 */
export function flywheelTone(
  label: ReturnType<typeof flywheelLabel>,
): "ok" | "warn" | "info" | "neutral" {
  if (label === "Compiled") return "ok";
  if (label === "Compiling") return "warn";
  if (label === "Ready") return "info";
  return "neutral";
}
