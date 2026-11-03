import { beforeEach, describe, expect, it, vi } from "vitest";

import { resolveApolloIdentities, searchSavedPeople } from "../src/api";

describe("Saved-person finder API boundary", () => {
  beforeEach(() => {
    window.__CLUB_HTTP__ = "http://backend.test";
    window.__CLUB_API_TOKEN__ = "finder-token";
  });

  it("GETs the search route with an encoded query, a limit, and the token", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        results: [
          {
            person_id: "person-1",
            first_name: "Maya",
            last_name: "Chen",
            last_name_status: "known",
            title: "Engineer",
            company: "Acme Labs",
            sequence_state: null,
            board_lane: "backlog",
            has_email: false,
            apollo_id: "a1",
            match_kind: "name",
          },
        ],
        count: 1,
        query: "maya labs",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await searchSavedPeople("maya labs", 20);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://backend.test/v1/people/search?q=maya%20labs&limit=20");
    expect(init.headers.get("X-Club-Token")).toBe("finder-token");
    expect(result.count).toBe(1);
    expect(result.results[0].board_lane).toBe("backlog");
  });

  it("POSTs candidates to resolve-identities and returns resolved + possible", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        resolved: {
          a1: {
            person_id: "person-1",
            first_name: "Maya",
            last_name: "Chen",
            title: "Engineer",
            company: "Acme Labs",
            sequence_state: null,
            board_lane: "backlog",
          },
        },
        possible: {
          a2: [
            {
              person_id: "person-2",
              first_name: "Noor",
              last_name: "Ali",
              title: "Designer",
              company: "Globex",
              sequence_state: null,
              board_lane: "backlog",
            },
          ],
        },
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const candidates = [
      { apollo_id: "a1", name: "Maya Chen" },
      { apollo_id: "a2", name: "Noor Ali" },
    ];
    const matches = await resolveApolloIdentities(candidates);

    expect(fetchMock).toHaveBeenCalledWith(
      "http://backend.test/v1/people/resolve-identities",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ candidates }),
      }),
    );
    expect(matches.resolved.a1.person_id).toBe("person-1");
    expect(matches.resolved.a2).toBeUndefined();
    expect(matches.possible.a2[0].person_id).toBe("person-2");
  });

  it("throws on a non-ok search response so the finder can show an error state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({}) }),
    );
    await expect(searchSavedPeople("maya")).rejects.toThrow("people search 500");
  });
});
