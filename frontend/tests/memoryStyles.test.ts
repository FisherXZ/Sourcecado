import { describe, expect, it } from "vitest";

import { styles } from "./cssBundle";

describe("Saved memory review styles", () => {
  it("puts the waiting count on the warm accent tint rather than a raw colour", () => {
    expect(styles).toMatch(
      /\.memory-counts strong\s*\{[^}]*background:\s*var\(--accent-tint\);[^}]*color:\s*var\(--accent-deep\);/,
    );
  });

  it("keeps review rows as quiet hairline surfaces", () => {
    expect(styles).toMatch(
      /\.memory-card\s*\{[^}]*border:\s*1px solid var\(--border\);[^}]*background:\s*var\(--surface\);/,
    );
  });

  it("keeps the keep and delete controls touch sized on narrow viewports", () => {
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.memory-actions button\s*\{[^}]*min-height:\s*44px;[^}]*min-width:\s*44px;/,
    );
  });
});
