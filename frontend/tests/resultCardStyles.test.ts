import { describe, expect, it } from "vitest";

import { styles } from "./cssBundle";

// The result cards (#234) share one chrome. These are stylesheet-text guards:
// they catch a declaration that disappears or shrinks. They do not measure a
// rendered box. The rendered boundary is covered by the result tests.
describe("Result card styles", () => {
  it("draws one bordered, bounded card with a 36px row", () => {
    expect(styles).toMatch(
      /\.sourcecado-card\s*\{[^}]*max-width:\s*100%;[^}]*border:\s*1px solid var\(--border\);[^}]*border-radius:\s*8px;/,
    );
    expect(styles).toMatch(/\.sourcecado-card-row\s*\{[^}]*min-height:\s*36px;/);
  });

  it("scrolls rows sideways inside the card body only", () => {
    expect(styles).toMatch(/\.sourcecado-card-body\s*\{[^}]*overflow-x:\s*auto;/);
    expect(styles).toMatch(
      /\.sourcecado-card-rows\s*\{[^}]*width:\s*max-content;[^}]*min-width:\s*100%;/,
    );
  });

  it("keeps card and chip text at 13px or larger and out of muted ink", () => {
    const start = styles.indexOf("/* Result cards and source chips (#234)");
    const end = styles.indexOf("/* Process layer (#226)");
    expect(start).toBeGreaterThanOrEqual(0);
    const block = styles.slice(start, end);
    const sizes = [...block.matchAll(/font-size:\s*(\d+)px/g)].map((m) => Number(m[1]));
    expect(sizes.length).toBeGreaterThan(0);
    expect(Math.min(...sizes)).toBeGreaterThanOrEqual(13);
    expect(block).not.toMatch(/color:\s*var\(--muted\)/);
  });

  it("gives each state its own look", () => {
    for (const state of ["failed", "partial", "restricted", "stale"]) {
      expect(styles).toContain(`.sourcecado-card-state[data-state="${state}"]`);
    }
    expect(styles).toMatch(
      /\.sourcecado-card\[data-state="failed"\]\s*\{[^}]*border-color:\s*var\(--error\);/,
    );
    expect(styles).toMatch(
      /\.sourcecado-card\[data-state="unsupported"\][^{]*\{[^}]*border-style:\s*dashed;/,
    );
  });

  it("keeps card controls and source chips touch-sized on narrow screens", () => {
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.sourcecado-card-button,[\s\S]*?\.sourcecado-source-chip,[\s\S]*?\{[^}]*min-height:\s*44px;/,
    );
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.sourcecado-card-check\s*\{[^}]*width:\s*44px;[^}]*height:\s*44px;/,
    );
  });

  it("sizes the Show result button and the inspector link icon for touch", () => {
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.sourcecado-step-actions \.sourcecado-show-result,\s*\.sourcecado-inspector \.sourcecado-inspector-external\s*\{[^}]*width:\s*44px;/,
    );
  });

  // Carried over from the per-renderer style tests this file replaces.
  it("keeps the approval summary controls touch-sized on narrow screens", () => {
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.sourcecado-gmail-approval-preview button\s*\{[^}]*min-height:\s*44px;/,
    );
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.sourcecado-calendar-approval-summary button\s*\{[^}]*min-height:\s*44px;/,
    );
  });

  it("keeps the line breaks and long words of draft, description and excerpt text", () => {
    expect(styles).toMatch(
      /\.sourcecado-card-text > p\s*\{[^}]*white-space:\s*pre-wrap;[^}]*overflow-wrap:\s*anywhere;/,
    );
  });

  it("wraps source chips in one row under the answer", () => {
    expect(styles).toMatch(
      /\.sourcecado-source-chips\s*\{[^}]*display:\s*flex;[^}]*flex-wrap:\s*wrap;/,
    );
  });

  it("stops card motion for reduced-motion operators", () => {
    expect(styles).toMatch(
      /\.sourcecado-card-skeleton li span\s*\{[^}]*animation:\s*shell-shimmer/,
    );
    expect(styles).toMatch(
      /@media \(prefers-reduced-motion: reduce\)[\s\S]*?\.sourcecado-card-skeleton li span[\s\S]*?animation:\s*none;/,
    );
    expect(styles).toMatch(
      /@media \(prefers-reduced-motion: reduce\)[\s\S]*?\.sourcecado-card-glyph-loading\s*\{[^}]*animation:\s*none;/,
    );
  });
});
