import { Fragment, h } from "preact";
import type { ComponentChildren, JSX } from "preact";

/**
 * Vault identity from ``wiki_status`` — enough to build Obsidian URIs.
 *
 * Obsidian's ``vault=`` query param is the **folder basename** of the vault on disk
 * (e.g. ``knotica`` for ``/Users/me/dev/data/knotica``), not the Knotica config vault
 * key (``main``). Always derive the name from ``vaultPath`` when available.
 */
export interface ObsidianContext {
  /** Fallback only — prefer ``vaultPath`` basename via ``resolveObsidianVaultName``. */
  vaultName?: string;
  vaultPath?: string;
}

/** Last path segment — Obsidian's registered vault name (folder basename). */
export function obsidianVaultNameFromPath(vaultPath: string): string | null {
  const trimmed = vaultPath.trim();
  if (!trimmed) return null;
  const normalized = trimmed.replace(/\\/g, "/").replace(/\/+$/, "");
  const slash = normalized.lastIndexOf("/");
  const base = slash >= 0 ? normalized.slice(slash + 1) : normalized;
  return base || null;
}

/** Obsidian vault folder name for URI ``vault=`` params — path basename wins over config key. */
export function resolveObsidianVaultName(ctx: ObsidianContext): string | undefined {
  const fromPath = ctx.vaultPath ? obsidianVaultNameFromPath(ctx.vaultPath) : null;
  if (fromPath) return fromPath;
  const name = ctx.vaultName?.trim();
  return name && name !== "…" ? name : undefined;
}

/** Build Obsidian URIs for the dashboard. Prefer ``vault`` + ``file``; fall back to ``path=``. */
export function obsidianOpenVault(vaultName: string): string | null {
  const name = vaultName.trim();
  if (!name || name === "…") return null;
  return `obsidian://open?vault=${encodeURIComponent(name)}`;
}

/** Encode a vault-relative note path for the ``file`` query param. */
export function encodeVaultFile(relativePath: string): string {
  const normalized = relativePath.replace(/^\/+/, "").replace(/\\/g, "/");
  return encodeURIComponent(normalized).replace(/%2F/g, "/");
}

export function obsidianOpenFile(vaultName: string, vaultRelativePath: string): string | null {
  const name = vaultName.trim();
  const file = vaultRelativePath.trim();
  if (!name || name === "…" || !file) return null;
  return `obsidian://open?vault=${encodeURIComponent(name)}&file=${encodeVaultFile(file)}`;
}

export function obsidianOpenPath(absolutePath: string): string | null {
  const path = absolutePath.trim();
  if (!path) return null;
  return `obsidian://open?path=${encodeURIComponent(path)}`;
}

export function obsidianOpenVaultFromContext(ctx: ObsidianContext): string | null {
  const name = resolveObsidianVaultName(ctx);
  return name ? obsidianOpenVault(name) : null;
}

/** Resolve a vault-relative path to the best available Obsidian URI. */
export function resolveObsidianUri(
  ctx: ObsidianContext,
  vaultRelativePath: string,
): string | null {
  const relative = vaultRelativePath.trim();
  if (!relative) return null;
  const vaultName = resolveObsidianVaultName(ctx);
  const byVault = vaultName ? obsidianOpenFile(vaultName, relative) : null;
  if (byVault) return byVault;
  if (ctx.vaultPath) {
    const abs = joinVaultPath(ctx.vaultPath, relative);
    return obsidianOpenPath(abs);
  }
  return null;
}

/** Topic page stem or path → vault-relative ``.md`` note path. */
export function topicPageRelativePath(topic: string, page: string): string {
  const cleaned = page.replace(/^\/+/, "").replace(/\\/g, "/");
  if (cleaned.includes("/")) {
    return cleaned.endsWith(".md") ? cleaned : `${cleaned}.md`;
  }
  const stem = cleaned.endsWith(".md") ? cleaned.slice(0, -3) : cleaned;
  return `${topic}/${stem}.md`;
}

/** Citation key → stored source note path. */
export function sourceRelativePath(topic: string, citationKey: string): string {
  const key = citationKey.trim();
  if (!key) return "";
  return key.includes("/") ? key : `sources/${topic}/${key}.md`;
}

function joinVaultPath(vaultPath: string, relative: string): string {
  const root = vaultPath.replace(/\/+$/, "");
  const rel = relative.replace(/^\/+/, "");
  return `${root}/${rel}`;
}

export function ObsidianLink({
  href,
  children,
  className = "",
  title = "Open in Obsidian",
  onClick,
}: {
  href: string | null | undefined;
  children: ComponentChildren;
  className?: string;
  title?: string;
  onClick?: (event: MouseEvent) => void;
}): JSX.Element | ComponentChildren {
  if (!href) return h(Fragment, null, children);
  return h(
    "a",
    {
      href,
      class: `obsidian-link ${className}`.trim(),
      title,
      onClick,
    },
    children,
  );
}

export function ObsidianFileLink({
  ctx,
  relativePath,
  children,
  className = "",
  title = "Open in Obsidian",
  onClick,
}: {
  ctx: ObsidianContext;
  relativePath: string;
  children: ComponentChildren;
  className?: string;
  title?: string;
  onClick?: (event: MouseEvent) => void;
}): JSX.Element {
  return h(ObsidianLink, {
    href: resolveObsidianUri(ctx, relativePath),
    className,
    title,
    onClick,
    children,
  });
}
