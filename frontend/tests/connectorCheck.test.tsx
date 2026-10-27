import { act, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Connector } from "../src/api";
import { ConnectionsPage } from "../src/routes/ConnectionsPage";
import { SettingsPage } from "../src/routes/SettingsPage";
import { connector } from "./connectorFixtures";

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

// What the saved token implies, before any live check.
const granolaSaved = connector({
  id: "granola",
  title: "Granola",
  status: "connected",
  catalogGroup: "connected",
  health: { category: "healthy", label: "Ready", message: "This connection is ready to use." },
  recovery: null,
  verified: true,
  availableActions: ["disconnect"],
});

// What the live check found: the token is saved but dead.
const granolaDead = connector({
  ...granolaSaved,
  status: "reconnect_required",
  verified: false,
  health: {
    category: "error",
    label: "Reconnect required",
    message: "Granola no longer accepts the saved sign-in. Reconnect to restore access.",
  },
  recovery: {
    category: "reconnect",
    actionLabel: "Reconnect",
    message: "Reconnect, then return to this page.",
  },
  availableActions: ["reconnect", "disconnect"],
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function rowLabel(title: string): string | null | undefined {
  return screen
    .getByRole("link", { name: new RegExp(`^${title}, `) })
    .querySelector(".connection-status")?.textContent;
}

describe("live connector check", () => {
  beforeEach(() => {
    window.location.hash = "#/connections";
    for (const fn of Object.values(api)) fn.mockReset();
    api.getConnectors.mockResolvedValue({ connectors: [granolaSaved] });
    api.getSettings.mockResolvedValue({
      persona: { id: "sourcing", name: "Sourcecado Sourcing Agent" },
      model: null,
      gmail: { connected: false, email: null },
      apollo: { configured: false },
    });
  });

  it("replaces a saved-but-dead Granola token with the check result on both screens", async () => {
    api.checkConnectors.mockResolvedValue({ connectors: [granolaDead] });

    const connections = render(<ConnectionsPage />);
    await waitFor(() => expect(rowLabel("Granola")).toBe("Reconnect required"));
    connections.unmount();

    render(<SettingsPage />);
    const list = await screen.findByRole("list", { name: "Connector status" });
    await waitFor(() =>
      expect(within(list).getByText("Reconnect required")).toBeInTheDocument(),
    );
    expect(within(list).getByRole("link", { name: "Fix Granola" })).toHaveAttribute(
      "href",
      "#/connections/granola",
    );
  });

  it("explains the failure and offers Reconnect on the detail page", async () => {
    window.location.hash = "#/connections/granola";
    api.checkConnectors.mockResolvedValue({ connectors: [granolaDead] });

    render(<ConnectionsPage connectorId="granola" />);
    const detail = await screen.findByRole("region", { name: "Granola connection details" });

    await waitFor(() =>
      expect(within(detail).getAllByText(granolaDead.health.message)).not.toHaveLength(0),
    );
    expect(within(detail).getByRole("button", { name: "Reconnect Granola" })).toBeInTheDocument();
  });

  it("keeps the saved-credential view when the check request fails", async () => {
    const check = deferred<{ connectors: Connector[] }>();
    api.checkConnectors.mockReturnValue(check.promise.then(() => Promise.reject(new Error("offline"))));

    render(<ConnectionsPage />);
    await screen.findByRole("region", { name: "Connected connections" });
    await act(async () => {
      check.resolve({ connectors: [] });
      await check.promise.catch(() => {});
    });

    expect(rowLabel("Granola")).toBe("Connected");
  });

  it("ignores an older check that finishes after a newer load", async () => {
    const first = deferred<{ connectors: Connector[] }>();
    const second = deferred<{ connectors: Connector[] }>();
    api.checkConnectors.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);

    render(<ConnectionsPage />);
    await screen.findByRole("region", { name: "Connected connections" });
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
    });
    await waitFor(() => expect(api.checkConnectors).toHaveBeenCalledTimes(2));

    await act(async () => {
      second.resolve({ connectors: [granolaSaved] });
      await second.promise;
    });
    await act(async () => {
      first.resolve({ connectors: [granolaDead] });
      await first.promise;
    });

    expect(rowLabel("Granola")).toBe("Connected");
  });
});
