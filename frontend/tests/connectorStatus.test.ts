import { describe, expect, it } from "vitest";

import type { ConnectorStatus } from "../src/api";
import { connectorStatus } from "../src/connectorStatus";
import { connector } from "./connectorFixtures";

describe("connectorStatus", () => {
  it.each<[ConnectorStatus, boolean, string, string, string]>([
    ["available", false, "Not connected", "neutral", "Connect"],
    ["authorizing", false, "Connecting", "neutral", "View"],
    ["connected", false, "Configured", "neutral", "Manage"],
    ["connected", true, "Connected", "ok", "Manage"],
    ["missing_scopes", false, "Missing permissions", "warn", "Fix"],
    ["degraded", false, "Degraded", "warn", "Fix"],
    ["reconnect_required", false, "Reconnect required", "warn", "Fix"],
    ["failed", false, "Connection failed", "error", "Fix"],
  ])("labels %s (verified: %s) as %s", (status, verified, label, tone, action) => {
    expect(connectorStatus(connector({ id: "gmail", title: "Gmail", status, verified }))).toEqual({
      label,
      tone,
      action,
    });
  });

  it("offers setup rather than sign-in for an unconfigured Apollo key", () => {
    expect(connectorStatus(connector({ id: "apollo", title: "Apollo" })).action).toBe("Set up");
  });

  it("never reads a saved configuration as Connected", () => {
    const saved = connector({ id: "apollo", title: "Apollo", status: "connected" });
    expect(connectorStatus(saved).label).toBe("Configured");
  });
});
