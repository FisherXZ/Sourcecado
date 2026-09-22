import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createWorkspaceGrant,
  pickDirectory,
  resolveInbox,
  revokeHostApproval,
} from "../src/api";

afterEach(() => {
  vi.unstubAllGlobals();
  delete (window as unknown as { __TAURI__?: unknown }).__TAURI__;
});

describe("workspace API boundary", () => {
  it("creates a grant with the operator-selected exact path and requested authority", async () => {
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ grant: { id: "grant-1" } }),
    });
    vi.stubGlobal("fetch", fetch);

    await createWorkspaceGrant({
      path: "/Users/operator/Candidates",
      label: "Candidates",
      access: "read_write",
      allow_shell: true,
      request_id: "request-1",
    });

    const [, options] = fetch.mock.calls[0];
    expect(options.method).toBe("POST");
    expect(JSON.parse(options.body)).toEqual({
      path: "/Users/operator/Candidates",
      label: "Candidates",
      access: "read_write",
      allow_shell: true,
      request_id: "request-1",
    });
  });

  it("sends allow-always only as an explicit inbox scope", async () => {
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    vi.stubGlobal("fetch", fetch);

    await resolveInbox("approval-1", "allow", "always");

    const [, options] = fetch.mock.calls[0];
    expect(JSON.parse(options.body)).toEqual({ decision: "allow", scope: "always" });
  });

  it("uses the native Tauri directory picker and never invents a browser path", async () => {
    const open = vi.fn().mockResolvedValue("/Users/operator/Workspace");
    (window as unknown as { __TAURI__: unknown }).__TAURI__ = {
      dialog: { open },
    };

    await expect(pickDirectory()).resolves.toBe("/Users/operator/Workspace");
    expect(open).toHaveBeenCalledWith({ directory: true, multiple: false });
  });

  it("grants the main window dialog:allow-open so the native folder picker is allowed", () => {
    const capabilities = JSON.parse(
      readFileSync("src-tauri/capabilities/default.json", "utf8"),
    ) as { windows: unknown; permissions: unknown };

    expect(capabilities.windows).toEqual(["main"]);
    expect(capabilities.permissions).toContain("dialog:allow-open");
  });

  it("revokes the addressed permanent approval", async () => {
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ approval: { id: "approval-1", revoked_at: "now" } }),
    });
    vi.stubGlobal("fetch", fetch);

    await revokeHostApproval("approval-1");

    expect(fetch.mock.calls[0][0]).toContain(
      "/v1/workspaces/host-approvals/approval-1",
    );
    expect(fetch.mock.calls[0][1].method).toBe("DELETE");
  });
});
