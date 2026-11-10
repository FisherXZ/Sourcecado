import { describe, expect, it } from "vitest";

import { styles } from "./cssBundle";

describe("Settings route styles", () => {
  it("keeps persona and recovery actions at least 44px tall on narrow layouts", () => {
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.persona-options button,\s*\.route-error button\s*\{[^}]*min-height:\s*44px;/,
    );
  });

  it("renders provider verification as compact divided operational rows", () => {
    expect(styles).toMatch(
      /\.provider-verification-list\s*\{[^}]*display:\s*grid;[^}]*list-style:\s*none;/,
    );
    expect(styles).toMatch(
      /\.provider-verification-list > li\s*\{[^}]*border-top:\s*1px solid var\(--border\);/,
    );
    expect(styles).toMatch(
      /\.provider-verification-status\.verified\s*\{[^}]*background:\s*var\(--accent-tint\);[^}]*color:\s*var\(--accent-deep\);/,
    );
  });
});
