import { h } from "preact";
import { useEffect, useMemo, useState } from "preact/hooks";

import { formatMetadataSize, lookupMetadataCatalog } from "./metadataCatalog";
import { ObsidianFileLink, type ObsidianContext } from "./obsidianLinks";
import { formatToolFailure, type ToolClient } from "./toolClient";
import type { MetadataTreeNode, VaultMetadataTree } from "./types";

function collectExpandedDefaults(nodes: MetadataTreeNode[], depth = 0): Set<string> {
  const out = new Set<string>();
  for (const node of nodes) {
    if (node.kind === "dir" && depth < 1) {
      out.add(node.path);
    }
    if (node.children?.length) {
      for (const childPath of collectExpandedDefaults(node.children, depth + 1)) {
        out.add(childPath);
      }
    }
  }
  return out;
}

function MetadataTreeRow({
  node,
  depth,
  expanded,
  onToggle,
  obsidianCtx,
}: {
  node: MetadataTreeNode;
  depth: number;
  expanded: Set<string>;
  onToggle: (path: string) => void;
  obsidianCtx: ObsidianContext;
}) {
  const catalog = lookupMetadataCatalog(node.path);
  const isDir = node.kind === "dir";
  const isOpen = isDir && expanded.has(node.path);
  const sizeLabel = node.kind === "file" ? formatMetadataSize(node.size) : null;
  const tooltip = `${catalog.title} — ${catalog.purpose}${sizeLabel ? ` (${sizeLabel})` : ""}`;

  return (
    <li class="metadata-tree-item">
      <div
        class={`metadata-tree-row ${isDir ? "is-dir" : "is-file"}`}
        style={{ paddingLeft: `${depth * 0.85 + 0.35}rem` }}
        title={tooltip}
      >
        {isDir ? (
          <button
            type="button"
            class="metadata-tree-toggle"
            aria-expanded={isOpen}
            aria-label={isOpen ? `Collapse ${node.name}` : `Expand ${node.name}`}
            onClick={() => onToggle(node.path)}
          >
            <span class="metadata-tree-chevron" aria-hidden="true">
              {isOpen ? "▾" : "▸"}
            </span>
          </button>
        ) : (
          <span class="metadata-tree-spacer" aria-hidden="true" />
        )}
        <span class={`metadata-tree-icon ${isDir ? "dir" : "file"}`} aria-hidden="true" />
        {isDir ? (
          <span class="metadata-tree-name">{node.name}</span>
        ) : (
          <ObsidianFileLink
            ctx={obsidianCtx}
            relativePath={node.path}
            className="metadata-tree-name metadata-tree-link"
            title={tooltip}
          >
            {node.name}
          </ObsidianFileLink>
        )}
        {sizeLabel ? <span class="metadata-tree-meta">{sizeLabel}</span> : null}
      </div>
      {isDir && isOpen && node.children?.length ? (
        <ul class="metadata-tree-children">
          {node.children.map((child) => (
            <MetadataTreeRow
              key={child.path}
              node={child}
              depth={depth + 1}
              expanded={expanded}
              onToggle={onToggle}
              obsidianCtx={obsidianCtx}
            />
          ))}
        </ul>
      ) : null}
      {isDir && isOpen && !node.children?.length ? (
        <p
          class="muted metadata-tree-empty"
          style={{ paddingLeft: `${(depth + 1) * 0.85 + 0.35}rem` }}
        >
          Empty directory
        </p>
      ) : null}
    </li>
  );
}

export function MetadataTreePanel({
  client,
  vault,
  topic,
  vaultReady,
  obsidianCtx,
}: {
  client: ToolClient | null;
  vault: string;
  topic: string;
  vaultReady: boolean;
  obsidianCtx: ObsidianContext;
}) {
  const [openSection, setOpenSection] = useState(true);
  const [tree, setTree] = useState<VaultMetadataTree | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());

  const defaultExpanded = useMemo(
    () => (tree?.children ? collectExpandedDefaults(tree.children) : new Set<string>()),
    [tree],
  );

  useEffect(() => {
    setExpanded(new Set(defaultExpanded));
  }, [defaultExpanded]);

  useEffect(() => {
    if (!client || !vaultReady) return;
    let cancelled = false;
    setBusy(true);
    setError(null);
    void client
      .vaultMetadataTree(vault, topic)
      .then((payload) => {
        if (!cancelled) setTree(payload);
      })
      .catch((cause) => {
        if (!cancelled) setError(formatToolFailure(cause, "vault_metadata_tree"));
      })
      .finally(() => {
        if (!cancelled) setBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [client, vault, topic, vaultReady]);

  function togglePath(path: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  const count = tree?.children?.length ?? 0;

  return (
    <section class="metadata-tree-panel">
      <button
        type="button"
        class="metadata-tree-section-toggle"
        aria-expanded={openSection}
        onClick={() => setOpenSection((value) => !value)}
      >
        <span class="metadata-tree-section-chevron" aria-hidden="true">
          {openSection ? "▾" : "▸"}
        </span>
        <span>
          <strong>Vault metadata (.knotica)</strong>
          <small class="muted">
            {busy && !tree
              ? "Loading…"
              : count
                ? `${count} top-level entr${count === 1 ? "y" : "ies"} · hover for purpose`
                : "No metadata files yet"}
          </small>
        </span>
      </button>

      {openSection ? (
        <div class="metadata-tree-body">
          {error ? <p role="alert">{error}</p> : null}
          {!error && busy && !tree ? <p class="muted">Loading metadata tree…</p> : null}
          {!error && tree && count === 0 ? (
            <p class="muted">No `.knotica` trees or root metadata files on disk yet.</p>
          ) : null}
          {!error && tree && count > 0 ? (
            <ul class="metadata-tree-root" aria-label="Vault metadata tree">
              {tree.children.map((node) => (
                <MetadataTreeRow
                  key={node.path}
                  node={node}
                  depth={0}
                  expanded={expanded}
                  onToggle={togglePath}
                  obsidianCtx={obsidianCtx}
                />
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
