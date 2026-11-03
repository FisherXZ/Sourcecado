import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { ConnectionsPage } from "../src/routes/ConnectionsPage";

vi.mock("../src/api", () => ({
  getConnectors: async () => ({ connectors: [] }), checkConnectors: async () => ({ connectors: [] }),
  connectConnector: vi.fn(), disconnectConnector: vi.fn(),
}));
let profile = { configured: false, verified: false, workspace: null as string | null, spend_paused: false };
const calls: Array<{url: string; body?: string}> = [];
beforeEach(() => {
  profile = { configured: false, verified: false, workspace: null, spend_paused: false };
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url, options: RequestInit = {}) => {
    calls.push({ url: String(url), body: options.body as string });
    if (options.method === "PUT") profile = { ...profile, configured: true };
    if (String(url).endsWith("/check")) profile = { ...profile, verified: true, workspace: "Sample workspace" };
    if (options.method === "DELETE") profile = { ...profile, configured: false, verified: false };
    return { ok: true, json: async () => profile };
  }));
});
afterEach(() => vi.unstubAllGlobals());

it("shows honest Not connected and saves a password field without exposing or persisting the key", async () => {
  render(<ConnectionsPage />);
  await screen.findByRole("heading", { name: "Monid sources" });
  expect(await screen.findByText("Not connected", { exact: true })).toBeVisible();
  const input = screen.getByLabelText("Monid API key");
  expect(input).toHaveAttribute("type", "password");
  fireEvent.change(input, { target: { value: "mk_fake_ui_credential_only" } });
  fireEvent.click(screen.getByRole("button", { name: "Save Monid key" }));
  await screen.findByText("Key saved — check connection");
  expect(input).toHaveValue("");
  expect(document.body.textContent).not.toContain("mk_fake_ui_credential_only");
  expect(JSON.stringify(localStorage)).not.toContain("mk_fake_ui_credential_only");
  expect(calls.filter(call => call.url.endsWith("/check"))).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "Check Monid connection" }));
  await screen.findByText("Connected", { exact: true });
  expect(screen.getByText("Sample workspace")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Disconnect Monid" }));
  await waitFor(() => expect(screen.getByText("Not connected", { exact: true })).toBeVisible());
  expect(calls.some(call => call.url.endsWith("/run"))).toBe(false);
});

it("does not keep claiming Connected after an explicit credential check fails", async () => {
  profile = {configured:true,verified:true,workspace:"Sample workspace",spend_paused:false};
  render(<ConnectionsPage />);
  const card=await screen.findByRole("region",{name:"Monid sources"});
  await within(card).findByText("Connected",{exact:true});
  vi.mocked(fetch).mockRejectedValueOnce(new Error("Never display a provider authorization header"));
  fireEvent.click(screen.getByRole("button",{name:"Check Monid connection"}));
  await screen.findByRole("alert");
  expect(screen.queryByText("Connected",{exact:true})).toBeNull();
  expect(screen.getByText("Key saved — check connection")).toBeVisible();
  expect(document.body.textContent).not.toContain("provider authorization header");
});
