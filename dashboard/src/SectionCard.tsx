import type { JSX } from "preact";

import type { IconName } from "./icons";
import { Icon } from "./icons";

/** Shared verdict vocabulary for `data-tone` across the stage-body grammar (design §2). */
export type SectionTone = "bad" | "warn" | "good" | "neutral";

export interface SectionCardProps {
  /** Rendered uppercase via `.microlabel` -- the card's section name. */
  title: string;
  icon?: IconName;
  /** Sets a 3px left border only (design §2.1) -- never a fill. */
  tone?: SectionTone;
  /** Right-aligned header slot: a status chip, an ⓘ, a quiet disclosure toggle. */
  headerActions?: JSX.Element;
  children: JSX.Element | JSX.Element[] | string;
  footer?: JSX.Element;
  /**
   * Only needed when two cards in the same stage share a title -- the
   * `.microlabel` header is a heading in appearance only (design §2.1); no
   * `<h3>`/`<h4>` is ever introduced here, so nothing else labels the card.
   */
  ariaLabel?: string;
}

/**
 * The stage-body grammar's container primitive (design §2.1). Every stage
 * interior is 2-4 of these in a fixed scan order: status -> data ->
 * configuration -> action. Never a disclosure -- no `aria-expanded` is ever
 * rendered here, so a card can sit inside a rail's own disclosure chain
 * without nesting one.
 */
export function SectionCard({
  title,
  icon,
  tone,
  headerActions,
  children,
  footer,
  ariaLabel,
}: SectionCardProps): JSX.Element {
  return (
    <section class="section-card" data-tone={tone} aria-label={ariaLabel}>
      <header class="section-card-head">
        {icon ? <Icon name={icon} size={16} /> : null}
        <span class="microlabel">{title}</span>
        {headerActions ? <span class="section-card-head-actions">{headerActions}</span> : null}
      </header>
      <div class="section-card-body">{children}</div>
      {footer ? <footer class="section-card-actions">{footer}</footer> : null}
    </section>
  );
}
