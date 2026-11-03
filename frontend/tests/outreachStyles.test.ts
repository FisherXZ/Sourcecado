import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const styles = readFileSync("src/styles/crm-interactions.css", "utf8");

describe("Outreach and task card styles", () => {
  it("keeps approval and send controls touch sized and the reviewed body readable", () => {
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?:is\(\.task-list, \.person-section, \.description-editor, \.undo-toast\) :is\(button, a\):is\(\.crm-btn[^{]*\{[^}]*min-height:\s*44px;/,
    );
    expect(styles).toMatch(/\.crm-mail-body\s*\{[^}]*white-space:\s*pre-wrap;/);
    expect(styles).toMatch(/\.crm-mail-body\s*\{[^}]*overflow:\s*auto;/);
  });

  it("stays on the warm token palette instead of hardcoded colour", () => {
    expect(styles).toMatch(/background:\s*var\(--surface\)/);
    expect(styles).toMatch(/color:\s*var\(--error\)/);
    expect(styles).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  });

  it("keeps CRM text at 13px or larger", () => {
    const sizes = [...styles.matchAll(/font-size:\s*(\d+)px/g)].map((match) => Number(match[1]));
    expect(sizes.length).toBeGreaterThan(0);
    expect(Math.min(...sizes)).toBeGreaterThanOrEqual(13);
  });
});
