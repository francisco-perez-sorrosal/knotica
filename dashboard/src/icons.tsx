import type { JSX } from "preact";

/**
 * The dashboard's icon set -- 26 inline stroke glyphs, CSP-safe by
 * construction (no icon font, no external fetch). Every glyph shares the
 * reference's outline contract: 24x24 viewbox, no fill, `currentColor`
 * stroke, 1.5 stroke width, round caps/joins. An icon is never the sole
 * label -- every call site pairs it with visible text or an `sr-only`
 * string; this module only draws the glyph.
 */

export type IconName =
  | "lane:home"
  | "lane:learn"
  | "lane:answer"
  | "lane:improve"
  | "lane:fill"
  | "lane:tend"
  | "state:pending"
  | "state:active"
  | "state:complete"
  | "state:blocked"
  | "state:unknown"
  | "state:running"
  | "stage:instrument"
  | "stage:observe"
  | "stage:gate"
  | "stage:heal"
  | "stage:promote"
  | "stage:prove"
  | "info"
  | "close"
  | "chevron-right"
  | "copy"
  | "external-link"
  | "plus"
  | "refresh"
  | "search";

/** Inner `<path>`/`<circle>` markup for each glyph, keyed exhaustively by name. */
const GLYPHS: Record<IconName, JSX.Element> = {
  "lane:home": (
    <path d="M4 10.5 12 4l8 6.5V19a1 1 0 0 1-1 1h-4v-6H9v6H5a1 1 0 0 1-1-1z" />
  ),
  "lane:learn": (
    <path d="M4 5.5c2-1 5-1 8 .5v13c-3-1.5-6-1.5-8-.5zM20 5.5c-2-1-5-1-8 .5v13c3-1.5 6-1.5 8-.5z" />
  ),
  "lane:answer": <path d="M4 5h16v10H9l-4 3.5V15H4z M11 9.2h.01 M11 12h.01" />,
  "lane:improve": (
    <path d="M5 12a7 7 0 0 1 12-5M19 4v4h-4 M19 12a7 7 0 0 1-12 5M5 20v-4h4" />
  ),
  "lane:fill": <path d="M4 4h7v7H4z M13 4h7v7h-7z M4 13h7v7H4z" />,
  "lane:tend": (
    <path d="M14.5 6.5a4 4 0 0 1-5.2 4.9L5 15.7 8.3 19l4.3-4.3a4 4 0 0 1 4.9-5.2l-2.6 2.6-2-2z" />
  ),
  "state:pending": <circle cx="12" cy="12" r="7" stroke-dasharray="3 3" />,
  "state:active": (
    <>
      <circle cx="12" cy="12" r="7" />
      <circle cx="12" cy="12" r="2.5" fill="currentColor" stroke="none" />
    </>
  ),
  "state:complete": (
    <>
      <circle cx="12" cy="12" r="7" />
      <path d="m9 12 2 2 4-4" />
    </>
  ),
  "state:blocked": <path d="M12 4 2.5 19.5h19zM12 10v4.5 M12 17.2h.01" />,
  "state:unknown": (
    <>
      <circle cx="12" cy="12" r="7" />
      <path d="M7 17 17 7" />
    </>
  ),
  "state:running": (
    <path d="M5 12a7 7 0 0 1 12-5M19 4v4h-4 M19 12a7 7 0 0 1-12 5M5 20v-4h4" />
  ),
  "stage:instrument": (
    <path d="M4 6h6M4 12h10M4 18h7 M12 4v4 M16 9v6 M13 16v4" />
  ),
  "stage:observe": <path d="M4 18V6 M4 18h16 M6 15l4-5 3 3 5-7" />,
  "stage:gate": (
    <path d="M12 3 5 6v6c0 5 3 8 7 9 4-1 7-4 7-9V6z M9.5 12l1.8 1.8L15 10" />
  ),
  "stage:heal": (
    <path d="M18 3v4M16 5h4 M6 15l3 3-6 3 3-6 M13.5 5.5l5 5-9 9-5-5z" />
  ),
  "stage:promote": (
    <path d="M6 3v12 M6 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4z M6 5a2 2 0 1 0 0-4 2 2 0 0 0 0 4z M18 5a2 2 0 1 0 0-4 2 2 0 0 0 0 4z M18 5v3a4 4 0 0 1-4 4H8" />
  ),
  "stage:prove": (
    <path d="M12 3l7 3v5c0 5-3 8-7 9-4-1-7-4-7-9V6zM9.5 12l1.8 1.8L15 10" />
  ),
  info: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v5.5 M12 7.8h.01" />
    </>
  ),
  close: <path d="M6 6l12 12M18 6 6 18" />,
  "chevron-right": <path d="M9 5l7 7-7 7" />,
  copy: (
    <path d="M9 9h9v9H9z M6 15H4.5A1.5 1.5 0 0 1 3 13.5v-9A1.5 1.5 0 0 1 4.5 3h9A1.5 1.5 0 0 1 15 4.5V6" />
  ),
  "external-link": (
    <path d="M9 6H5a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-4 M13 3h7v7 M20 3l-9 9" />
  ),
  plus: <path d="M12 5v14M5 12h14" />,
  refresh: (
    <path d="M4 4v5h5 M20 20v-5h-5 M4.6 15a8 8 0 0 0 14.2 2.4M19.4 9A8 8 0 0 0 5.2 6.6" />
  ),
  search: <path d="M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM21 21l-4.6-4.6" />,
};

const SIZE_PX: Record<16 | 20 | 24, number> = { 16: 16, 20: 20, 24: 24 };

/**
 * Renders one glyph from the inventory. `size` maps to the design's three
 * fixed contexts: 16px inline, 20px in cards/strip nodes, 24px for
 * chrome-scale marks. Always `aria-hidden` -- callers supply the visible
 * text or `sr-only` label the glyph sits beside.
 */
export function Icon({
  name,
  size = 16,
  class: className,
}: {
  name: IconName;
  size?: 16 | 20 | 24;
  class?: string;
}): JSX.Element {
  const px = SIZE_PX[size];
  return (
    <svg
      class={className}
      width={px}
      height={px}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="1.5"
      stroke-linecap="round"
      stroke-linejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {GLYPHS[name]}
    </svg>
  );
}

/**
 * The one busy affordance for the whole dashboard. Reuses the `refresh`
 * glyph rather than adding a 27th -- the inventory above is a census, not a
 * grab bag. `aria-hidden` like every `Icon`: the accessible name is always
 * carried by the text the spinner sits beside, and `aria-busy` on the
 * control is the machine-readable state. Motion is never the sole carrier
 * of anything, so a reduced-motion or ANSI-less reader loses nothing.
 */
export function Spinner({
  size = 16,
}: {
  size?: 16 | 20 | 24;
} = {}): JSX.Element {
  return <Icon name="refresh" size={size} class="spin" />;
}
