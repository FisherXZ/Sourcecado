import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const styles = readFileSync("src/styles.css", "utf8");

describe("Outreach panel styles", () => {
  it("keeps approval controls touch sized and the reviewed body readable", () => {
    expect(styles).toMatch(
      /\.person-outreach-compose button,\s*\n\.person-outreach-actions button\s*\{[^}]*min-height:\s*44px;/,
    );
    expect(styles).toMatch(
      /\.person-outreach-body\s*\{[^}]*white-space:\s*pre-wrap;/,
    );
    expect(styles).toMatch(
      /\.person-outreach-body\s*\{[^}]*overflow:\s*auto;/,
    );
  });

  it("stays on the warm token palette instead of hardcoded colour", () => {
    const block = styles.slice(styles.indexOf(".person-outreach-compose,"));
    expect(block).toMatch(/background:\s*var\(--surface\)/);
    expect(block).toMatch(/color:\s*var\(--error\)/);
    expect(block).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  });
});
