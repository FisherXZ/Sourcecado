import type { ReactNode } from "react";

/**
 * Line icons for the composer, its menus and the queue.
 *
 * One stroke weight and one 20px grid, so a button's icon never has to be
 * restyled where it is used. Every icon is decorative: the button that holds
 * it carries the accessible name.
 */
function Icon({ children }: { readonly children: ReactNode }) {
  return (
    <svg
      className="sourcecado-icon"
      viewBox="0 0 20 20"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  );
}

export const PlusIcon = () => (
  <Icon>
    <path d="M10 4.5v11M4.5 10h11" />
  </Icon>
);

export const CloseIcon = () => (
  <Icon>
    <path d="M5.5 5.5l9 9M14.5 5.5l-9 9" />
  </Icon>
);

export const SendIcon = () => (
  <Icon>
    <path d="M10 15.5v-11M5.5 9 10 4.5 14.5 9" />
  </Icon>
);

export const ArrowUpIcon = SendIcon;

export const ArrowDownIcon = () => (
  <Icon>
    <path d="M10 4.5v11M5.5 11 10 15.5 14.5 11" />
  </Icon>
);

export const PersonIcon = () => (
  <Icon>
    <circle cx="10" cy="7" r="3" />
    <path d="M4.5 16c.6-2.8 2.7-4.3 5.5-4.3s4.9 1.5 5.5 4.3" />
  </Icon>
);

export const FileIcon = () => (
  <Icon>
    <path d="M6 3.5h5.5L15 7v9.5H6z" />
    <path d="M11.5 3.5V7H15" />
  </Icon>
);

export const SkillIcon = () => (
  <Icon>
    <path d="M10.8 3.5 5.5 11h4l-.8 5.5L14.5 9h-4z" />
  </Icon>
);

export const SearchIcon = () => (
  <Icon>
    <circle cx="9" cy="9" r="4.5" />
    <path d="m12.5 12.5 3.5 3.5" />
  </Icon>
);

export const EditIcon = () => (
  <Icon>
    <path d="M4.5 15.5 5 12l7-7 3 3-7 7z" />
    <path d="m11 6 3 3" />
  </Icon>
);

export const CheckIcon = () => (
  <Icon>
    <path d="m4.5 10.5 3.5 3.5 7.5-8" />
  </Icon>
);

export const RetryIcon = () => (
  <Icon>
    <path d="M15.5 10a5.5 5.5 0 1 1-1.8-4.1" />
    <path d="M15.5 4v3h-3" />
  </Icon>
);

export const PlayIcon = () => (
  <Icon>
    <path d="M7 4.8v10.4L15.5 10z" />
  </Icon>
);

export const TrashIcon = () => (
  <Icon>
    <path d="M4.5 6h11M8 6V4.5h4V6M6 6l.6 9.5h6.8L14 6" />
  </Icon>
);
