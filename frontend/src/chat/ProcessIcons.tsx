import type { ReactNode } from "react";

import type { StepState } from "./processState";
import type { ToolIconKey } from "./toolRegistry";

/**
 * 16px line icons for the process layer. They draw with `currentColor`, so
 * the Warm Operator tokens set their color. They are decoration only: every
 * row also says its state in words, and the icons stay out of the
 * accessibility tree.
 */
function Icon({
  children,
  className,
}: {
  readonly children: ReactNode;
  readonly className?: string;
}) {
  return (
    <svg
      className={`sourcecado-process-icon${className ? ` ${className}` : ""}`}
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
      {children}
    </svg>
  );
}

const TOOL_ICONS: Readonly<Record<ToolIconKey, ReactNode>> = {
  apollo: (
    <>
      <circle cx="7" cy="7" r="4.5" />
      <path d="m10.5 10.5 3 3" />
    </>
  ),
  gmail: (
    <>
      <rect x="2" y="3.5" width="12" height="9" rx="1.5" />
      <path d="m2.5 4.8 5.5 4.4 5.5-4.4" />
    </>
  ),
  drive: (
    <path d="M2 4.5a1 1 0 0 1 1-1h3l1.5 1.5H13a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1z" />
  ),
  calendar: (
    <>
      <rect x="2.5" y="3.5" width="11" height="10" rx="1.5" />
      <path d="M2.5 6.5h11M5.5 2v3M10.5 2v3" />
    </>
  ),
  granola: (
    <>
      <rect x="3.5" y="2.5" width="9" height="11" rx="1.5" />
      <path d="M6 6h4M6 8.5h4M6 11h2.5" />
    </>
  ),
  person: (
    <>
      <circle cx="8" cy="5.5" r="2.5" />
      <path d="M3 13.5c.6-2.6 2.6-4 5-4s4.4 1.4 5 4" />
    </>
  ),
  file: (
    <>
      <path d="M4 2.5h5l3 3v8H4z" />
      <path d="M9 2.5v3h3" />
    </>
  ),
  terminal: (
    <>
      <rect x="2" y="3" width="12" height="10" rx="1.5" />
      <path d="m5 6.5 2 1.5-2 1.5M8.5 10.5H11" />
    </>
  ),
  web: (
    <>
      <circle cx="8" cy="8" r="5.5" />
      <path d="M2.5 8h11M8 2.5c1.7 1.6 2.6 3.4 2.6 5.5S9.7 11.9 8 13.5C6.3 11.9 5.4 10.1 5.4 8S6.3 4.1 8 2.5z" />
    </>
  ),
  clock: (
    <>
      <circle cx="8" cy="8" r="5.5" />
      <path d="M8 5v3.2l2 1.3" />
    </>
  ),
  tool: <path d="M8.8 2 4.5 8.8h3.2L7.2 14l4.3-6.8H8.3z" />,
};

export function ToolIcon({ name }: { readonly name: ToolIconKey }) {
  return <Icon className="sourcecado-tool-icon">{TOOL_ICONS[name]}</Icon>;
}

const STATUS_ICONS: Readonly<Record<StepState, ReactNode>> = {
  running: (
    <>
      <circle cx="8" cy="8" r="5.5" opacity=".25" />
      <path d="M8 2.5A5.5 5.5 0 0 1 13.5 8" />
    </>
  ),
  waiting: (
    <>
      <circle cx="8" cy="8" r="5.5" />
      <path d="M8 5v3.2l2 1.3" />
    </>
  ),
  done: <path d="m3.5 8.5 3 3 6-6.5" />,
  partial: (
    <>
      <path d="M8 2.8 14 13H2z" />
      <path d="M8 6.8v2.8M8 11.6v.1" />
    </>
  ),
  failed: <path d="m4 4 8 8M12 4l-8 8" />,
  blocked: (
    <>
      <rect x="3.5" y="7" width="9" height="6.5" rx="1.5" />
      <path d="M5.5 7V5.5a2.5 2.5 0 0 1 5 0V7" />
    </>
  ),
  denied: (
    <>
      <circle cx="8" cy="8" r="5.5" />
      <path d="m4.2 4.2 7.6 7.6" />
    </>
  ),
  interrupted: (
    <>
      <circle cx="8" cy="8" r="5.5" />
      <path d="M6.6 6v4M9.4 6v4" />
    </>
  ),
  unknown: (
    <>
      <circle cx="8" cy="8" r="5.5" />
      <path d="M6.5 6.4a1.6 1.6 0 1 1 2.4 1.4c-.6.4-.9.8-.9 1.5M8 11.4v.1" />
    </>
  ),
};

export function StatusIcon({ state }: { readonly state: StepState }) {
  return (
    <Icon className={`sourcecado-status-icon sourcecado-status-icon-${state}`}>
      {STATUS_ICONS[state]}
    </Icon>
  );
}

const ACTION_ICONS = {
  chevron: <path d="m6 4 4 4-4 4" />,
  inspect: (
    <>
      <path d="M1.8 8S4 3.8 8 3.8 14.2 8 14.2 8 12 12.2 8 12.2 1.8 8 1.8 8z" />
      <circle cx="8" cy="8" r="1.8" />
    </>
  ),
  search: (
    <>
      <circle cx="7" cy="7" r="4.5" />
      <path d="m10.5 10.5 3 3" />
    </>
  ),
  retry: <path d="M13 8a5 5 0 1 1-1.6-3.7M13 2.5v3h-3" />,
  repair: (
    <path d="M9.5 3a3.5 3.5 0 0 0-3.1 5L3 11.4a1.2 1.2 0 0 0 1.7 1.7L8 9.6A3.5 3.5 0 0 0 13 6.5L11 8.3 9.3 7.7 8.7 6z" />
  ),
  continue: <path d="M3 8h9M8.5 4.5 12 8l-3.5 3.5" />,
  details: (
    <>
      <circle cx="8" cy="8" r="5.5" />
      <path d="M8 7.4v3.4M8 5.2v.1" />
    </>
  ),
} as const;

export function ActionIcon({ name }: { readonly name: keyof typeof ACTION_ICONS }) {
  return <Icon className={`sourcecado-action-icon-${name}`}>{ACTION_ICONS[name]}</Icon>;
}
