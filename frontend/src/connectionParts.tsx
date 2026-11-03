/** Parts shared by the Connections catalog, its detail panels and Monid. */
import { Icon, type IconName } from "./icons";

const CONNECTOR_ICONS: Record<string, IconName> = {
  gmail: "draft",
  drive: "folder",
  calendar: "calendar",
  apollo: "people",
  granola: "notes",
  monid: "search",
};

/** A product tile: one glyph on a hairline square, the same for every connector. */
export function ConnectorMark({ id }: { id: string }) {
  return (
    <span className="connection-mark" aria-hidden="true">
      <Icon name={CONNECTOR_ICONS[id] || "link"} />
    </span>
  );
}

export function StatusPill({
  label,
  tone,
  role,
}: {
  label: string;
  tone: string;
  role?: "status";
}) {
  return (
    <span className={`connection-status tone-${tone}`} role={role}>
      {label}
    </span>
  );
}

export function BackToConnections() {
  return (
    <a className="connection-back" href="#/connections" aria-label="Back to Connections">
      <Icon name="back" />
      Connections
    </a>
  );
}
