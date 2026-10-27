import { describe, expect, it } from "vitest";

import { styles } from "./cssBundle";

// The recovery block (#236): failed step icon buttons, the overflow menu, the
// failed-turn notice and the reconnect chip. These checks read the stylesheet
// text, so they guard the declarations, not the rendered boxes.
const start = styles.indexOf("Failed step and failed turn recovery (#236)");
const block = styles.slice(start, styles.indexOf("@media (max-width: 767px) {\n  .sourcecado-approval-card > button,", start));

describe("recovery styles", () => {
  it("finds its own block", () => {
    expect(start).toBeGreaterThan(0);
    expect(block).toContain(".sourcecado-connection");
  });

  it("keeps every size at 13px or more and uses no muted ink", () => {
    const sizes = [...block.matchAll(/font-size:\s*(\d+(?:\.\d+)?)px/g)].map((m) => Number(m[1]));
    expect(sizes.length).toBeGreaterThan(0);
    expect(Math.min(...sizes)).toBeGreaterThanOrEqual(13);
    expect(block).not.toMatch(/var\(--muted\)/);
  });

  it("gives the icon buttons a 44px touch target on narrow screens", () => {
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.sourcecado-step-icon-button\s*\{[^}]*width:\s*44px;[^}]*height:\s*44px;/,
    );
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.sourcecado-failure-toggle\s*\{[^}]*min-height:\s*44px;[^}]*min-width:\s*44px;/,
    );
  });

  it("places the overflow menu against the window, above the step list", () => {
    expect(block).toMatch(/\.sourcecado-step-menu\s*\{[^}]*position:\s*fixed;[^}]*z-index:\s*40;/);
  });

  it("keeps the reconnect chip in normal flow, so it cannot sit on the usage line", () => {
    const chip = block.match(/\.sourcecado-connection\s*\{([^}]*)\}/)?.[1] ?? "";
    expect(chip).toMatch(/display:\s*flex;/);
    expect(chip).not.toMatch(/position:\s*(absolute|fixed)/);
    expect(chip).not.toMatch(/\b(top|bottom|left|right):/);
  });

  it("tints a failed turn with the error tokens and spins the reconnect icon only in warn ink", () => {
    expect(block).toMatch(
      /\.sourcecado-notice\.sourcecado-notice-failed\s*\{[^}]*background:\s*var\(--error-bg\);/,
    );
    expect(block).toMatch(
      /\.sourcecado-connection\[data-state="reconnecting"\] \.sourcecado-status-icon-running\s*\{[^}]*color:\s*var\(--warn\);/,
    );
  });

  it("splits a failed row into two lines on narrow screens so the reason is not cut short", () => {
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.sourcecado-step-failed \.sourcecado-step-toggle\s*\{[^}]*flex-wrap:\s*wrap;/,
    );
  });
});
