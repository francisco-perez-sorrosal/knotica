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

export function flywheelTone(
  label: ReturnType<typeof flywheelLabel>,
): "ok" | "warn" | "bad" {
  if (label === "Compiled") return "ok";
  if (label === "Compiling" || label === "Ready") return "warn";
  return "bad";
}
