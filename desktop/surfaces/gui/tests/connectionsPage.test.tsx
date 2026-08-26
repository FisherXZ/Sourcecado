import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Connector } from "../src/api";
import { ConnectionsPage } from "../src/routes/ConnectionsPage";

const api = vi.hoisted(() => ({
  connectConnector: vi.fn(),
  disconnectConnector: vi.fn(),
  getConnectors: vi.fn(),
}));

vi.mock("../src/api", async () => {
  const actual = await vi.importActual<typeof import("../src/api")>("../src/api");
  return {
    ...actual,
    connectConnector: api.connectConnector,
    disconnectConnector: api.disconnectConnector,
    getConnectors: api.getConnectors,
  };
});

function connector(overrides: Partial<Connector> & Pick<Connector, "id" | "title">): Connector {
  return {
    id: overrides.id,
    title: overrides.title,
    description: `${overrides.title} source connection.`,
    status: "available",
    catalogGroup: "available",
    email: null,
    requiredScopes: [],
    missingScopes: [],
    health: {
      category: "setup_required",
      label: "Available",
      message: "This connection has not been set up yet.",
    },
    recovery: {
      category: "connect",
      actionLabel: "Connect",
      message: "Start authorization, then return to this detail page.",
    },
    supportedActions: [],
    availableActions: ["connect"],
    repairRoute: `#/connections/${overrides.id}`,
    authorizationGroup: overrides.id === "granola" ? "granola" : null,
    ...overrides,
  };
}

const gmailAvailable = connector({
  id: "gmail",
  title: "Gmail",
  description: "Search email and create review-only drafts.",
  requiredScopes: ["Read Gmail messages", "Create Gmail drafts"],
  missingScopes: ["Read Gmail messages", "Create Gmail drafts"],
  supportedActions: ["Search and read email", "Create drafts for review"],
  authorizationGroup: "google",
});

const driveConnected = connector({
  id: "drive",
  title: "Google Drive",
  description: "Search and read Drive files.",
  status: "connected",
  catalogGroup: "connected",
  email: "operator@example.com",
  health: { category: "healthy", label: "Ready", message: "This connection is ready to use." },
  recovery: null,
  supportedActions: ["Search files", "Read files"],
  availableActions: ["disconnect"],
  authorizationGroup: "google",
});

const granolaAvailable = connector({
  id: "granola",
  title: "Granola",
  description: "Search meeting notes and context.",
  supportedActions: ["Search meeting notes"],
  authorizationGroup: "granola",
});

describe("ConnectionsPage", () => {
  beforeEach(() => {
    api.connectConnector.mockReset();
    api.disconnectConnector.mockReset();
    api.getConnectors.mockReset();
    window.location.hash = "#/connections";
  });

  it("renders stable connected and available skeleton rows while loading", () => {
    api.getConnectors.mockReturnValue(new Promise(() => {}));

    const { container } = render(<ConnectionsPage />);

    expect(screen.getByRole("heading", { level: 1, name: "Connections" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Loading connections");
    expect(container.querySelectorAll(".connection-skeleton-row")).toHaveLength(5);
    expect(screen.getByText("Connected")).toBeInTheDocument();
    expect(screen.getByText("Available")).toBeInTheDocument();
  });

  it("groups connected and attention-needed accounts before available connectors", async () => {
    const calendarMissing = connector({
      id: "calendar",
      title: "Google Calendar",
      status: "missing_scopes",
      catalogGroup: "connected",
      email: "operator@example.com",
      missingScopes: ["View and update calendar events"],
      health: {
        category: "attention",
        label: "Missing permissions",
        message: "Additional permission is required.",
      },
      recovery: {
        category: "grant_scopes",
        actionLabel: "Finish setup",
        message: "Reconnect and approve the listed permissions.",
      },
      availableActions: ["reconnect", "disconnect"],
      authorizationGroup: "google",
    });
    const apolloDegraded = connector({
      id: "apollo",
      title: "Apollo",
      status: "degraded",
      catalogGroup: "connected",
      health: {
        category: "attention",
        label: "Degraded",
        message: "Some Apollo requests are unavailable.",
      },
      recovery: {
        category: "retry",
        actionLabel: "Reconnect",
        message: "Reconnect Apollo to restore access.",
      },
      availableActions: ["view_guidance"],
    });
    api.getConnectors.mockResolvedValue({
      connectors: [gmailAvailable, driveConnected, granolaAvailable, calendarMissing, apolloDegraded],
    });

    render(<ConnectionsPage />);

    const connected = await screen.findByRole("region", { name: "Connected connections" });
    const available = screen.getByRole("region", { name: "Available connections" });
    expect(within(connected).getAllByRole("link").map((link) => link.textContent)).toEqual([
      expect.stringContaining("Google Drive"),
      expect.stringContaining("Google Calendar"),
      expect.stringContaining("Apollo"),
    ]);
    expect(within(connected).getByText("Missing permissions")).toBeInTheDocument();
    expect(within(connected).getByText("Degraded")).toBeInTheDocument();
    expect(within(available).getAllByRole("link").map((link) => link.textContent)).toEqual([
      expect.stringContaining("Gmail"),
      expect.stringContaining("Granola"),
    ]);
  });

  it("searches connector identity and account, then clears an empty result", async () => {
    api.getConnectors.mockResolvedValue({ connectors: [gmailAvailable, driveConnected, granolaAvailable] });

    render(<ConnectionsPage />);

    const search = await screen.findByRole("searchbox", { name: "Search connections" });
    fireEvent.change(search, { target: { value: "operator@example.com" } });
    expect(screen.getByRole("link", { name: /Google Drive/ })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Gmail/ })).not.toBeInTheDocument();

    fireEvent.change(search, { target: { value: "nothing here" } });
    expect(screen.getByRole("heading", { name: "No connectors match" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear search" }));

    expect(search).toHaveValue("");
    expect(screen.getByRole("link", { name: /Gmail/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Granola/ })).toBeInTheDocument();
  });

  it("loads a refresh-safe detail with account, health, scopes, actions, and a Back path", async () => {
    const gmailMissing = connector({
      ...gmailAvailable,
      status: "missing_scopes",
      catalogGroup: "connected",
      email: "operator@example.com",
      missingScopes: ["Read Gmail messages"],
      health: {
        category: "attention",
        label: "Missing permissions",
        message: "Additional permission is required.",
      },
      recovery: {
        category: "grant_scopes",
        actionLabel: "Finish setup",
        message: "Reconnect and approve the listed permissions.",
      },
      availableActions: ["reconnect", "disconnect"],
    });
    window.location.hash = "#/connections/gmail";
    api.getConnectors.mockResolvedValue({ connectors: [gmailMissing, driveConnected] });

    const { container } = render(<ConnectionsPage connectorId="gmail" />);

    const detail = await screen.findByRole("region", { name: "Gmail connection details" });
    expect(container.querySelector(".connections-page.has-detail")).toBeInTheDocument();
    expect(within(detail).getByRole("link", { name: "Back to Connections" })).toHaveAttribute(
      "href",
      "#/connections",
    );
    expect(within(detail).getByText("operator@example.com")).toBeInTheDocument();
    expect(within(detail).getAllByText("Missing permissions")).not.toHaveLength(0);
    expect(within(detail).getByText("Read Gmail messages")).toBeInTheDocument();
    expect(within(detail).getByText("Search and read email")).toBeInTheDocument();
    expect(within(detail).getByText("Create drafts for review")).toBeInTheDocument();
    expect(within(detail).getByRole("button", { name: "Finish setup" })).toBeInTheDocument();
  });

  it("refreshes on OAuth return focus without losing the detail hash", async () => {
    const gmailConnected = connector({
      ...gmailAvailable,
      status: "connected",
      catalogGroup: "connected",
      email: "operator@example.com",
      missingScopes: [],
      health: { category: "healthy", label: "Ready", message: "This connection is ready to use." },
      recovery: null,
      availableActions: ["disconnect"],
    });
    window.location.hash = "#/connections/gmail";
    api.getConnectors
      .mockResolvedValueOnce({ connectors: [gmailAvailable] })
      .mockResolvedValueOnce({ connectors: [gmailConnected] });

    render(<ConnectionsPage connectorId="gmail" />);
    const detail = await screen.findByRole("region", { name: "Gmail connection details" });
    expect(within(detail).getAllByText("Available")).not.toHaveLength(0);

    fireEvent.focus(window);

    await waitFor(() => expect(api.getConnectors).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(within(detail).getByText("operator@example.com")).toBeInTheDocument(),
    );
    expect(within(detail).getByText("Ready")).toBeInTheDocument();
    expect(window.location.hash).toBe("#/connections/gmail");
  });

  it("keeps the connect button in place while authorization starts and completes on focus", async () => {
    let resolveConnect!: (value: { url: string; opened: boolean }) => void;
    const connecting = new Promise<{ url: string; opened: boolean }>((resolve) => {
      resolveConnect = resolve;
    });
    const gmailConnected = connector({
      ...gmailAvailable,
      status: "connected",
      catalogGroup: "connected",
      email: "operator@example.com",
      missingScopes: [],
      health: { category: "healthy", label: "Ready", message: "This connection is ready to use." },
      recovery: null,
      availableActions: ["disconnect"],
    });
    window.location.hash = "#/connections/gmail";
    api.getConnectors
      .mockResolvedValueOnce({ connectors: [gmailAvailable] })
      .mockResolvedValueOnce({ connectors: [gmailConnected] });
    api.connectConnector.mockReturnValue(connecting);

    render(<ConnectionsPage connectorId="gmail" />);
    const connect = await screen.findByRole("button", { name: "Connect Gmail" });
    fireEvent.click(connect);

    expect(screen.getByRole("button", { name: "Connecting Gmail" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Connecting Gmail" })).toBeDisabled();
    resolveConnect({ url: "https://provider.example/authorize", opened: true });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Connecting Gmail" })).toHaveAttribute(
        "aria-busy",
        "true",
      ),
    );

    fireEvent.focus(window);

    expect(await screen.findByRole("button", { name: "Disconnect Gmail" })).toBeInTheDocument();
    expect(window.location.hash).toBe("#/connections/gmail");
  });

  it("offers a direct authorization link when the provider window was not opened", async () => {
    window.location.hash = "#/connections/gmail";
    api.getConnectors.mockResolvedValue({ connectors: [gmailAvailable] });
    api.connectConnector.mockResolvedValue({
      url: "https://provider.example/authorize",
      opened: false,
    });

    render(<ConnectionsPage connectorId="gmail" />);
    fireEvent.click(await screen.findByRole("button", { name: "Connect Gmail" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Authorization window didn’t open");
    expect(within(alert).getByRole("link", { name: "Open Gmail authorization" })).toHaveAttribute(
      "href",
      "https://provider.example/authorize",
    );
    expect(screen.getByRole("button", { name: "Connect Gmail" })).toBeEnabled();
  });

  it("turns provider or callback failures into safe concrete recovery", async () => {
    window.location.hash = "#/connections/gmail";
    api.getConnectors.mockResolvedValue({ connectors: [gmailAvailable] });
    api.connectConnector.mockRejectedValue(
      new Error("state mismatch access_token=oauth-secret /Users/operator/.config/club"),
    );

    const { container } = render(<ConnectionsPage connectorId="gmail" />);
    fireEvent.click(await screen.findByRole("button", { name: "Connect Gmail" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("provider or OAuth callback");
    expect(alert).toHaveTextContent("try again");
    expect(container).not.toHaveTextContent("oauth-secret");
    expect(container).not.toHaveTextContent("/Users/operator");
    expect(screen.getByRole("button", { name: "Connect Gmail" })).toBeEnabled();
  });

  it("explains an unchanged OAuth return as a callback or provider recovery", async () => {
    window.location.hash = "#/connections/gmail";
    api.getConnectors.mockResolvedValue({ connectors: [gmailAvailable] });
    api.connectConnector.mockResolvedValue({
      url: "https://provider.example/authorize",
      opened: true,
    });

    render(<ConnectionsPage connectorId="gmail" />);
    fireEvent.click(await screen.findByRole("button", { name: "Connect Gmail" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Connecting Gmail" })).toBeDisabled(),
    );

    fireEvent.focus(window);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("provider or OAuth callback");
    expect(alert).toHaveTextContent("provider redirect URL");
    expect(window.location.hash).toBe("#/connections/gmail");
  });

  it("represents a server-reported authorizing state with the stable disabled action", async () => {
    const gmailAuthorizing = connector({
      ...gmailAvailable,
      status: "authorizing",
      catalogGroup: "connected",
      health: {
        category: "attention",
        label: "Authorization in progress",
        message: "Finish authorization in the provider window.",
      },
    });
    window.location.hash = "#/connections/gmail";
    api.getConnectors.mockResolvedValue({ connectors: [gmailAuthorizing] });

    render(<ConnectionsPage connectorId="gmail" />);

    const action = await screen.findByRole("button", { name: "Connecting Gmail" });
    expect(action).toBeDisabled();
    expect(action).toHaveAttribute("aria-busy", "true");
  });

  it("keeps failed and reconnect-required recovery beside the connector", async () => {
    const gmailReconnect = connector({
      ...gmailAvailable,
      status: "reconnect_required",
      catalogGroup: "connected",
      email: "operator@example.com",
      health: {
        category: "attention",
        label: "Reconnect required",
        message: "Google rejected the saved authorization.",
      },
      recovery: {
        category: "reconnect",
        actionLabel: "Reconnect",
        message: "Reconnect Gmail to restore access.",
      },
      availableActions: ["reconnect", "disconnect"],
    });
    window.location.hash = "#/connections/gmail";
    api.getConnectors.mockResolvedValue({ connectors: [gmailReconnect] });

    render(<ConnectionsPage connectorId="gmail" />);

    const recovery = await screen.findByRole("alert");
    expect(recovery).toHaveTextContent("Reconnect Gmail to restore access");
    expect(screen.getByRole("button", { name: "Reconnect Gmail" })).toBeEnabled();
    expect(screen.getByRole("link", { name: /Gmail, Reconnect required/ })).toBeInTheDocument();
  });

  it("renders a failed connector with a safe reconnect action", async () => {
    const gmailFailed = connector({
      ...gmailAvailable,
      status: "failed",
      catalogGroup: "connected",
      health: {
        category: "attention",
        label: "Connection failed",
        message: "The saved authorization could not be verified.",
      },
      recovery: {
        category: "reconnect",
        actionLabel: "Reconnect",
        message: "Reconnect Gmail to restore access.",
      },
      availableActions: ["reconnect"],
    });
    window.location.hash = "#/connections/gmail";
    api.getConnectors.mockResolvedValue({ connectors: [gmailFailed] });

    render(<ConnectionsPage connectorId="gmail" />);

    expect(await screen.findByRole("link", { name: /Gmail, Connection failed/ })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Reconnect Gmail to restore access");
    expect(screen.getByRole("button", { name: "Reconnect Gmail" })).toBeEnabled();
  });

  it("shows Apollo configuration guidance without exposing a secret editor", async () => {
    const apollo = connector({
      id: "apollo",
      title: "Apollo",
      description: "Search people and enrich sourcing records.",
      recovery: {
        category: "configure",
        actionLabel: "View setup guide",
        message: "Set APOLLO_API_KEY in the local Sourcecado environment, then restart Sourcecado.",
      },
      supportedActions: ["Search people", "Enrich contacts"],
      availableActions: ["view_guidance"],
    });
    window.location.hash = "#/connections/apollo";
    api.getConnectors.mockResolvedValue({ connectors: [apollo] });

    const { container } = render(<ConnectionsPage connectorId="apollo" />);

    const detail = await screen.findByRole("region", { name: "Apollo connection details" });
    expect(within(detail).getByText("How to configure Apollo")).toBeInTheDocument();
    expect(within(detail).getByText(/does not display or edit API keys/)).toBeInTheDocument();
    expect(within(detail).queryByRole("textbox")).not.toBeInTheDocument();
    expect(container).not.toHaveTextContent("sk-");
  });

  it("requires explicit all-three confirmation before disconnecting shared Google access", async () => {
    const gmailConnected = connector({
      ...gmailAvailable,
      status: "connected",
      catalogGroup: "connected",
      email: "operator@example.com",
      missingScopes: [],
      health: { category: "healthy", label: "Ready", message: "This connection is ready to use." },
      recovery: null,
      availableActions: ["disconnect"],
    });
    window.location.hash = "#/connections/gmail";
    api.getConnectors
      .mockResolvedValueOnce({ connectors: [gmailConnected, driveConnected] })
      .mockResolvedValueOnce({ connectors: [gmailAvailable, granolaAvailable] });
    api.disconnectConnector.mockResolvedValue({
      connected: false,
      disconnected: ["gmail", "drive", "calendar"],
    });

    render(<ConnectionsPage connectorId="gmail" />);
    fireEvent.click(await screen.findByRole("button", { name: "Disconnect Gmail" }));

    const confirmation = screen.getByRole("alertdialog", { name: "Disconnect Google account?" });
    expect(confirmation).toHaveTextContent("Gmail, Google Drive, and Google Calendar");
    expect(api.disconnectConnector).not.toHaveBeenCalled();
    fireEvent.click(within(confirmation).getByRole("button", { name: "Disconnect all three" }));

    await waitFor(() => expect(api.disconnectConnector).toHaveBeenCalledWith("gmail"));
    await waitFor(() => expect(api.getConnectors).toHaveBeenCalledTimes(2));
    expect(window.location.hash).toBe("#/connections/gmail");
  });

  it("disconnects Granola without implying that Google access is affected", async () => {
    const granolaConnected = connector({
      ...granolaAvailable,
      status: "connected",
      catalogGroup: "connected",
      health: { category: "healthy", label: "Ready", message: "This connection is ready to use." },
      recovery: null,
      availableActions: ["disconnect"],
    });
    window.location.hash = "#/connections/granola";
    api.getConnectors
      .mockResolvedValueOnce({ connectors: [granolaConnected] })
      .mockResolvedValueOnce({ connectors: [granolaAvailable] });
    api.disconnectConnector.mockResolvedValue({ connected: false, disconnected: ["granola"] });

    render(<ConnectionsPage connectorId="granola" />);
    fireEvent.click(await screen.findByRole("button", { name: "Disconnect Granola" }));

    await waitFor(() => expect(api.disconnectConnector).toHaveBeenCalledWith("granola"));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Connect Granola" })).toBeInTheDocument();
  });

  it("shows page retry and unknown-detail recovery without leaking fetch errors", async () => {
    api.getConnectors.mockRejectedValueOnce(new Error("token=sidecar-secret /private/state"));
    const { container, rerender } = render(<ConnectionsPage />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Connections couldn’t be loaded");
    expect(container).not.toHaveTextContent("sidecar-secret");
    api.getConnectors.mockResolvedValueOnce({ connectors: [gmailAvailable] });
    fireEvent.click(within(alert).getByRole("button", { name: "Retry loading connections" }));
    expect(await screen.findByRole("link", { name: /Gmail/ })).toBeInTheDocument();

    window.location.hash = "#/connections/unknown";
    rerender(<ConnectionsPage connectorId="unknown" />);
    expect(screen.getByRole("heading", { name: "Connection not found" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to Connections" })).toHaveAttribute(
      "href",
      "#/connections",
    );
  });
});
