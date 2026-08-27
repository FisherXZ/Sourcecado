import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SettingsPage } from "../src/routes/SettingsPage";

const api = vi.hoisted(() => ({
  createWorkspaceGrant: vi.fn(),
  getConnectors: vi.fn(),
  getSettings: vi.fn(),
  pickDirectory: vi.fn(),
  revokeHostApproval: vi.fn(),
  revokeWorkspaceGrant: vi.fn(),
  setPersona: vi.fn(),
  updateWorkspaceGrant: vi.fn(),
}));

vi.mock("../src/api", () => ({
  createWorkspaceGrant: api.createWorkspaceGrant,
  getConnectors: api.getConnectors,
  getSettings: api.getSettings,
  pickDirectory: api.pickDirectory,
  revokeHostApproval: api.revokeHostApproval,
  revokeWorkspaceGrant: api.revokeWorkspaceGrant,
  setPersona: api.setPersona,
  updateWorkspaceGrant: api.updateWorkspaceGrant,
}));

describe("SettingsPage", () => {
  beforeEach(() => {
    api.createWorkspaceGrant.mockReset();
    api.getConnectors.mockReset();
    api.getSettings.mockReset();
    api.pickDirectory.mockReset();
    api.revokeHostApproval.mockReset();
    api.revokeWorkspaceGrant.mockReset();
    api.setPersona.mockReset();
    api.updateWorkspaceGrant.mockReset();
  });

  it("manages workspace grants, Docker fallback, directory requests, and permanent approvals", async () => {
    const workspace = {
      docker: {
        cli_available: true,
        daemon_available: false,
        image_available: false,
        available: false,
        image: "python:3.13-slim",
        network: "unrestricted",
      },
      execution_target: "host_fallback",
      host_fallback_enabled: true,
      grants: [
        {
          id: "grant-1",
          path: "/Users/operator/Candidates",
          label: "Candidates",
          access: "read_write",
          allow_shell: true,
          filesystem_identity: { device: 1, inode: 2 },
          created_at: "2026-08-26T12:00:00Z",
          updated_at: "2026-08-26T12:00:00Z",
          revoked_at: null,
        },
      ],
      directory_requests: [
        {
          id: "directory-request-1",
          label: "Candidate packets",
          access: "read_write",
          allow_shell: true,
          session_id: "session-1",
          run_id: "run-1",
          created_at: "2026-08-26T12:00:00Z",
          resolved_at: null,
          grant_id: null,
        },
      ],
      host_approvals: [
        {
          id: "host-approval-1",
          command_summary: "bash · 1 argument",
          cwd: "/Users/operator/Candidates",
          fingerprint: "abcdef123456",
          created_at: "2026-08-26T12:00:00Z",
          revoked_at: null,
        },
      ],
      tasks: [],
    };
    api.getSettings.mockResolvedValue({
      persona: { id: "sourcing", name: "Sourcecado Sourcing Agent" },
      model: "configured-model-id",
      gmail: { connected: false, email: null },
      apollo: { configured: false },
      workspace,
    });
    api.getConnectors.mockResolvedValue({ connectors: [] });
    api.pickDirectory.mockResolvedValue("/Users/operator/Packets");
    api.createWorkspaceGrant.mockResolvedValue({
      grant: {
        ...workspace.grants[0],
        id: "grant-2",
        path: "/Users/operator/Packets",
        label: "Candidate packets",
      },
    });
    api.revokeHostApproval.mockResolvedValue({
      approval: { ...workspace.host_approvals[0], revoked_at: "now" },
    });

    const { container } = render(<SettingsPage />);

    const access = await screen.findByRole("region", { name: "Workspace access" });
    expect(access).toHaveTextContent("Docker unavailable");
    expect(access).toHaveTextContent("Host fallback is not sandboxed");
    expect(access).toHaveTextContent("CLI installed");
    expect(access).toHaveTextContent("Daemon unavailable");
    expect(access).toHaveTextContent("Image missing");
    expect(access).toHaveTextContent("Network unrestricted");
    expect(access).toHaveTextContent("Candidates");
    expect(access).toHaveTextContent("/Users/operator/Candidates");
    expect(access).toHaveTextContent("Read and write");
    expect(access).toHaveTextContent("Shell enabled");

    fireEvent.click(
      within(access).getByRole("button", { name: "Choose Candidate packets" }),
    );
    await waitFor(() =>
      expect(api.createWorkspaceGrant).toHaveBeenCalledWith({
        path: "/Users/operator/Packets",
        label: "Candidate packets",
        access: "read_write",
        allow_shell: true,
        request_id: "directory-request-1",
      }),
    );
    expect(container).not.toHaveTextContent("directory-request-1");

    fireEvent.click(
      screen.getByRole("button", { name: "Revoke permanent approval bash · 1 argument" }),
    );
    await waitFor(() =>
      expect(api.revokeHostApproval).toHaveBeenCalledWith("host-approval-1"),
    );
  });

  it("announces that operator settings are loading", () => {
    api.getSettings.mockReturnValue(new Promise(() => {}));
    api.getConnectors.mockReturnValue(new Promise(() => {}));

    render(<SettingsPage />);

    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("status")).toHaveTextContent("Loading settings");
  });

  it("shows local identity, configured model status, and a safe connector summary", async () => {
    api.getSettings.mockResolvedValue({
      persona: { id: "sourcing", name: "Sourcecado Sourcing Agent" },
      model: "provider/gpt-secret-implementation-id",
      gmail: { connected: true, email: "operator@example.com" },
      apollo: { configured: true },
      api_key: "sk-settings-never-render",
    });
    api.getConnectors.mockResolvedValue({
      connectors: [
        {
          id: "gmail",
          title: "Gmail",
          status: "connected",
          email: "operator@example.com",
          access_token: "oauth-secret-never-render",
        },
        {
          id: "apollo",
          title: "Apollo",
          status: "configured",
          email: null,
          api_key: "apollo-secret-never-render",
        },
        { id: "drive", title: "Drive", status: "missing", email: null },
      ],
    });

    const { container } = render(<SettingsPage />);

    const operator = await screen.findByRole("region", { name: "Operator" });
    expect(operator).toHaveTextContent("Sourcing Director");
    expect(operator).toHaveTextContent("Local to this device");
    expect(screen.getByText("Sourcecado Sourcing Agent")).toBeInTheDocument();

    const model = screen.getByRole("region", { name: "Model" });
    expect(model).toHaveTextContent("Configured");
    expect(model).toHaveTextContent("Chat is ready to use the configured model");

    const connectors = screen.getByRole("list", { name: "Connector status" });
    expect(within(connectors).getByText("Gmail")).toBeInTheDocument();
    expect(within(connectors).getByText("Connected")).toBeInTheDocument();
    expect(within(connectors).getByText("Apollo")).toBeInTheDocument();
    expect(within(connectors).getByText("Configured")).toBeInTheDocument();
    expect(within(connectors).getByText("Drive")).toBeInTheDocument();
    expect(within(connectors).getByText("Needs setup")).toBeInTheDocument();

    expect(container).not.toHaveTextContent("provider/gpt-secret-implementation-id");
    expect(container).not.toHaveTextContent("sk-settings-never-render");
    expect(container).not.toHaveTextContent("oauth-secret-never-render");
    expect(container).not.toHaveTextContent("apollo-secret-never-render");
  });

  it("makes an unconfigured model state explicit without exposing setup internals", async () => {
    api.getSettings.mockResolvedValue({
      persona: { id: "sourcing", name: "Sourcecado Sourcing Agent" },
      model: null,
      gmail: { connected: false, email: null },
      apollo: { configured: false },
    });
    api.getConnectors.mockResolvedValue({ connectors: [] });

    render(<SettingsPage />);

    const model = await screen.findByRole("region", { name: "Model" });
    expect(model).toHaveTextContent("Not configured");
    expect(model).toHaveTextContent("Chat needs a configured model before it can run");
    expect(model).not.toHaveTextContent("environment variable");
    expect(model).not.toHaveTextContent("API key");
  });

  it("shows a safe load failure and retries both settings sources", async () => {
    api.getSettings
      .mockRejectedValueOnce(new Error("settings 500 token=super-secret /private/config.json"))
      .mockResolvedValue({
        persona: { id: "buddy", name: "Club" },
        model: null,
        gmail: { connected: false, email: null },
        apollo: { configured: false },
      });
    api.getConnectors.mockResolvedValue({ connectors: [] });

    const { container } = render(<SettingsPage />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Settings couldn’t be loaded");
    expect(container).not.toHaveTextContent("super-secret");
    expect(container).not.toHaveTextContent("/private/config.json");

    fireEvent.click(within(alert).getByRole("button", { name: "Retry loading settings" }));

    expect(await screen.findByRole("region", { name: "Operator" })).toBeInTheDocument();
    expect(api.getSettings).toHaveBeenCalledTimes(2);
    expect(api.getConnectors).toHaveBeenCalledTimes(2);
  });

  it("reserves card chrome for the interactive persona choice, not static info panels", async () => {
    api.getSettings.mockResolvedValue({
      persona: { id: "sourcing", name: "Sourcecado Sourcing Agent" },
      model: "configured-model-id",
      gmail: { connected: false, email: null },
      apollo: { configured: false },
    });
    api.getConnectors.mockResolvedValue({ connectors: [] });

    render(<SettingsPage />);

    const persona = await screen.findByRole("region", { name: "On-duty persona" });
    expect(persona).toHaveClass("settings-card");
    expect(screen.getByRole("region", { name: "Operator" })).not.toHaveClass(
      "settings-card",
    );
    expect(screen.getByRole("region", { name: "Model" })).not.toHaveClass(
      "settings-card",
    );
    expect(screen.getByRole("region", { name: "Connections" })).not.toHaveClass(
      "settings-card",
    );
  });

  it("persists a persona switch and updates the visible selected state", async () => {
    window.location.hash = "#/settings";
    api.getSettings.mockResolvedValue({
      persona: { id: "sourcing", name: "Sourcecado Sourcing Agent" },
      model: "configured-model-id",
      gmail: { connected: false, email: null },
      apollo: { configured: false },
    });
    api.getConnectors.mockResolvedValue({ connectors: [] });
    api.setPersona.mockResolvedValue({ persona: { id: "buddy", name: "Club" } });

    render(<SettingsPage />);

    const sourcing = await screen.findByRole("button", { name: /Sourcing agent/ });
    const buddy = screen.getByRole("button", { name: /Local coworker/ });
    expect(sourcing).toHaveAttribute("aria-pressed", "true");
    expect(buddy).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(buddy);

    await waitFor(() => expect(api.setPersona).toHaveBeenCalledWith("buddy"));
    await waitFor(() => expect(buddy).toHaveAttribute("aria-pressed", "true"));
    expect(sourcing).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("Club")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Persona changed to Local coworker");
    expect(window.location.hash).toBe("#/settings");
  });

  it("keeps the prior persona selected when persistence fails and exposes no error details", async () => {
    api.getSettings.mockResolvedValue({
      persona: { id: "sourcing", name: "Sourcecado Sourcing Agent" },
      model: null,
      gmail: { connected: false, email: null },
      apollo: { configured: false },
    });
    api.getConnectors.mockResolvedValue({ connectors: [] });
    api.setPersona.mockRejectedValue(
      new Error("persona 500 token=persona-secret /Users/operator/personas/buddy.md"),
    );

    const { container } = render(<SettingsPage />);
    const sourcing = await screen.findByRole("button", { name: /Sourcing agent/ });
    const buddy = screen.getByRole("button", { name: /Local coworker/ });

    fireEvent.click(buddy);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Persona couldn’t be changed");
    expect(alert).toHaveTextContent("Your previous selection is still active");
    expect(container).not.toHaveTextContent("persona-secret");
    expect(container).not.toHaveTextContent("/Users/operator/personas");
    expect(sourcing).toHaveAttribute("aria-pressed", "true");
    expect(buddy).toHaveAttribute("aria-pressed", "false");
    expect(sourcing).toBeEnabled();
    expect(buddy).toBeEnabled();
  });
});
