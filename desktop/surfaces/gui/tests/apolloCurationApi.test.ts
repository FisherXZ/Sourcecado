import { beforeEach, describe, expect, it, vi } from "vitest";

import { curateApolloCandidates } from "../src/api";

describe("Apollo curation API boundary", () => {
  beforeEach(() => {
    window.__CLUB_HTTP__ = "http://sidecar.test";
    window.__CLUB_API_TOKEN__ = "review-token";
  });

  it("sends reviewed rows and keeps only person/chat receipt fields", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        status: "partial",
        selected_row_count: 2,
        selected_identity_count: 1,
        kept: [
          {
            row_index: 0,
            apollo_id: "person-tim",
            person_id: "person-1",
            version: 2,
            operation: "created",
            first_name: "Tim",
            last_name: "Zh***g",
            title: "CEO",
            company: "Apollo.io",
            sourcing_chat: null,
            email: "PRIVATE@example.com",
          },
        ],
        failed: [{ row_index: 1, apollo_id: null, code: "missing_apollo_id" }],
        duplicates: [],
        original_session: {
          session_id: "thread-apollo",
          bound_person_id: null,
          reason: "multiple_selection",
        },
        raw_provider_payload: "PRIVATE",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const people = [
      { apolloId: "person-tim", firstName: "Tim" },
      { firstName: "Missing identity" },
    ];

    const result = await curateApolloCandidates({
      sessionId: "thread-apollo",
      target: "Director-authored target",
      people,
      bindOriginal: false,
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://sidecar.test/v1/apollo/curate",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          session_id: "thread-apollo",
          target: "Director-authored target",
          people,
          bind_original: false,
        }),
      }),
    );
    expect(result.kept[0]).toEqual({
      row_index: 0,
      apollo_id: "person-tim",
      person_id: "person-1",
      version: 2,
      operation: "created",
      first_name: "Tim",
      last_name: null,
      last_name_status: "hidden_by_apollo",
      title: "CEO",
      company: "Apollo.io",
      sourcing_chat: null,
    });
    expect(JSON.stringify(result)).not.toContain("Zh***g");
    expect(JSON.stringify(result)).not.toContain("PRIVATE");
  });
});
