import type { ProcessId, ProcessMeta } from "../processContract";

/**
 * Learn's two: the pages handoff, and topic creation from the chrome.
 *
 * Keyed by `Extract<ProcessId, "learn.*">` rather than by a hand-written union:
 * the `Record` is exhaustive over exactly the ids in this namespace, so a new
 * `learn.*` id added to `ProcessId` is a compile error here until its row is
 * written, and a row belonging to another namespace cannot be filed here by
 * accident. The id namespace is the split axis because rows change together
 * per lane -- which is how every migration wave was scoped.
 */
export const LEARN_PROCESSES: Record<
  Extract<ProcessId, `learn.${string}`>,
  ProcessMeta
> = {
  "learn.ingest_dispatch": {
    lane: "learn",
    stage: "pages",
    // Ships under three labels, one per capability tier: `Send to Claude`,
    // `Queue for Claude`, or -- where the host can do neither -- the copyable
    // instruction itself. One process, three affordances for one payload.
    title: "Send to Claude",
    spend: "free",
    mutates: false,
    dispatch: "handoff",
    clientMethod: null,
    why: "The run has fetched and parsed the source and is waiting on the turn that writes pages from it, and only your Claude session can take that turn.",
    willDo:
      "Sends the ingest instruction to your Claude session. Nothing is written from here — the rail advances as the session writes, and this stage re-reads the journal every second.",
    previewMode: "none",
    progressMode: "external",
    outcomeMode: "external",
    next: {
      kind: "always",
      go: {
        lane: "learn",
        stage: "curate",
        why: "A written page is not yet a training signal — curating is the separate run that turns one into an example the compiler reads.",
      },
    },
  },

  // The three chrome processes. Their triggers live in the app chrome, which
  // belongs to no lane -- so `lane`/`stage` name the lane each process serves
  // rather than the surface it is clicked on, and all three carry `stage:
  // null` to say so. It is the one place in the registry where `lane` is not
  // literally where the control is, and the alternative -- inventing a
  // seventh lane for the chrome -- would put a lane in `processModel.ts` that
  // no rail renders.
  "learn.create_topic": {
    lane: "learn",
    stage: null,
    title: "Create",
    spend: "free",
    mutates: true,
    dispatch: "client",
    clientMethod: "createTopic",
    why: "A knowledge base is normally several topics and creating a vault seeds only the first, so without this the dashboard could start a wiki and then never grow it.",
    willDo:
      "Creates the topic and its schema in the active vault and selects it. Nothing is billed. It adds a topic; it never touches the ones already there.",
    previewMode: "none",
    progressMode: "busy",
    outcomeMode: "refresh",
    outcomeFallback: "The topic is created in this vault and selected.",
    next: {
      kind: "always",
      go: {
        lane: "learn",
        stage: "source",
        why: "A topic with no source has nothing to answer from — storing one is the first step that gives it any content at all.",
      },
    },
  },
};
