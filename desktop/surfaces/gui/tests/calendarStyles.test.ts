import { describe, expect, it } from "vitest";

import { styles } from "./cssBundle";

describe("Calendar artifact styles", () => {
  it("uses a bounded quiet surface with divided event rows", () => {
    expect(styles).toMatch(
      /\.sourcecado-calendar-result\s*\{[^}]*max-width:\s*100%;[^}]*border-top:\s*1px solid var\(--border\);[^}]*background:\s*transparent;/,
    );
    expect(styles).toMatch(
      /\.sourcecado-calendar-result > ol > li\s*\{[^}]*border-top:\s*1px solid var\(--border\);[^}]*background:\s*transparent;/,
    );
  });

  it("preserves description line breaks and uses touch-sized narrow controls", () => {
    expect(styles).toMatch(
      /\.sourcecado-calendar-description > p\s*\{[^}]*white-space:\s*pre-wrap;[^}]*overflow-wrap:\s*anywhere;/,
    );
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.sourcecado-calendar-result button,\s*\.sourcecado-calendar-approval-summary button\s*\{[^}]*min-height:\s*44px;/,
    );
  });

  it("removes Calendar skeleton motion for reduced-motion operators", () => {
    expect(styles).toMatch(
      /\.sourcecado-calendar-loading li span\s*\{[^}]*animation:\s*shell-shimmer/,
    );
    expect(styles).toMatch(
      /@media \(prefers-reduced-motion: reduce\)[\s\S]*?\.sourcecado-calendar-loading li span[\s\S]*?animation:\s*none;/,
    );
  });
});
