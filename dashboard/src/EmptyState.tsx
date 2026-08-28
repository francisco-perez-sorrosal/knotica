import type { JSX } from "preact";

import type { IconName } from "./icons";
import { Icon } from "./icons";

/**
 * The centred icon/title/sentence/one-action template used for every empty,
 * error, and zero state (design §3.2/§3.5) -- replacing the bare
 * `<p>Nothing needs you.</p>` and `<aside role="alert">` patterns that
 * currently carry no icon, no hierarchy, and no route back to a fix.
 *
 * `children` is an optional slot between the sentence and the action --
 * the server-unreachable state uses it for a `<CopyBlock>` carrying the
 * remediation command. `action` stays a single element by convention: one
 * action, never a row of competing ones.
 */
export function EmptyState({
  icon,
  title,
  sentence,
  action,
  children,
}: {
  icon: IconName;
  title: string;
  sentence: string;
  action?: JSX.Element;
  children?: JSX.Element | JSX.Element[] | null;
}): JSX.Element {
  return (
    <div class="empty-state">
      <div class="empty-state-icon">
        <Icon name={icon} size={24} />
      </div>
      <p class="empty-state-title">{title}</p>
      <p class="empty-state-sentence">{sentence}</p>
      {children ? <div class="empty-state-extra">{children}</div> : null}
      {action ? <div class="empty-state-action">{action}</div> : null}
    </div>
  );
}
