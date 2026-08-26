import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const css = readFileSync("src/styles.css", "utf8");

describe("connection layout styles", () => {
  it("keeps every connector action at least 44px tall", () => {
    expect(css).toMatch(
      /\.connection-primary-action,\s*\.connection-secondary-action[\s\S]*?min-height:\s*44px;/,
    );
    expect(css).toMatch(/\.connection-confirmation button[\s\S]*?min-height:\s*44px;/);
  });

  it("replaces the catalog with detail at tablet and narrow widths", () => {
    expect(css).toContain("@media (max-width: 1100px)");
    expect(css).toMatch(
      /@media \(max-width: 1100px\)[\s\S]*?\.connections-page\.has-detail \.connections-catalog\s*\{\s*display:\s*none;/,
    );
    expect(css).toMatch(
      /@media \(max-width: 1100px\)[\s\S]*?\.connections-page\.has-detail \.connection-detail\s*\{[\s\S]*?display:\s*block;/,
    );
  });

  it("keeps catalog search sticky on narrow pages and disables skeleton animation for reduced motion", () => {
    expect(css).toMatch(
      /@media \(max-width: 1100px\)[\s\S]*?\.connections-search\s*\{[\s\S]*?position:\s*sticky;[\s\S]*?top:\s*0;/,
    );
    expect(css).toMatch(
      /@media \(prefers-reduced-motion: reduce\)[\s\S]*?\.connection-skeleton-row[\s\S]*?animation:\s*none;/,
    );
  });

  it("uses restrained text marks instead of decorative icon circles", () => {
    const markRule = css.match(/\.connection-mark\s*\{([\s\S]*?)\}/)?.[1] || "";
    expect(markRule).not.toMatch(/border-radius:\s*50%/);
    expect(markRule).not.toMatch(/background:/);
  });
});
