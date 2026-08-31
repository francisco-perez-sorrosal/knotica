import type { ProcessId, ProcessMeta } from "../processContract";

/**
 * Improve's ten: the billed eval/gate core, the compile, Instrument's three dataset verbs, the arena scorer switch, the two branch verbs, and the probe.
 *
 * Keyed by `Extract<ProcessId, "improve.*">` rather than by a hand-written union:
 * the `Record` is exhaustive over exactly the ids in this namespace, so a new
 * `improve.*` id added to `ProcessId` is a compile error here until its row is
 * written, and a row belonging to another namespace cannot be filed here by
 * accident. The id namespace is the split axis because rows change together
 * per lane -- which is how every migration wave was scoped.
 */
export const IMPROVE_PROCESSES: Record<
  Extract<ProcessId, `improve.${string}`>,
  ProcessMeta
> = {
  "improve.run_eval": {
    lane: "improve",
    stage: "observe",
    title: "Run eval now (billed)",
    spend: "billed",
    mutates: true,
    dispatch: "client",
    clientMethod: "loopRunEval",
    why: "The trend only moves when a cycle actually scores this topic against the held-out set; without a fresh scalar there is nothing for the gate to compare a candidate to.",
    willDo:
      "Runs one eval cycle on the held-out set and records the scalar as a new generation. Billed — you see the worker, the judge, the thread count and the estimate before anything runs. Nothing in the wiki changes.",
    previewMode: "nonce",
    progressMode: "busy",
    outcomeMode: "result",
    next: {
      kind: "always",
      go: {
        lane: "improve",
        stage: "gate",
        why: "A fresh scalar is only worth having if a candidate is measured against it — gating is what turns the number into a verdict.",
      },
    },
  },

  "improve.gate_candidate": {
    lane: "improve",
    stage: "gate",
    title: "Gate next candidate now",
    spend: "billed",
    mutates: true,
    dispatch: "client",
    clientMethod: "loopRunOnce",
    why: "A compiled candidate is waiting and nothing downstream moves until it is measured against the frozen baseline.",
    willDo:
      "Runs one eval cycle against the gate baseline and stamps a verdict on the candidate branch. Billed — you see the estimate before anything runs. The verdict is recorded, not applied.",
    previewMode: "nonce",
    progressMode: "busy",
    outcomeMode: "verdict",
    next: {
      kind: "conditional",
      // The discriminant is `LoopOnceResult.decision`, whose vocabulary is
      // `core/loop_state.py::LoopDecision` — `pass` / `fail` / `none`. It is
      // *not* the gapfill gate's `merged` / `refused`; the two gates are
      // different instruments and share no verdict word.
      branches: [
        {
          when: "pass",
          go: {
            lane: "improve",
            stage: "promote",
            why: "The candidate cleared the baseline — merge it into the vault so answers actually improve.",
          },
        },
        {
          when: "fail",
          go: {
            lane: "improve",
            stage: "heal",
            why: "The candidate did not clear the baseline; a fresh compile is how you try again.",
          },
        },
      ],
      fallback: {
        lane: "improve",
        stage: "observe",
        why: "Nothing was decided this tick — read the trend and the raw cycle before spending again.",
      },
    },
  },

  "improve.compile_run": {
    lane: "improve",
    stage: "heal",
    title: "Compile now",
    spend: "billed",
    mutates: true,
    dispatch: "client",
    clientMethod: "compileRun",
    why: "The gate refused the last candidate, so the standing program is the best this topic has and nothing improves until a fresh optimisation produces something else to measure.",
    willDo:
      "Re-optimises the prompt program against the trainset and writes the result to a new candidate branch. Billed, and the first click only arms the control. The vault's answers do not change — a candidate branch is not promoted by compiling it.",
    previewMode: "armed",
    progressMode: "busy",
    // `compile action=run` returns the branch it wrote and no sentence about
    // it; before this row a finished compile looked exactly like a click that
    // did nothing, because the only visible effect was a status re-read.
    outcomeMode: "refresh",
    outcomeFallback:
      "A fresh candidate branch is compiled and waiting to be measured.",
    next: {
      kind: "always",
      go: {
        lane: "improve",
        stage: "gate",
        why: "A candidate is only worth having once it is scored against the baseline — until it is gated, nothing knows whether this compile was an improvement.",
      },
    },
  },

  // Instrument's three. All three already print what they did; what none of
  // them said was which step the numbers they moved are owed to next.
  "improve.datasets_bootstrap": {
    lane: "improve",
    stage: "instrument",
    title: "Bootstrap",
    spend: "billed",
    mutates: true,
    dispatch: "client",
    clientMethod: "datasetsBootstrap",
    why: "The golden pipeline starts empty, and with no candidate questions there is nothing to review and therefore nothing that can ever be frozen into a held-out set.",
    willDo:
      "Synthesises candidate questions from this topic's entity pages and writes them to the candidates file. Billed, and the first click only arms the control. It adds candidates; it never overwrites a reviewed or held-out set.",
    previewMode: "armed",
    progressMode: "busy",
    outcomeMode: "result",
    next: {
      kind: "terminal",
      why: "Candidates need a human verdict before they can be frozen, and this surface has no control for that review — the numbers above are the whole of what changed here.",
    },
  },

  "improve.datasets_bootstrap_train": {
    lane: "improve",
    stage: "instrument",
    title: "Bootstrap trainset",
    spend: "billed",
    mutates: true,
    dispatch: "client",
    clientMethod: "datasetsBootstrapTrain",
    why: "A compile optimises the prompt program against the trainset, so an empty or thin trainset caps how good any compiled candidate can be no matter how often it is run.",
    willDo:
      "Reads this topic's pages and appends labelled examples to the trainset. Billed, and the first click only arms the control. The trainset stays disjoint from the held-out set — a question the model trained on measures nothing.",
    previewMode: "armed",
    progressMode: "busy",
    outcomeMode: "result",
    next: {
      kind: "always",
      go: {
        lane: "improve",
        stage: "heal",
        why: "A widened trainset only pays off through a compile — that is the step that reads it and turns it into a new candidate program.",
      },
    },
  },

  "improve.datasets_freeze": {
    lane: "improve",
    stage: "instrument",
    title: "Freeze golden",
    // Writes files and commits; it calls no model.
    spend: "free",
    mutates: true,
    dispatch: "client",
    clientMethod: "datasetsFreeze",
    why: "Every later comparison in this topic is made against the held-out set, so until one is frozen there is no baseline, no gate verdict and no eval scalar that means anything.",
    willDo:
      "Moves the reviewed candidates into the held-out set and commits them. Nothing is billed. Consequential rather than destructive: the frozen set becomes the stick every future scalar is measured with, and it refuses outright to freeze a set that overlaps the trainset.",
    previewMode: "armed",
    progressMode: "busy",
    outcomeMode: "result",
    next: {
      kind: "always",
      go: {
        lane: "improve",
        stage: "observe",
        why: "A frozen held-out set is what an eval cycle scores against — running one is how the set turns into the first number worth comparing to.",
      },
    },
  },

  "improve.arena_scorer_switch": {
    lane: "improve",
    stage: "heal",
    // Ships under two labels, one per direction: `Use eval scorer` arms the
    // spend and is armed→confirm; `Use heuristic scorer` goes back to free on
    // a single quiet click. The asymmetry is deliberate and recorded rather
    // than flattened -- going free needs no guard.
    title: "Use eval scorer",
    spend: "arms-billing",
    // Writes one key under `[loop]` in the config file, not the vault.
    mutates: false,
    dispatch: "client",
    clientMethod: "loopCadence",
    why: "The race was refused before scoring because the arena scorer and the gate baseline are not the same instrument, so no ranking between them would mean anything and every race aborts the same way until one of them changes.",
    willDo:
      "Writes the chosen scorer under `[loop]` in your config. This click bills nothing; switching to the eval scorer arms one full golden-set eval per variant on every future race. Reversible — the same control switches back.",
    previewMode: "armed",
    progressMode: "busy",
    outcomeMode: "result",
    next: {
      kind: "terminal",
      why: "Both runners rebuild from config on every tick, so the scorer takes effect without a restart and nothing else is owed here.",
    },
  },

  "improve.branch_promote": {
    lane: "improve",
    stage: "promote",
    title: "Preview promote",
    spend: "free",
    mutates: true,
    dispatch: "client",
    clientMethod: "branchPromote",
    why: "A candidate that cleared the baseline changes nothing while it sits on a branch — the topic keeps answering with the program it had until the branch is merged.",
    willDo:
      "Shows you exactly what merging this branch changes, and merges it only on a second, explicit click. Nothing is billed. Reversible — the merge is a normal commit.",
    previewMode: "dry-run",
    progressMode: "busy",
    outcomeMode: "result",
    next: {
      kind: "always",
      go: {
        lane: "improve",
        stage: "prove",
        why: "The scoreboard says it scores better; the probe is the only place you see whether it actually answers better, which is the claim the whole loop is making.",
      },
    },
  },

  "improve.branch_delete": {
    lane: "improve",
    stage: "promote",
    title: "Preview delete",
    spend: "free",
    mutates: true,
    dispatch: "client",
    clientMethod: "branchDelete",
    why: "This candidate is not going to be merged, and an open branch nobody drops keeps presenting itself for review every time this stage is opened.",
    willDo:
      "Shows you what dropping this branch removes, and drops it only on a second, explicit click. Nothing is billed and the vault keeps the program it already had — the branch goes, the wiki does not change.",
    previewMode: "dry-run",
    progressMode: "busy",
    outcomeMode: "result",
    next: {
      kind: "always",
      go: {
        lane: "improve",
        stage: "heal",
        why: "Dropping the candidate leaves the topic on its last promoted program, so a fresh compile is what produces something else to try.",
      },
    },
  },

  // The probe and Answer's `Ask` call the same billed `query` tool. `query`
  // mints no `confirm_nonce`, so both preview client-side (`armed`) rather
  // than on a server quote -- the last two single-click spends, closed. The
  // spend grammar now has no exception: every billed click arms first.
  "improve.probe": {
    lane: "improve",
    stage: "prove",
    title: "Probe it",
    spend: "billed",
    // `query` reads pages and answers; it writes nothing back.
    mutates: false,
    dispatch: "client",
    clientMethod: "query",
    why: "A promoted program is only actually better if it answers better, and a scoreboard delta cannot tell you whether the questions this topic exists for improved.",
    willDo:
      "Asks the compiled program this question and renders the answer beside the one you pinned. It costs tokens, so only a second, explicit click sends it; the answer is not stored, so nothing in the vault changes.",
    previewMode: "armed",
    progressMode: "busy",
    outcomeMode: "result",
    next: {
      kind: "terminal",
      why: "The flywheel closed here: you have read what the compiled program actually says, which is the only evidence the loop was worth running.",
    },
  },
};
