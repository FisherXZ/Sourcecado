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
});
