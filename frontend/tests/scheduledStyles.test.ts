import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { styles as bundle } from "./cssBundle";

const css = readFileSync("src/styles/scheduled.css", "utf8");
const narrow = css.slice(css.indexOf("@media (max-width: 767px)"));

describe("Scheduled styles", () => {
  it("gives buttons, links and fields a 44px touch target on narrow screens", () => {
    expect(narrow).toMatch(/\.scheduled-button\s*\{[^}]*min-height:\s*44px;[^}]*min-width:\s*44px;/);
    expect(narrow).toMatch(/\.scheduled-run-thread\s*\{[^}]*width:\s*44px;[^}]*height:\s*44px;/);
    expect(narrow).toMatch(/\.scheduled-field :where\(input, select\)\s*\{[^}]*min-height:\s*44px;/);
  });

  it("turns table rows into two-line cards on narrow screens", () => {
    expect(narrow).toMatch(/\.scheduled-row\s*\{[^}]*display:\s*grid;/);
    expect(narrow).toMatch(/\.scheduled-cell svg\s*\{[^}]*display:\s*block;/);
  });

  it("stacks the run list above the receipt below 1024px", () => {
    expect(css).toMatch(/\.scheduled-history-grid\s*\{[^}]*grid-template-columns:\s*320px minmax\(0, 1fr\);/);
    expect(css).toMatch(
      /@media \(max-width: 1023px\)[\s\S]*?\.scheduled-history-grid\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\);/,
    );
  });

  it("marks failed and attention receipts with a tinted border", () => {
    expect(css).toMatch(/\.scheduled-receipt\.receipt-failed\s*\{[^}]*var\(--error\)/);
    expect(css).toMatch(
      /\.scheduled-receipt\.receipt-waiting_approval,\s*\.scheduled-receipt\.receipt-partial,\s*\.scheduled-receipt\.receipt-unknown\s*\{[^}]*var\(--warn\)/,
    );
  });

  it("keeps focus visible and stops motion for reduced-motion users", () => {
    expect(css).toMatch(/\.scheduled-button:focus-visible,[\s\S]*?outline:\s*2px solid var\(--accent\);/);
    expect(css).toMatch(
      /@media \(prefers-reduced-motion: reduce\)[\s\S]*?\.scheduled-skeleton span\s*\{\s*animation:\s*none;/,
    );
    expect(bundle).toMatch(/\.rail-inbox-badge\s*\{[^}]*min-width:\s*20px;/);
  });
});
