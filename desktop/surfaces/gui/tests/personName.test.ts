import { describe, expect, it } from "vitest";

import { withoutApolloNameMasks } from "../src/personName";

describe("Apollo name masks", () => {
  it("handles non-Latin masked surnames without rewriting Markdown prose", () => {
    expect(withoutApolloNameMasks("Ada 张***李")).toBe(
      "Ada (surname hidden by Apollo)",
    );
    expect(
      withoutApolloNameMasks("This is **important**. Use *carefully*, please."),
    ).toBe("This is **important**. Use *carefully*, please.");
  });
});
