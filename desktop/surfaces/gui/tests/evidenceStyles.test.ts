import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const styles = readFileSync("src/styles.css", "utf8");

describe("Evidence set styles", () => {
  it("uses one bounded quiet surface with divided evidence rows", () => {
    expect(styles).toMatch(
      /\.sourcecado-evidence-set\s*\{[^}]*max-width:\s*100%;[^}]*border-top:\s*1px solid var\(--border\);[^}]*background:\s*transparent;/,
    );
    expect(styles).toMatch(
      /\.sourcecado-evidence-set > ol > li\s*\{[^}]*border-top:\s*1px solid var\(--border\);[^}]*background:\s*transparent;/,
    );
  });

  it("preserves excerpt line breaks and uses touch-sized narrow controls", () => {
    expect(styles).toMatch(
      /\.sourcecado-evidence-excerpt > p\s*\{[^}]*white-space:\s*pre-wrap;[^}]*overflow-wrap:\s*anywhere;/,
    );
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.sourcecado-evidence-set button\s*\{[^}]*min-height:\s*44px;/,
    );
  });

  it("removes evidence skeleton motion for reduced-motion operators", () => {
    expect(styles).toMatch(
      /\.sourcecado-evidence-loading li span\s*\{[^}]*animation:\s*shell-shimmer/,
    );
    expect(styles).toMatch(
      /@media \(prefers-reduced-motion: reduce\)[\s\S]*?\.sourcecado-evidence-loading li span\s*\{[^}]*animation:\s*none;/,
    );
  });
});
