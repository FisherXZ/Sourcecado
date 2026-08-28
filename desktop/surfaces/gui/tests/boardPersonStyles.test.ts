import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const styles = readFileSync("src/styles.css", "utf8");

describe("Board and person-file styles", () => {
  it("uses scoped responsive grids and touch-sized sequence controls", () => {
    expect(styles).toMatch(
      /\.board-grid\s*\{[^}]*grid-template-columns:\s*repeat\(3, minmax\(0, 1fr\)\);/,
    );
    expect(styles).toMatch(
      /\.person-sequence-action\s*\{[^}]*min-height:\s*44px;/,
    );
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.board-grid,\s*\.person-summary-grid\s*\{[^}]*grid-template-columns:\s*1fr;/,
    );
    expect(styles).toMatch(
      /\.person-chat-action button\s*\{[^}]*min-height:\s*44px;/,
    );
  });

  it("keeps the follow-up flag on warm tokens and the reply check touch sized", () => {
    // DESIGN.md reserves colour on a dense row for real attention, and the
    // warm "needs attention" pair is already defined in both themes. A
    // hardcoded colour here would break dark mode silently.
    expect(styles).toMatch(
      /\.board-row-flag\s*\{[^}]*background:\s*var\(--warn-bg\);[^}]*color:\s*var\(--warn\);/,
    );
    expect(styles).toMatch(
      /\.board-page-actions button\s*\{[^}]*min-height:\s*44px;/,
    );
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.board-page-actions button\s*\{[^}]*min-height:\s*44px;[^}]*min-width:\s*44px;/,
    );
  });

  it("renders brief claims as a quiet divided list with tinted state pills", () => {
    expect(styles).toMatch(
      /\.person-claims\s*\{[^}]*display:\s*grid;[^}]*list-style:\s*none;/,
    );
    expect(styles).toMatch(
      /\.person-claim\s*\{[^}]*border-bottom:\s*1px solid var\(--border\);/,
    );
    expect(styles).toMatch(
      /\.person-claim-state\.is-current\s*\{[^}]*background:\s*var\(--accent-tint\);[^}]*color:\s*var\(--accent-deep\);/,
    );
    expect(styles).toMatch(
      /\.person-claim-state\.is-conflicting\s*\{[^}]*background:\s*var\(--error-bg\);[^}]*color:\s*var\(--error\);/,
    );
    expect(styles).toMatch(
      /\.person-claim-refs\s*\{[^}]*font-family:\s*ui-monospace, "Geist Mono", monospace;/,
    );
  });

  it("keeps handoff review controls touch sized and on the accent", () => {
    expect(styles).toMatch(/\.person-handoff textarea\s*\{[^}]*min-height:\s*44px;/);
    expect(styles).toMatch(
      /\.person-handoff button\s*\{[^}]*min-height:\s*44px;[^}]*background:\s*var\(--accent\);/,
    );
  });
});
