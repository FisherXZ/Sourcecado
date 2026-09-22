import { describe, expect, it } from "vitest";

import { parseHash } from "../src/app/route";

describe("app route parser", () => {
  it("parses Board and decoded person-file destinations", () => {
    expect(parseHash("#/board")).toEqual({ kind: "board" });
    expect(parseHash("#/people/person%20one")).toEqual({
      kind: "person",
      personId: "person one",
    });
  });

  it("parses the saved-memory classification destination", () => {
    expect(parseHash("#/memory")).toEqual({ kind: "memory" });
  });

  it("parses a durable Scheduled task detail and rejects malformed task ids", () => {
    expect(parseHash("#/scheduled/42")).toEqual({ kind: "scheduled", jobId: 42 });
    expect(parseHash("#/scheduled/not-a-task")).toEqual({ kind: "scheduled" });
    expect(parseHash("#/scheduled/0")).toEqual({ kind: "scheduled" });
  });

  it("falls back safely when a person id is malformed", () => {
    expect(parseHash("#/people/%E0%A4%A")).toEqual({ kind: "board" });
  });

  it("parses a person-bound chat route with both stable identities", () => {
    expect(parseHash("#/chat/thread%20one/person/person%20one")).toEqual({
      kind: "chat",
      sessionId: "thread one",
      personId: "person one",
    });
  });
});
