import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const styles = readFileSync("src/styles.css", "utf8");

describe("Skills and Settings route styles", () => {
  it("keeps persona and recovery actions at least 44px tall on narrow layouts", () => {
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.persona-options button,\s*\.route-error button\s*\{[^}]*min-height:\s*44px;/,
    );
  });
});
