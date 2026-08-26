import { describe, expect, it } from "vitest";

import { styles } from "./cssBundle";

describe("Apollo shortlist styles", () => {
  it("uses one bounded disclosure surface with quiet divided rows", () => {
    expect(styles).toMatch(
      /\.sourcecado-apollo-result\s*\{[^}]*max-width:\s*100%;[^}]*border-top:\s*1px solid var\(--border\);[^}]*background:\s*transparent;/,
    );
    expect(styles).toMatch(
      /\.sourcecado-apollo-result > ol > li\s*\{[^}]*border-top:\s*1px solid var\(--border\);[^}]*background:\s*transparent;/,
    );
  });

  it("keeps Apollo controls touch-sized on narrow screens", () => {
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.sourcecado-apollo-result button\s*\{[^}]*min-height:\s*44px;/,
    );
  });

  it("disables candidate skeleton animation when reduced motion is requested", () => {
    expect(styles).toMatch(
      /\.sourcecado-apollo-skeleton span\s*\{[^}]*animation:\s*shell-shimmer/,
    );
    expect(styles).toMatch(
      /@media \(prefers-reduced-motion: reduce\)[\s\S]*?\.sourcecado-apollo-skeleton span[\s\S]*?animation:\s*none;/,
    );
  });
});
