import { describe, expect, it } from "vitest";

import { styles } from "./cssBundle";

describe("Gmail draft artifact styles", () => {
  it("uses one quiet bounded artifact surface and preserves body line breaks", () => {
    expect(styles).toMatch(
      /\.sourcecado-gmail-draft\s*\{[^}]*max-width:\s*100%;[^}]*border-top:\s*1px solid var\(--border\);[^}]*background:\s*transparent;/,
    );
    expect(styles).toMatch(
      /\.sourcecado-gmail-body\s*\{[^}]*white-space:\s*pre-wrap;[^}]*overflow-wrap:\s*anywhere;/,
    );
  });

  it("keeps draft controls touch-sized on narrow screens", () => {
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.sourcecado-gmail-draft button,\s*\.sourcecado-gmail-approval-preview button\s*\{[^}]*min-height:\s*44px;/,
    );
  });

  it("removes draft skeleton motion when reduced motion is requested", () => {
    expect(styles).toMatch(
      /\.sourcecado-gmail-creating li span\s*\{[^}]*animation:\s*shell-shimmer/,
    );
    expect(styles).toMatch(
      /@media \(prefers-reduced-motion: reduce\)[\s\S]*?\.sourcecado-gmail-creating li span[\s\S]*?animation:\s*none;/,
    );
  });
});
