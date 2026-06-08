import { describe, expect, it } from "vitest";
import { slugifySourceId } from "../src/db.js";

describe("slugifySourceId", () => {
  it("lowercases and collapses non-alphanumeric runs while preserving separators", () => {
    expect(slugifySourceId("Spring 2026/Cold Emailing/Apollo.csv")).toBe(
      "spring-2026/cold-emailing/apollo-csv"
    );
  });

  it("produces the same id for Windows backslash and POSIX separators", () => {
    const posix = slugifySourceId("spring-2026/ai/outreach.csv");
    const windows = slugifySourceId("spring-2026\\ai\\outreach.csv");
    expect(windows).toBe(posix);
    expect(windows).toBe("spring-2026/ai/outreach-csv");
  });

  it("drops empty segments from leading or repeated separators", () => {
    expect(slugifySourceId("/spring-2026//ai/")).toBe("spring-2026/ai");
  });
});
