import { beforeEach, describe, expect, it, vi } from "vitest";

import { getSkills } from "../src/api";

const fetchMock = vi.fn();

function response(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: vi.fn().mockResolvedValue(body),
  } as unknown as Response;
}

describe("skills API boundary", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    window.__CLUB_HTTP__ = "http://sidecar.test";
    window.__CLUB_API_TOKEN__ = "review-token";
  });

  it("normalizes inspectable fields and drops private manifest internals", async () => {
    fetchMock.mockResolvedValue(
      response({
        skills: [
          {
            name: "weekly-sourcing",
            purpose: "Build a source-backed weekly shortlist.",
            use_when: "Use when the director asks who to work next.",
            source: "builtin",
            status: "ready",
            instructions: "1. Read active Person Files.\n2. Show why-now evidence.",
            path: "/Users/operator/private/weekly-sourcing/SKILL.md",
            allowed_tools: ["gmail_send"],
            token: "sk-live-secret-never-render",
            manifest: { unrestricted: true },
          },
        ],
      }),
    );

    const body = await getSkills();

    expect(body.skills).toEqual([
      {
        name: "weekly-sourcing",
        purpose: "Build a source-backed weekly shortlist.",
        useWhen: "Use when the director asks who to work next.",
        source: "builtin",
        status: "ready",
        instructions: "1. Read active Person Files.\n2. Show why-now evidence.",
      },
    ]);
    expect(JSON.stringify(body)).not.toContain("/Users/operator/private");
    expect(JSON.stringify(body)).not.toContain("gmail_send");
    expect(JSON.stringify(body)).not.toContain("sk-live-secret-never-render");
    expect(JSON.stringify(body)).not.toContain("unrestricted");
  });

  it("drops malformed rows and falls back to safe source and status values", async () => {
    fetchMock.mockResolvedValue(
      response({
        skills: [
          null,
          { name: "", purpose: "Missing identity" },
          {
            name: "candidate-research",
            purpose: "Understand one candidate.",
            use_when: 42,
            source: "private_disk",
            status: "executing",
            instructions: null,
          },
        ],
      }),
    );

    await expect(getSkills()).resolves.toEqual({
      skills: [
        {
          name: "candidate-research",
          purpose: "Understand one candidate.",
          useWhen: "",
          source: "workspace",
          status: "ready",
          instructions: "",
        },
      ],
    });
  });
});
