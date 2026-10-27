import { describe, expect, it } from "vitest";

import { styles } from "./cssBundle";

// The recovery block (#236): failed step icon buttons, the overflow menu, the
// failed-turn notice and the reconnect chip. These checks read the stylesheet
// text, so they guard the declarations, not the rendered boxes.
const start = styles.indexOf("Failed step and failed turn recovery (#236)");
// The block ends with its last narrow-screen rule.
const endAt = styles.indexOf(".sourcecado-step-failed .sourcecado-step-status", start);
const block = styles.slice(start, styles.indexOf("}", endAt) + 1);

describe("recovery styles", () => {
  it("finds its own block", () => {
    expect(start).toBeGreaterThan(0);
    expect(block).toContain(".sourcecado-connection");
  });

  it("keeps every size at 13px or more and uses muted ink only on a button that is off", () => {
    const sizes = [...block.matchAll(/font-size:\s*(\d+(?:\.\d+)?)px/g)].map((m) => Number(m[1]));
    expect(sizes.length).toBeGreaterThan(0);
    expect(Math.min(...sizes)).toBeGreaterThanOrEqual(13);
    // Muted ink is allowed on a control that is off, which is not information.
    for (const [, selector] of block.matchAll(/([^{}]+)\{[^}]*\bcolor:\s*var\(--muted\)[^}]*\}/g)) {
      expect(selector).toMatch(/aria-disabled/);
    }
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

  it("shows an off Retry as off: muted ink on a raised ground with a dashed edge, not a faded icon", () => {
    expect(styles).toMatch(
      /\.sourcecado-message-action\[aria-disabled="true"\],\s*\.sourcecado-message-action\[aria-disabled="true"\]:hover\s*\{[^}]*border:\s*1px dashed var\(--muted\);[^}]*background:\s*var\(--raised\);[^}]*color:\s*var\(--muted\);/,
    );
    expect(styles).not.toMatch(/\[aria-disabled="true"\] \.sourcecado-icon\s*\{[^}]*opacity/);
    expect(styles).toMatch(
      /\.sourcecado-notice-head \.sourcecado-step-icon-button\[aria-disabled="true"\],[^{]*\{[^}]*border:\s*1px dashed var\(--muted\);[^}]*color:\s*var\(--muted\);/,
    );
  });

  it("keeps that muted ink at 4.5:1 or better on the raised ground, in both themes", () => {
    const luminance = (hex: string) => {
      const channels = [1, 3, 5].map((at) => {
        const value = parseInt(hex.slice(at, at + 2), 16) / 255;
        return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * channels[0]! + 0.7152 * channels[1]! + 0.0722 * channels[2]!;
    };
    const ratio = (a: string, b: string) => {
      const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x);
      return (light! + 0.05) / (dark! + 0.05);
    };
    const light = styles.match(/:root\s*\{([^}]*)\}/)?.[1] ?? "";
    const dark = styles.match(/:root\[data-theme="dark"\]\s*\{([^}]*)\}/)?.[1] ?? "";
    const token = (block: string, name: string) =>
      block.match(new RegExp(`--${name}:\\s*(#[0-9A-Fa-f]{6})`))?.[1] ?? "";
    expect(ratio(token(light, "muted"), token(light, "raised"))).toBeGreaterThanOrEqual(4.5);
    expect(ratio(token(dark, "muted"), token(dark, "raised"))).toBeGreaterThanOrEqual(4.5);
  });

  it("shows the reason line in 13px body ink, with the bar on hover and always on touch", () => {
    const line = styles.match(/\.sourcecado-retry-reason\s*\{([^}]*)\}/)?.[1] ?? "";
    expect(line).toMatch(/font-size:\s*13px;/);
    expect(line).toMatch(/color:\s*var\(--text\);/);
    expect(styles).toMatch(
      /\.sourcecado-assistant-message:is\(:hover, :focus-within\)\s*\.sourcecado-message-actions\s*\+ \.sourcecado-retry-reason\s*\{[^}]*opacity:\s*1;/,
    );
    // The touch rule comes after the hidden default, so it wins.
    const hidden = styles.indexOf(".sourcecado-message-actions + .sourcecado-retry-reason {\n  opacity: 0;");
    const touch = styles.indexOf("@media (hover: none), (max-width: 767px) {\n  .sourcecado-message-actions + .sourcecado-retry-reason");
    expect(hidden).toBeGreaterThan(0);
    expect(touch).toBeGreaterThan(hidden);
  });
});
