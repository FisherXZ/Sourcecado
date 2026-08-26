import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  connectConnector,
  disconnectConnector,
  getConnectors,
} from "../src/api";

const fetchMock = vi.fn();

function response(body: unknown, options: { ok?: boolean; status?: number } = {}): Response {
  return {
    ok: options.ok ?? true,
    status: options.status ?? 200,
    json: vi.fn().mockResolvedValue(body),
  } as unknown as Response;
}

describe("connector API boundary", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    window.__CLUB_HTTP__ = "http://sidecar.test";
    window.__CLUB_API_TOKEN__ = "review-token";
  });

  it("whitelists normalized connector fields and drops credential-shaped payload fields", async () => {
    fetchMock.mockResolvedValue(
      response({
        connectors: [
          {
            id: "gmail",
            title: "Gmail",
            description: "Search email and create review-only drafts.",
            status: "missing_scopes",
            catalog_group: "connected",
            email: "operator@example.com",
            required_scopes: ["Read Gmail messages", "Create Gmail drafts"],
            missing_scopes: ["Read Gmail messages"],
            health: {
              category: "attention",
              label: "Missing permissions",
              message: "Reconnect to approve required access.",
            },
            recovery: {
              category: "grant_scopes",
              action_label: "Finish setup",
              message: "Reconnect and approve the listed permissions.",
            },
            supported_actions: ["Search and read email", "Create drafts for review"],
            available_actions: ["reconnect", "disconnect"],
            repair_route: "#/connections/gmail",
            authorization_group: "google",
            access_token: "oauth-secret-never-render",
            api_key: "api-secret-never-render",
          },
        ],
      }),
    );

    const body = await getConnectors();

    expect(body.connectors).toEqual([
      {
        id: "gmail",
        title: "Gmail",
        description: "Search email and create review-only drafts.",
        status: "missing_scopes",
        catalogGroup: "connected",
        email: "operator@example.com",
        requiredScopes: ["Read Gmail messages", "Create Gmail drafts"],
        missingScopes: ["Read Gmail messages"],
        health: {
          category: "attention",
          label: "Missing permissions",
          message: "Reconnect to approve required access.",
        },
        recovery: {
          category: "grant_scopes",
          actionLabel: "Finish setup",
          message: "Reconnect and approve the listed permissions.",
        },
        supportedActions: ["Search and read email", "Create drafts for review"],
        availableActions: ["reconnect", "disconnect"],
        repairRoute: "#/connections/gmail",
        authorizationGroup: "google",
      },
    ]);
    expect(JSON.stringify(body)).not.toContain("oauth-secret-never-render");
    expect(JSON.stringify(body)).not.toContain("api-secret-never-render");
  });

  it("starts each supported connector through its existing endpoint", async () => {
    fetchMock.mockResolvedValue(
      response({ url: "https://provider.example/authorize", opened: true, started: true }),
    );

    await connectConnector("gmail");
    await connectConnector("drive");
    await connectConnector("calendar");
    await connectConnector("granola");

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "http://sidecar.test/v1/gmail/connect",
      "http://sidecar.test/v1/connectors/drive/connect",
      "http://sidecar.test/v1/connectors/calendar/connect",
      "http://sidecar.test/v1/connectors/granola/connect",
    ]);
  });

  it("maps every shared Google disconnect to the all-Google endpoint", async () => {
    fetchMock.mockResolvedValue(
      response({ connected: false, disconnected: ["gmail", "drive", "calendar"] }),
    );

    const result = await disconnectConnector("calendar");

    expect(fetchMock).toHaveBeenCalledWith(
      "http://sidecar.test/v1/gmail/disconnect",
      expect.objectContaining({ method: "POST" }),
    );
    expect(result.disconnected).toEqual(["gmail", "drive", "calendar"]);
  });

  it("keeps Granola disconnect scoped to Granola", async () => {
    fetchMock.mockResolvedValue(
      response({ connected: false, disconnected: ["granola"] }),
    );

    const result = await disconnectConnector("granola");

    expect(fetchMock).toHaveBeenCalledWith(
      "http://sidecar.test/v1/connectors/granola/disconnect",
      expect.objectContaining({ method: "POST" }),
    );
    expect(result.disconnected).toEqual(["granola"]);
  });
});
