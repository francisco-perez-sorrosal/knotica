import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";

import { Spinner } from "./icons";
import { ProcessBrief } from "./lanes/ProcessBrief";
import type { ToolClient } from "./toolClient";

export interface CreateDrawerProps {
  /** Owned by `App.tsx`'s `⊕` trigger -- this component is always mounted, so
   *  field state below survives a close/reopen exactly as it did before the
   *  extraction; `open` only gates whether the panel renders. */
  open: boolean;
  client: ToolClient | null;
  onClose: () => void;
  onCreatedKb: (name: string) => Promise<void>;
  onCreatedTopic: (name: string) => void;
  onRefreshStatus: (includeMetrics?: boolean) => Promise<void>;
}

/**
 * The "New knowledge base" / "New topic" panel behind the chrome `⊕`
 * trigger -- extracted from `App.tsx` (added in the chrome restructure).
 * The trigger button and the `open` flag it controls stay in `App.tsx`;
 * this component owns everything downstream of "the drawer is open": both
 * forms' field state, submission, and errors.
 */
export function CreateDrawer({
  open,
  client,
  onClose,
  onCreatedKb,
  onCreatedTopic,
  onRefreshStatus,
}: CreateDrawerProps): JSX.Element | null {
  const [newKbPath, setNewKbPath] = useState("");
  const [newKbName, setNewKbName] = useState("");
  const [newKbTopic, setNewKbTopic] = useState("");
  const [newKbBusy, setNewKbBusy] = useState(false);
  const [newKbError, setNewKbError] = useState<string | null>(null);
  const [newTopicName, setNewTopicName] = useState("");
  const [newTopicBusy, setNewTopicBusy] = useState(false);
  const [newTopicError, setNewTopicError] = useState<string | null>(null);

  // The old chrome trigger cleared both errors on every click, open or
  // close; clearing was only ever observable on the *next* open (closed
  // means unrendered). Reproduced here as the equivalent open-transition
  // effect, since the trigger button itself stays in `App.tsx`.
  useEffect(() => {
    if (!open) return;
    setNewKbError(null);
    setNewTopicError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  if (!open) return null;

  function newKbBasename(path: string): string {
    const trimmed = path.trim().replace(/\/+$/, "");
    const parts = trimmed.split("/");
    return parts[parts.length - 1] || trimmed;
  }

  async function submitNewKb(event: Event) {
    event.preventDefault();
    const path = newKbPath.trim();
    if (!client || !path) return;
    setNewKbBusy(true);
    setNewKbError(null);
    try {
      const name = newKbName.trim() || newKbBasename(path);
      await client.vaultCreate(name, path, newKbTopic.trim(), true);
      onClose();
      setNewKbPath("");
      setNewKbName("");
      setNewKbTopic("");
      await onCreatedKb(name);
    } catch (cause) {
      setNewKbError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setNewKbBusy(false);
    }
  }

  // A knowledge base is normally several topics, but `vault action=create`
  // seeds only the first — so without this the dashboard could start a KB and
  // then not grow it. Refresh before selecting: the picker renders from the
  // status payload, and selecting a topic it has not yet seen shows an entry
  // that vanishes on the next poll.
  async function submitNewTopic(event: Event) {
    event.preventDefault();
    const name = newTopicName.trim();
    if (!client || !name) return;
    setNewTopicBusy(true);
    setNewTopicError(null);
    try {
      await client.createTopic(name);
      onClose();
      setNewTopicName("");
      await onRefreshStatus(true);
      onCreatedTopic(name);
    } catch (cause) {
      setNewTopicError(
        cause instanceof Error ? cause.message : String(cause),
      );
    } finally {
      setNewTopicBusy(false);
    }
  }

  return (
    <div id="chrome-create-drawer" class="chrome-create-drawer">
      <form
        class="doctor-repair-toolbar"
        onSubmit={(event) => void submitNewKb(event)}
      >
        <p class="microlabel chrome-create-drawer-title">
          New knowledge base
          {/* Two forms, one `Create` label each -- the brief is what tells
              them apart in words as well as by their fields. */}
          <ProcessBrief process="vault.create" term="why a new one" />
        </p>
        <label class="heal-inline-field">
          <span>path</span>
          <input
            type="text"
            required
            value={newKbPath}
            placeholder="/path/to/vault"
            onInput={(event) =>
              setNewKbPath((event.target as HTMLInputElement).value)
            }
          />
        </label>
        <label class="heal-inline-field">
          <span>name</span>
          <input
            type="text"
            value={newKbName}
            placeholder={newKbBasename(newKbPath) || "vault name"}
            onInput={(event) =>
              setNewKbName((event.target as HTMLInputElement).value)
            }
          />
        </label>
        <label class="heal-inline-field">
          <span>topic</span>
          <input
            type="text"
            value={newKbTopic}
            placeholder="optional"
            onInput={(event) =>
              setNewKbTopic((event.target as HTMLInputElement).value)
            }
          />
        </label>
        <button
          type="submit"
          class="primary"
          disabled={newKbBusy || !newKbPath.trim()}
          aria-busy={newKbBusy || undefined}
        >
          {newKbBusy ? (
            <>
              <Spinner />
              Creating…
            </>
          ) : (
            "Create"
          )}
        </button>
        <button
          type="button"
          class="toggle"
          onClick={() => {
            onClose();
            setNewKbError(null);
          }}
        >
          Cancel
        </button>
        {newKbError ? <p class="tone-bad">{newKbError}</p> : null}
      </form>

      <form
        class="doctor-repair-toolbar"
        onSubmit={(event) => void submitNewTopic(event)}
      >
        <p class="microlabel chrome-create-drawer-title">
          New topic
          <ProcessBrief process="learn.create_topic" term="why another topic" />
        </p>
        <label class="heal-inline-field">
          <span>topic</span>
          <input
            type="text"
            required
            value={newTopicName}
            placeholder="pretraining"
            onInput={(event) =>
              setNewTopicName((event.target as HTMLInputElement).value)
            }
          />
        </label>
        <button
          type="submit"
          class="primary"
          disabled={newTopicBusy || !newTopicName.trim()}
          aria-busy={newTopicBusy || undefined}
        >
          {newTopicBusy ? (
            <>
              <Spinner />
              Creating…
            </>
          ) : (
            "Create"
          )}
        </button>
        <button
          type="button"
          class="toggle"
          onClick={() => {
            onClose();
            setNewTopicError(null);
          }}
        >
          Cancel
        </button>
        {newTopicError ? <p class="tone-bad">{newTopicError}</p> : null}
      </form>
    </div>
  );
}
