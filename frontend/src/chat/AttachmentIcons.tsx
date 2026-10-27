import { FileIcon, Icon } from "./icons";
import type { AttachmentKind } from "../api";

/** Line icons for attached files and their preview. Decorative: the control that holds one is named. */
export const ImageIcon = () => (
  <Icon>
    <rect x="3.5" y="4.5" width="13" height="11" rx="2" />
    <circle cx="7.6" cy="8.6" r="1.2" />
    <path d="M4 14.2l3.6-3.6 3 3 2-2 3.4 3.1" />
  </Icon>
);

export const TableIcon = () => (
  <Icon>
    <rect x="3.5" y="4.5" width="13" height="11" rx="1.5" />
    <path d="M3.5 8.5h13M3.5 12h13M8.5 4.5v11" />
  </Icon>
);

export const SpinnerIcon = () => (
  <Icon>
    <circle cx="10" cy="10" r="6" opacity=".25" />
    <path d="M10 4a6 6 0 0 1 6 6" className="sourcecado-attachment-spin" />
  </Icon>
);

export const AlertIcon = () => (
  <Icon>
    <circle cx="10" cy="10" r="6.5" />
    <path d="M10 6.8v4M10 13.2v.1" />
  </Icon>
);

export const ZoomInIcon = () => (
  <Icon>
    <circle cx="9" cy="9" r="4.8" />
    <path d="M12.6 12.6 16 16M9 7v4M7 9h4" />
  </Icon>
);

export const ZoomOutIcon = () => (
  <Icon>
    <circle cx="9" cy="9" r="4.8" />
    <path d="M12.6 12.6 16 16M7 9h4" />
  </Icon>
);

export const FitIcon = () => (
  <Icon>
    <path d="M4 8V4h4M16 8V4h-4M4 12v4h4M16 12v4h-4" />
  </Icon>
);

export function KindIcon({ kind }: { readonly kind: AttachmentKind | undefined }) {
  if (kind === "image") return <ImageIcon />;
  if (kind === "csv") return <TableIcon />;
  return <FileIcon />;
}
