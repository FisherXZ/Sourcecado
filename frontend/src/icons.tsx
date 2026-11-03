/**
 * Small icons for buttons. Hidden from screen readers; every button has its
 * own text or label.
 */

const PATHS = {
  draft: "M4 5.5h12v9H4zM4 6l6 4.5L16 6",
  reply: "M8 5 3.5 9.5 8 14M4 9.5h7.5a5 5 0 0 1 5 5v.5",
  edit: "M12.5 4.5l3 3L8 15H5v-3zM11 6l3 3",
  back: "M15.5 10h-11M9 5 4 10l5 5",
  search: "M13.5 8.5a5 5 0 1 1-10 0 5 5 0 0 1 10 0zM12.2 12.2l4.3 4.3",
  copy: "M7.5 7.5h8.5v8.5H7.5zM4.5 12.5v-8h8",
  external: "M11 4h5v5M16 4l-7 7M14 11.5V16H4V6h4.5",
  add: "M10 4.5v11M4.5 10h11",
  chat: "M4 4.5h12v8.5H9l-4 3v-3H4z",
  company: "M4.5 16.5V4h7v12.5M11.5 8h4v8.5M3 16.5h14M7 7h2M7 10h2M7 13h2",
  role: "M3.5 7h13v9h-13zM7.5 7V4.5h5V7M3.5 11h13",
  save: "M5 4h8l2 2v10H5zM7.5 4v3.5h5V4M7.5 16v-4.5h5V16",
  refresh: "M15.5 8.5A5.5 5.5 0 0 0 5 7M4.5 11.5A5.5 5.5 0 0 0 15 13M5 3.5V7h3.5M15 16.5V13h-3.5",
  sort: "M6.5 4v12M4 13.5 6.5 16 9 13.5M13.5 16V4M11 6.5 13.5 4 16 6.5",
  previous: "M12 5 7 10l5 5",
  next: "M8 5l5 5-5 5",
  open: "M11 4h5v5M16 4l-7 7M14 11.5V16H4V6h4.5",
  check: "M4.5 10.5 8 14l7.5-8",
  approve: "M10 3.5 15.5 6v4c0 3.2-2.3 5.6-5.5 6.5C6.8 15.6 4.5 13.2 4.5 10V6zM7.5 10l1.8 1.8L12.8 8",
  send: "M3.5 10 16 4l-4 12-2.5-5zM9.5 11 16 4",
  deny: "M5.5 5.5l9 9M14.5 5.5l-9 9",
  close: "M5.5 5.5l9 9M14.5 5.5l-9 9",
  flag: "M5.5 17V3.5M5.5 4.5h9l-2 3.5 2 3.5h-9",
  assistant: "M10 3.5l1.5 4 4 1.5-4 1.5-1.5 4-1.5-4-4-1.5 4-1.5zM15 13.5l.6 1.4 1.4.6-1.4.6-.6 1.4-.6-1.4-1.4-.6 1.4-.6z",
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name }: { readonly name: IconName }) {
  return (
    <svg
      className="action-icon"
      viewBox="0 0 20 20"
      width="16"
      height="16"
      focusable="false"
      aria-hidden="true"
    >
      <path d={PATHS[name]} />
    </svg>
  );
}
