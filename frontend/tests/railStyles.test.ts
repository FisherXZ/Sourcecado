import { describe, expect, it } from "vitest";

import { styles } from "./cssBundle";

// Contracts for the rebuilt sidebar (issue #227). jsdom does not lay out CSS,
// so these read the stylesheet text. The rendered behavior is covered in
// shell.test.tsx and globalRail.test.tsx.

const RAIL_SELECTOR =
  /\.(rail-|thread-|operator-|new-chat-button|app-identity|app-mark)/;

function railFontSizes(): Array<{ selector: string; px: number }> {
  const css = styles.replace(/\/\*[\s\S]*?\*\//g, "");
  const found: Array<{ selector: string; px: number }> = [];
  for (const [, selector, body] of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (!RAIL_SELECTOR.test(selector)) continue;
    const size = body.match(/font(?:-size)?:\s*(?:[\w-]+\s+)*?(\d+(?:\.\d+)?)px/);
    if (size) found.push({ selector: selector.trim(), px: Number(size[1]) });
  }
  return found;
}

describe("sidebar styles", () => {
  it("keeps every piece of rail text at 13px or larger", () => {
    const sizes = railFontSizes();
    expect(sizes.length).toBeGreaterThan(8);
    expect(sizes.filter(({ px }) => px < 13)).toEqual([]);
  });

  it("sizes rows at 32px and the badge at 13px", () => {
    expect(styles).toMatch(/\.rail-link\s*\{[^}]*min-height:\s*32px;/);
    expect(styles).toMatch(/\.thread-row\s*\{[^}]*min-height:\s*32px;/);
    expect(styles).toMatch(/\.rail-inbox-badge\s*\{[^}]*font-size:\s*13px;/);
  });

  it("shrinks the wide rail to a 48px icon column and shows tooltips", () => {
    expect(styles).toMatch(
      /@media \(min-width: 1180px\)[\s\S]*?\.app-rail\.is-collapsed\s*\{[^}]*width:\s*48px;/,
    );
    expect(styles).toMatch(
      /\.is-collapsed \[data-tooltip\]:is\(:hover, :focus-visible\)::after\s*\{[^}]*content:\s*attr\(data-tooltip\);/,
    );
  });

  it("hides rename and pin until hover or focus, and shows them on touch", () => {
    expect(styles).toMatch(/\.thread-actions\s*\{[^}]*opacity:\s*0;/);
    expect(styles).toMatch(
      /\.thread-row:hover \.thread-actions,\s*\.thread-actions:focus-within\s*\{[^}]*opacity:\s*1;/,
    );
    expect(styles).toMatch(
      /\.thread-row:has\(:focus-visible\) \.thread-actions\s*\{[^}]*opacity:\s*1;/,
    );
    expect(styles).toMatch(/@media \(hover: none\)\s*\{\s*\.thread-actions\s*\{[^}]*opacity:\s*1;/);
  });

  it("clips long thread titles with an ellipsis and keeps the action strip flush to the row edge", () => {
    expect(styles).toMatch(
      /\.thread-title\s*\{[^}]*overflow:\s*hidden;[^}]*text-overflow:\s*ellipsis;[^}]*white-space:\s*nowrap;/,
    );
    expect(styles).toMatch(/\.thread-actions\s*\{[^}]*right:\s*0;/);
    expect(styles).toMatch(/\.thread-actions\s*\{[^}]*border-radius:\s*0 7px 7px 0;/);
    expect(styles).toMatch(/\.thread-action:focus-visible\s*\{[^}]*outline-offset:\s*-2px;/);
  });

  it("shows a count as a corner dot, clear of the icon, in the icon column", () => {
    const dot = styles.match(
      /@media \(min-width: 1180px\)[\s\S]*?\.is-collapsed \.rail-inbox-badge\s*\{([^}]*)\}/,
    );
    expect(dot).not.toBeNull();
    // A 16px icon is centered in a 32px row, so it spans 8px to 24px. An 8px
    // dot at the top right corner spans 24px to 32px across and 0 to 8px down.
    expect(dot![1]).toMatch(/top:\s*0;/);
    expect(dot![1]).toMatch(/right:\s*0;/);
    expect(dot![1]).toMatch(/width:\s*8px;/);
    expect(dot![1]).toMatch(/height:\s*8px;/);
    expect(styles).toMatch(/\.is-collapsed \.rail-badge-count\s*\{[^}]*clip-path:\s*inset\(50%\);/);
  });

  it("keeps the edge sheet off screen until it opens below 1180px", () => {
    expect(styles).toMatch(
      /@media \(max-width: 1179px\)[\s\S]*?\.app-rail\s*\{[^}]*transform:\s*translateX\(-102%\);/,
    );
    expect(styles).toMatch(
      /@media \(max-width: 1179px\)[\s\S]*?\.app-rail\.is-open\s*\{[^}]*transform:\s*translateX\(0\);/,
    );
    expect(styles).toMatch(
      /@media \(max-width: 1179px\)[\s\S]*?\.rail-collapse-toggle\s*\{[^}]*display:\s*none;/,
    );
  });
});
