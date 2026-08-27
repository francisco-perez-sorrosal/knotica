/**
 * Home's half of `ToolClient`: the one status read the cross-topic inbox makes.
 *
 * `wiki_status` is the vault's single status endpoint, and both of its views
 * live here rather than being split across lanes -- `view="summary"` is the
 * shell's rail poll and `view="attention"` is Home's own inbox, but they are
 * one tool call with one argument, and a lane boundary drawn through a single
 * parameter would be a boundary in name only.
 */

import type { WikiStatus } from "../../types";
import type { ToolCallGroup } from "../../toolClientCore";

import type { StatusView } from "./types";

export interface HomeToolCalls {
  wikiStatus(
    topic: string,
    vault?: string,
    view?: StatusView,
  ): Promise<WikiStatus>;
}

export const homeToolCalls: ToolCallGroup<HomeToolCalls> = {
  wikiStatus(
    topic: string,
    vault = "",
    view: StatusView = "summary",
  ): Promise<WikiStatus> {
    return this.call("wiki_status", { topic, vault, view });
  },
};
