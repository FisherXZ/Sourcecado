import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SettingsPage } from "../src/routes/SettingsPage";

const api = vi.hoisted(() => ({
  getConnectors: vi.fn(),
  getSettings: vi.fn(),
  setPersona: vi.fn(),
}));

vi.mock("../src/api", () => ({
  getConnectors: api.getConnectors,
  getSettings: api.getSettings,
  setPersona: api.setPersona,
}));

describe("SettingsPage", () => {
  beforeEach(() => {
    api.getConnectors.mockReset();
    api.getSettings.mockReset();
    api.setPersona.mockReset();
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
