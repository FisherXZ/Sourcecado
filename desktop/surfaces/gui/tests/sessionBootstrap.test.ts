import { afterEach, describe, expect, it, vi } from "vitest";

import { getSessionsForBoot } from "../src/app/sessionBootstrap";

describe("session-list bootstrap", () => {
  afterEach(() => {
    window.__CLUB_HTTP__ = undefined;
    window.__CLUB_API_TOKEN__ = undefined;
    vi.unstubAllGlobals();
  });

  it("uses the current launch token when a bounded retry runs", async () => {
    window.__CLUB_HTTP__ = "http://sidecar.test";
    window.__CLUB_API_TOKEN__ = "old-launch-token";
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(async () => {
        window.__CLUB_API_TOKEN__ = "rotated-launch-token";
        return { ok: false, status: 401 };
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({
          sessions: [],
          open_id: null,
          last_destination: "#/skills",
        }),
      });
    vi.stubGlobal("fetch", fetchMock);

    await expect(getSessionsForBoot(() => true)).resolves.toEqual({
      sessions: [],
      open_id: null,
      last_destination: "#/skills",
    });

    const firstHeaders = fetchMock.mock.calls[0][1].headers as Headers;
    const secondHeaders = fetchMock.mock.calls[1][1].headers as Headers;
    expect(firstHeaders.get("X-Club-Token")).toBe("old-launch-token");
    expect(secondHeaders.get("X-Club-Token")).toBe("rotated-launch-token");
  });
});
