import { describe, expect, it } from "vitest";

import { styles } from "./cssBundle";

describe("Jump to latest styles", () => {
  it("floats above the composer without taking room in the thread", () => {
    expect(styles).toMatch(
      /\.sourcecado-jump-anchor\s*\{[^}]*position:\s*relative;[^}]*height:\s*0;/,
    );
    expect(styles).toMatch(
      /\.sourcecado-jump-latest\s*\{[^}]*position:\s*absolute;[^}]*bottom:\s*12px;[^}]*width:\s*36px;[^}]*height:\s*36px;/,
    );
  });

  it("uses Warm Operator tokens and a visible keyboard focus ring", () => {
    const rule = styles.match(/\.sourcecado-jump-latest\s*\{[^}]*\}/)?.[0] ?? "";
    expect(rule).toContain("var(--surface)");
    expect(rule).toContain("var(--border)");
    expect(rule).toContain("var(--text)");
    expect(rule).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(styles).toMatch(
      /\.sourcecado-jump-latest:focus-visible\s*\{[^}]*outline:\s*2px solid var\(--accent\);/,
    );
  });

  it("fades in only when motion is allowed", () => {
    expect(styles).toMatch(
      /@media \(prefers-reduced-motion: no-preference\)\s*\{\s*\.sourcecado-jump-latest\s*\{[^}]*animation:\s*sourcecado-jump-in/,
    );
    expect(styles).toMatch(
      /@media \(prefers-reduced-motion: reduce\)\s*\{\s*\.sourcecado-jump-latest\s*\{[^}]*transition:\s*none;/,
    );
  });

  it("grows to a 44px touch target on narrow screens", () => {
    expect(styles).toMatch(
      /@media \(max-width: 767px\)\s*\{\s*\.sourcecado-jump-latest\s*\{[^}]*width:\s*44px;[^}]*height:\s*44px;/,
    );
  });

  it("leaves the scroll behavior of the transcript to the thread library", () => {
    expect(styles).not.toMatch(/\.sourcecado-transcript[^{]*\{[^}]*\sscroll-behavior/);
  });
});
