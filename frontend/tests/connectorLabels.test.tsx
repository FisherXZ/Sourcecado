import { cleanup, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConnectionsPage } from "../src/routes/ConnectionsPage";
import { SettingsPage } from "../src/routes/SettingsPage";
import {
  configuredUnverifiedApollo,
  reportedErrorGranola,
  unconfiguredGmail,
  verifiedDrive,
} from "./connectorFixtures";

const api = vi.hoisted(() => ({
  checkConnectors: vi.fn(),
  connectConnector: vi.fn(),
  disconnectConnector: vi.fn(),
  getConnectors: vi.fn(),
  getSettings: vi.fn(),
}));

vi.mock("../src/api", async () => {
  const actual = await vi.importActual<typeof import("../src/api")>("../src/api");
  return { ...actual, ...api };
});

const connectors = [unconfiguredGmail, configuredUnverifiedApollo, reportedErrorGranola, verifiedDrive];

const expected = [
  { title: "Gmail", label: "Not connected", action: "Connect", href: "#/connections/gmail" },
  { title: "Apollo", label: "Configured", action: "Manage", href: "#/connections/apollo" },
  { title: "Granola", label: "Connection failed", action: "Fix", href: "#/connections/granola" },
  { title: "Google Drive", label: "Connected", action: "Manage", href: "#/connections/drive" },
];

async function connectionsLabels(): Promise<Record<string, string>> {
  render(<ConnectionsPage />);
  await screen.findByRole("region", { name: "Connected connections" });
  const labels: Record<string, string> = {};
  for (const { title } of expected) {
    const row = screen.getByRole("link", { name: new RegExp(`^${title}, `) });
    labels[title] = row.querySelector(".connection-status")?.textContent ?? "";
  }
  cleanup();
  return labels;
}

async function settingsLabels(): Promise<Record<string, string>> {
  render(<SettingsPage />);
  const list = await screen.findByRole("list", { name: "Connector status" });
  const labels: Record<string, string> = {};
  for (const item of within(list).getAllByRole("listitem")) {
    const title = item.firstElementChild?.textContent ?? "";
    labels[title] = item.querySelector(".connection-status")?.textContent ?? "";
  }
  cleanup();
  return labels;
}

describe("connector labels across Connections and Settings", () => {
  beforeEach(() => {
    window.location.hash = "#/connections";
    api.getConnectors.mockReset();
    api.getConnectors.mockResolvedValue({ connectors });
    api.checkConnectors.mockReset();
    api.checkConnectors.mockResolvedValue({ connectors });
    api.getSettings.mockReset();
    api.getSettings.mockResolvedValue({
      persona: { id: "sourcing", name: "Sourcecado Sourcing Agent" },
      model: null,
      gmail: { connected: false, email: null },
      apollo: { configured: true },
    });
  });

  it("gives the same connector response the same label on both screens", async () => {
    const onConnections = await connectionsLabels();
    const onSettings = await settingsLabels();

    expect(onSettings).toEqual(onConnections);
    for (const { title, label } of expected) {
      expect(onConnections[title]).toBe(label);
    }
  });

  it("shows the same label in each connector's detail header", async () => {
    for (const { title, label, href } of expected) {
      window.location.hash = href;
      render(<ConnectionsPage connectorId={href.slice("#/connections/".length)} />);
      const detail = await screen.findByRole("region", { name: `${title} connection details` });
      expect(detail.querySelector(".connection-detail-header .connection-status")).toHaveTextContent(
        label,
      );
      cleanup();
    }
  });

  it("links each Settings row to the existing Connect or Fix flow", async () => {
    render(<SettingsPage />);
    const list = await screen.findByRole("list", { name: "Connector status" });

    for (const { title, action, href } of expected) {
      expect(within(list).getByRole("link", { name: `${action} ${title}` })).toHaveAttribute(
        "href",
        href,
      );
    }
  });
});
