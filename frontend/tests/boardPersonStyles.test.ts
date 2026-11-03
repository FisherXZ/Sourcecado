import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const styles = readFileSync("src/styles.css", "utf8");
const contacts = readFileSync("src/styles/contacts.css", "utf8");

describe("Contacts and person-file styles", () => {
  it("uses a dense bounded Contacts table with safe horizontal overflow", () => {
    expect(contacts).toMatch(
      /\.contacts-list\s*\{[^}]*border:\s*1px solid var\(--border\);[^}]*background:\s*var\(--surface\);/,
    );
    expect(contacts).toMatch(
      /\.contacts-table-scroll\s*\{[^}]*overflow-x:\s*auto;/,
    );
    expect(contacts).toMatch(
      /\.contacts-table\s*\{[^}]*width:\s*100%;[^}]*min-width:\s*960px;[^}]*border-collapse:\s*collapse;/,
    );
    expect(contacts).toMatch(
      /\.contacts-table tbody tr\s*\{[^}]*height:\s*38px;[^}]*border-bottom:\s*1px solid var\(--border\);/,
    );
    // Light vertical gridlines between columns, as in a records table.
    expect(contacts).toMatch(
      /\.contacts-table th \+ th,\s*\.contacts-table td \+ td\s*\{[^}]*border-left:\s*1px solid var\(--border\);/,
    );
    expect(contacts).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.contacts-filters button,\s*\.contacts-chip,\s*\.contacts-search\s*\{[^}]*height:\s*44px;/,
    );
    expect(contacts).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.contacts-icon-button\s*\{[^}]*width:\s*44px;[^}]*height:\s*44px;/,
    );
  });

  it("keeps Contacts on tokens with no text below 13px", () => {
    expect(contacts).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    for (const size of contacts.matchAll(/font-size:\s*(\d+)px/g)) {
      expect(Number(size[1])).toBeGreaterThanOrEqual(13);
    }
    // Responsive overrides must load after the CRM sheets they adjust.
    const responsive = styles.indexOf('@import "./styles/responsive.css";');
    for (const sheet of ["contacts.css", "crm-details.css"]) {
      const at = styles.indexOf(`@import "./styles/${sheet}";`);
      expect(at).toBeGreaterThan(-1);
      expect(at).toBeLessThan(responsive);
    }
  });

  it("keeps person-file header controls touch sized on narrow screens", () => {
    // The sequence pill, Open chat and Edit details live in person-file.css.
    const personFile = readFileSync("src/styles/person-file.css", "utf8");
    expect(personFile).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.pf-btn,\s*\.pf-sequence,[^{]*\{[^}]*min-height:\s*44px;/,
    );
    expect(personFile).toMatch(/\.pf-panel\.is-inactive\s*\{[^}]*display:\s*none;/);
    expect(personFile).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  });

  it("floats the preview over the table from 1100px and keeps narrow forms inside it", () => {
    expect(contacts).toMatch(
      /@media \(min-width: 1100px\)\s*\{\s*\.board-page \.person-preview\s*\{[^}]*position:\s*fixed;[^}]*width:\s*420px;[^}]*box-shadow:/,
    );
    // The side-by-side grid only applies between 1024px and 1099px.
    expect(styles).toMatch(
      /@media \(min-width: 1024px\) and \(max-width: 1099px\)\s*\{\s*\.board-page\.has-preview\s*\{[^}]*display:\s*grid;/,
    );
    // The header wraps instead of truncating the name.
    expect(contacts).toMatch(/\.person-preview \.preview-titlebar\s*\{[^}]*flex-wrap:\s*wrap;/);
    expect(contacts).not.toMatch(/\.preview-titlebar h2\s*\{[^}]*text-overflow:\s*ellipsis;/);
    // The flag form wraps, so the date chip cannot push past the card at 390px.
    const details = readFileSync("src/styles/crm-details.css", "utf8");
    expect(details).toMatch(/\.person-preview \.preview-flag\s*\{[^}]*flex-wrap:\s*wrap;/);
    expect(details).toMatch(/\.person-preview \.flag-field input\s*\{[^}]*min-width:\s*0;/);
  });

  it("keeps CRM interaction states on tokens with no text below 13px", () => {
    const details = readFileSync("src/styles/crm-details.css", "utf8");
    expect(details).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    for (const size of details.matchAll(/font-size:\s*(\d+)px/g)) {
      expect(Number(size[1])).toBeGreaterThanOrEqual(13);
    }
    // Touch sizing for the new ghost buttons and inline inputs.
    expect(details).toMatch(
      /@media \(max-width: 767px\)\s*\{\s*\.pf :is\(\.pd-btn, \.enrichment-btn, \.sequence-undo, \.pd-value\),[^{]*\{[^}]*min-height:\s*44px;/,
    );
  });

  it("keeps attention and Gmail warnings on warm tokens", () => {
    // DESIGN.md reserves colour on a dense row for real attention, and the
    // warm "needs attention" pair is already defined in both themes. A
    // hardcoded colour here would break dark mode silently.
    expect(contacts).toMatch(
      /\.contacts-pill\.is-warn\s*\{[^}]*background:\s*var\(--warn-bg\);[^}]*color:\s*var\(--warn\);/,
    );
    expect(contacts).toMatch(
      /\.contacts-mail-chip\.is-warn\s*\{[^}]*background:\s*var\(--warn-bg\);[^}]*color:\s*var\(--warn\);/,
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
