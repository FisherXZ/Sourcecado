import type { ReactNode } from "react";

/**
 * 16px line icons for the inspector panel. They match the process-layer icons
 * (same box, stroke and `currentColor`) and stay out of the accessibility
 * tree: every control that uses one also has an accessible name.
 */
const ICONS = {
  close: <path d="m4 4 8 8M12 4l-8 8" />,
  /** A retained source: a link. */
  source: (
    <path d="M6.8 9.2a2.5 2.5 0 0 0 3.5 0l2.2-2.2a2.5 2.5 0 0 0-3.5-3.5l-.7.7M9.2 6.8a2.5 2.5 0 0 0-3.5 0L3.5 9a2.5 2.5 0 0 0 3.5 3.5l.7-.7" />
  ),
  /** A generated artifact: a page with lines. */
  artifact: (
    <>
      <path d="M4 2.5h5l3 3v8H4z" />
      <path d="M9 2.5v3h3M6 8.5h4M6 11h3" />
    </>
  ),
  detail: (
    <>
      <circle cx="8" cy="8" r="5.5" />
      <path d="M8 7.4v3.4M8 5.2v.1" />
    </>
  ),
  external: (
    <path d="M9 3h4v4M13 3 7.5 8.5M11 9.5V12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h2.5" />
  ),
  progress: <path d="M3 4.5h.01M3 8h.01M3 11.5h.01M6 4.5h7M6 8h7M6 11.5h7" />,
  output: <path d="M8 2.5v7M5.2 6.8 8 9.6l2.8-2.8M3 11v1.5a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1V11" />,
  context: (
    <>
      <path d="M3 5h5M12 5h1M3 11h1M8 11h5" />
      <circle cx="10" cy="5" r="1.5" />
      <circle cx="6" cy="11" r="1.5" />
    </>
  ),
  card: (
    <>
      <rect x="2.5" y="3" width="11" height="10" rx="1.5" />
      <path d="M2.5 6.5h11" />
    </>
  ),
  raw: (
    <path d="M6 3c-1.5 0-2 .6-2 2v1.5c0 .8-.5 1.5-1.5 1.5.9 0 1.5.7 1.5 1.5V11c0 1.4.5 2 2 2M10 3c1.5 0 2 .6 2 2v1.5c0 .8.5 1.5 1.5 1.5-.9 0-1.5.7-1.5 1.5V11c0 1.4-.5 2-2 2" />
  ),
} as const satisfies Record<string, ReactNode>;

export type InspectorIconName = keyof typeof ICONS;

export function InspectorIcon({ name }: { readonly name: InspectorIconName }) {
  return (
    <svg
      className={`sourcecado-process-icon sourcecado-inspector-icon-${name}`}
      viewBox="0 0 16 16"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {ICONS[name]}
    </svg>
  );
}
