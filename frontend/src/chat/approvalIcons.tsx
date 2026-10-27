export type ApprovalIconName =
  | "mail-send"
  | "mail-draft"
  | "mail"
  | "user"
  | "text"
  | "message"
  | "thread"
  | "hash"
  | "sparkles"
  | "search"
  | "list"
  | "coins"
  | "flag"
  | "calendar"
  | "trash"
  | "terminal"
  | "bolt"
  | "folder"
  | "building"
  | "file"
  | "check"
  | "x"
  | "clock"
  | "minus"
  | "alert"
  | "chevron-down"
  | "chevron-right"
  | "shield";

// 16px stroke icons on a 16x16 grid. Paths only: color comes from the text.
const PATHS: Readonly<Record<ApprovalIconName, string>> = {
  "mail-send": "M2 4.5h9v7H2zM2 5l4.5 3.5L11 5M12.5 6.5l2 1.5-2 1.5",
  "mail-draft": "M2 4h12v8H2zM2 4.5l6 4.5 6-4.5",
  mail: "M2 4h12v8H2zM2 4.5l6 4.5 6-4.5",
  user: "M8 8a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zM3.5 13a4.5 4.5 0 0 1 9 0",
  text: "M3 4.5h10M3 8h10M3 11.5h6",
  message: "M2.5 3.5h11v7H8l-3 2.5v-2.5H2.5z",
  thread: "M3 3.5h7v4H6l-2 1.5V7.5H3zM7 9.5h6v3h-1v1.5L10.5 12.5H7z",
  hash: "M6 3 5 13M11 3l-1 10M3 6h10M2.5 10h10",
  sparkles: "M7 3l1.2 3.3L11.5 7.5 8.2 8.7 7 12 5.8 8.7 2.5 7.5l3.3-1.2zM12 2.5l.5 1.3 1.3.5-1.3.5-.5 1.3-.5-1.3-1.3-.5 1.3-.5z",
  search: "M7 11.5a4.5 4.5 0 1 0 0-9 4.5 4.5 0 0 0 0 9zM10.5 10.5l3 3",
  list: "M5.5 4.5H13M5.5 8H13M5.5 11.5H13M3 4.5h.01M3 8h.01M3 11.5h.01",
  coins: "M8 7.5c2.5 0 4.5-.9 4.5-2S10.5 3.5 8 3.5 3.5 4.4 3.5 5.5s2 2 4.5 2zM3.5 5.5v2.5c0 1.1 2 2 4.5 2s4.5-.9 4.5-2V5.5M3.5 8v2.5c0 1.1 2 2 4.5 2s4.5-.9 4.5-2V8",
  flag: "M4 13.5v-11M4 3h8l-1.8 2.5L12 8H4",
  calendar: "M3 4.5h10v8.5H3zM3 7h10M5.5 2.5v3M10.5 2.5v3",
  trash: "M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8.5h5.8l.6-8.5M7 7v4M9 7v4",
  terminal: "M2.5 3.5h11v9h-11zM5 6.5l2 1.5-2 1.5M8.5 10h2.5",
  bolt: "M9 2.5 4.5 9H8l-1 4.5L11.5 7H8z",
  folder: "M2.5 4.5h4l1.2 1.5h5.8v6.5h-11z",
  building: "M4 13.5v-10h6v10M10 7h2v6.5M6 5.5h2M6 8h2M6 10.5h2M2.5 13.5h11",
  file: "M4 2.5h5l3 3v8H4zM9 2.5v3h3",
  check: "M3.5 8.5l3 3 6-7",
  x: "M4 4l8 8M12 4l-8 8",
  clock: "M8 13.5a5.5 5.5 0 1 0 0-11 5.5 5.5 0 0 0 0 11zM8 5v3.2l2 1.3",
  minus: "M4 8h8",
  alert: "M8 2.5l6 10.5H2zM8 6.5v3M8 11.2h.01",
  "chevron-down": "M4 6l4 4 4-4",
  "chevron-right": "M6 4l4 4-4 4",
  shield: "M8 2l5 2v4c0 3-2.2 5-5 6-2.8-1-5-3-5-6V4z",
};

export function ApprovalIcon({
  name,
  className,
}: {
  readonly name: ApprovalIconName;
  readonly className?: string;
}) {
  return (
    <svg
      className={`sourcecado-approval-icon${className ? ` ${className}` : ""}`}
      viewBox="0 0 16 16"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d={PATHS[name]} />
    </svg>
  );
}
