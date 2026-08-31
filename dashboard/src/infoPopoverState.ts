import { signal } from "@preact/signals";

/**
 * Module-level "at most one InfoPopover open" signal. A
 * popover identifies itself by its own `id`; opening one closes whatever
 * else was open -- two floating panels competing for the same
 * iframe-constrained viewport is worse than one closing early.
 */
const openPopoverId = signal<string | null>(null);

export function isPopoverOpen(id: string): boolean {
  return openPopoverId.value === id;
}

export function openPopover(id: string): void {
  openPopoverId.value = id;
}

export function closePopover(id: string): void {
  if (openPopoverId.value === id) {
    openPopoverId.value = null;
  }
}

export function togglePopover(id: string): void {
  openPopoverId.value = openPopoverId.value === id ? null : id;
}
