/**
 * Small icons for buttons. Hidden from screen readers; every button has its
 * own text or label.
 */

const PATHS = {
  draft: "M4 5.5h12v9H4zM4 6l6 4.5L16 6",
  reply: "M8 5 3.5 9.5 8 14M4 9.5h7.5a5 5 0 0 1 5 5v.5",
  edit: "M12.5 4.5l3 3L8 15H5v-3zM11 6l3 3",
  save: "M5 4h8l2 2v10H5zM7.5 4v3.5h5V4M7.5 16v-4.5h5V16",
  refresh: "M15.5 8.5A5.5 5.5 0 0 0 5 7M4.5 11.5A5.5 5.5 0 0 0 15 13M5 3.5V7h3.5M15 16.5V13h-3.5",
  approve: "M10 3.5 15.5 6v4c0 3.2-2.3 5.6-5.5 6.5C6.8 15.6 4.5 13.2 4.5 10V6zM7.5 10l1.8 1.8L12.8 8",
  send: "M3.5 10 16 4l-4 12-2.5-5zM9.5 11 16 4",
  deny: "M5.5 5.5l9 9M14.5 5.5l-9 9",
  close: "M5.5 5.5l9 9M14.5 5.5l-9 9",
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
