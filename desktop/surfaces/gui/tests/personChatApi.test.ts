import { beforeEach, describe, expect, it, vi } from "vitest";

import { getSession, openPersonSourcingChat } from "../src/api";

describe("person sourcing chat API boundary", () => {
  beforeEach(() => {
    window.__CLUB_HTTP__ = "http://sidecar.test";
    window.__CLUB_API_TOKEN__ = "review-token";
  });

  it("sends the viewed version and rebuilds only stable binding fields", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 201,
      json: async () => ({
        created: true,
        session: { id: "thread one", title: "Sourcing · Ada", n_msgs: 0 },
        active_person: {
          person_id: "person one",
          version: 2,
          label: "Ada, Founder at Analytic",
          private_evidence: "PLANTED",
        },
        raw_prompt: "PLANTED",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(openPersonSourcingChat("person one", 2)).resolves.toEqual({
      created: true,
      session: { id: "thread one", title: "Sourcing · Ada", n_msgs: 0 },
      active_person: {
        person_id: "person one",
        version: 2,
        label: "Ada, Founder at Analytic",
      },
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://sidecar.test/v1/people/person%20one/sourcing-chat",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ expected_person_version: 2 }),
      }),
    );
  });

  it("verifies the route person while loading a bound session", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        id: "thread one",
        title: "Sourcing · Ada",
        messages: [],
        events: [],
        active_person: {
          person_id: "person one",
          version: 2,
          label: "Ada",
          private_evidence: "PLANTED",
        },
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const conversation = await getSession("thread one", "person one");

    expect(fetchMock).toHaveBeenCalledWith(
      "http://sidecar.test/v1/sessions/thread%20one?expected_person_id=person%20one",
      expect.any(Object),
    );
    expect(conversation.active_person).toEqual({
      person_id: "person one",
      version: 2,
      label: "Ada",
    });
    expect(JSON.stringify(conversation)).not.toContain("PLANTED");
  });
});
