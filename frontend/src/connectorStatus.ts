import type { Connector } from "./api";

export type ConnectorStatusTone = "neutral" | "ok" | "warn" | "error";

export type ConnectorStatusView = {
  label: string;
  tone: ConnectorStatusTone;
  /** What the operator can do from a summary row; opens the connector's detail page. */
  action: string;
};

/**
 * The one status vocabulary for every surface that summarizes a connector.
 * Connections and Settings both call this, so the same response can never
 * read differently on the two screens.
 */
export function connectorStatus(connector: Connector): ConnectorStatusView {
  switch (connector.status) {
    case "available":
      return {
        label: "Not connected",
        tone: "neutral",
        action: connector.id === "apollo" ? "Set up" : "Connect",
      };
    case "authorizing":
      return { label: "Connecting", tone: "neutral", action: "View" };
    case "connected":
      // A saved configuration the app has not verified is not "Connected".
      return connector.verified
        ? { label: "Connected", tone: "ok", action: "Manage" }
        : { label: "Configured", tone: "neutral", action: "Manage" };
    case "missing_scopes":
      return { label: "Missing permissions", tone: "warn", action: "Fix" };
    case "degraded":
      return { label: "Degraded", tone: "warn", action: "Fix" };
    case "reconnect_required":
      return { label: "Reconnect required", tone: "warn", action: "Fix" };
    case "failed":
      return { label: "Connection failed", tone: "error", action: "Fix" };
    default: {
      const unhandled: never = connector.status;
      return unhandled;
    }
  }
}
