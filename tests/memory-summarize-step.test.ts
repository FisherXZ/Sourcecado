import { summarizeStep } from "@/lib/memory/answer";

describe("summarizeStep", () => {
  it("counts facts and chunks for a successful search_memory step", () => {
    const part = summarizeStep({
      index: 1,
      tool: "search_memory",
      ok: true,
      observation: 'Success: {"acceptedFacts":[1,2,3],"gapFacts":[1],"chunks":[1,2]}',
    });
    expect(part).toMatchObject({ index: 1, tool: "search_memory", ok: true });
    expect(part.detail).toBe("4 facts, 2 chunks");
  });

  it("singularizes one fact / one chunk", () => {
    const part = summarizeStep({
      index: 2,
      tool: "search_memory",
      ok: true,
      observation: 'Success: {"acceptedFacts":[1],"gapFacts":[],"chunks":[1]}',
    });
    expect(part.detail).toBe("1 fact, 1 chunk");
  });

  it("surfaces the error message (without the Error(...) prefix) for a failed step", () => {
    const part = summarizeStep({
      index: 1,
      tool: "search_memory",
      ok: false,
      observation: "Error (permission_denied): source not allowed",
    });
    expect(part.ok).toBe(false);
    expect(part.detail).toContain("source not allowed");
    expect(part.detail).not.toContain("Error (");
  });

  it("attaches a contactCard for a found get_contact step", () => {
    const part = summarizeStep({
      index: 1,
      tool: "get_contact",
      ok: true,
      observation:
        'Success: {"status":"found","contact":{"id":1,"canonicalName":"Jane Smith","role":"PM","organizationName":"Acme",' +
        '"phone":"555-0100","email":"jane@acme.com","linkedinUrl":"https://linkedin.com/in/janesmith","photoUrl":"https://example.com/jane.jpg"}}',
    });
    expect(part.contactCard).toEqual({
      id: 1,
      canonicalName: "Jane Smith",
      role: "PM",
      organizationName: "Acme",
      phone: "555-0100",
      email: "jane@acme.com",
      linkedinUrl: "https://linkedin.com/in/janesmith",
      photoUrl: "https://example.com/jane.jpg",
    });
  });

  it("omits contactCard for an ambiguous or not_found get_contact step", () => {
    const ambiguous = summarizeStep({
      index: 1,
      tool: "get_contact",
      ok: true,
      observation: 'Success: {"status":"ambiguous","candidates":[]}',
    });
    expect(ambiguous.contactCard).toBeUndefined();

    const notFound = summarizeStep({
      index: 1,
      tool: "get_contact",
      ok: true,
      observation: 'Success: {"status":"not_found"}',
    });
    expect(notFound.contactCard).toBeUndefined();
  });

  it("attaches outreachHistory for a list_outreach_history step", () => {
    const part = summarizeStep({
      index: 1,
      tool: "list_outreach_history",
      ok: true,
      observation:
        'Success: [{"id":1,"occurredAt":"2026-01-01T00:00:00.000Z","channel":"email","summary":"Intro","citation":null}]',
    });
    expect(part.outreachHistory).toHaveLength(1);
    expect(part.outreachHistory?.[0].summary).toBe("Intro");
  });

  it("attaches memoryFacts (accepted + gap) for a search_memory step", () => {
    const part = summarizeStep({
      index: 1,
      tool: "search_memory",
      ok: true,
      observation:
        'Success: {"acceptedFacts":[{"subject":"Jane","predicate":"role","object":"PM","citation":"c1","status":"accepted"}],' +
        '"gapFacts":[{"subject":"Jane","predicate":"last_contacted","object":"unknown","citation":null,"status":"candidate"}],' +
        '"chunks":[]}',
    });
    expect(part.memoryFacts).toHaveLength(2);
    expect(part.memoryFacts?.map((f) => f.status).sort()).toEqual(["accepted", "candidate"]);
  });

  it("omits memoryFacts when search_memory returns nothing", () => {
    const part = summarizeStep({
      index: 1,
      tool: "search_memory",
      ok: true,
      observation: 'Success: {"acceptedFacts":[],"gapFacts":[],"chunks":[]}',
    });
    expect(part.memoryFacts).toBeUndefined();
  });

  it("attaches no structured data for a failed step, regardless of tool", () => {
    const part = summarizeStep({
      index: 1,
      tool: "get_contact",
      ok: false,
      observation: "Error (invalid_args): name is required",
    });
    expect(part.contactCard).toBeUndefined();
    expect(part.outreachHistory).toBeUndefined();
    expect(part.memoryFacts).toBeUndefined();
  });
});
