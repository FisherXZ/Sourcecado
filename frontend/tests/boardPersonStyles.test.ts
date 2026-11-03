import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const styles = readFileSync("src/styles.css", "utf8");

describe("Contacts and person-file styles", () => {
  it("uses a dense bounded Contacts table with safe horizontal overflow", () => {
    expect(styles).toMatch(
      /\.contacts-list\s*\{[^}]*border:\s*1px solid var\(--border\);[^}]*background:\s*var\(--surface\);/,
    );
    expect(styles).toMatch(
      /\.contacts-table-scroll\s*\{[^}]*overflow-x:\s*auto;/,
    );
    expect(styles).toMatch(
      /\.contacts-table\s*\{[^}]*width:\s*100%;[^}]*min-width:\s*820px;[^}]*border-collapse:\s*collapse;/,
    );
    expect(styles).toMatch(
      /\.contacts-table tbody tr\s*\{[^}]*height:\s*38px;[^}]*border-bottom:\s*1px solid var\(--border\);/,
    );
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.contacts-filters button,\s*\.contacts-search input\s*\{[^}]*min-height:\s*44px;/,
    );
  });

  it("keeps person-file header controls touch sized on narrow screens", () => {
    // The sequence pill, Open chat and Edit details live in person-file.css.
    const personFile = readFileSync("src/styles/person-file.css", "utf8");
    expect(personFile).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.pf-btn,\s*\.pf-sequence select,[^{]*\{[^}]*min-height:\s*44px;/,
    );
    expect(personFile).toMatch(/\.pf-panel\.is-inactive\s*\{[^}]*display:\s*none;/);
    expect(personFile).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
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
