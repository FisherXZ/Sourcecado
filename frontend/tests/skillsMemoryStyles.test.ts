import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { styles } from "./cssBundle";

const sheet = readFileSync("src/styles/skills-memory.css", "utf8");

describe("Skills and Memory styles", () => {
  it("uses tokens only and keeps text at 12px or larger", () => {
    expect(sheet).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    for (const size of sheet.matchAll(/font-size:\s*(\d+)px/g)) {
      expect(Number(size[1])).toBeGreaterThanOrEqual(12);
    }
  });

  it("shows the skills list beside the skill and stacks them on narrow screens", () => {
    expect(styles).toMatch(
      /\.sk-layout\s*\{[^}]*display:\s*grid;[^}]*grid-template-columns:\s*minmax\(240px,\s*300px\)\s+minmax\(0,\s*1fr\);/,
    );
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.sk-layout\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\);/,
    );
  });

  it("keeps every button touch sized on narrow screens", () => {
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.sm-btn,\s*\.sm-icon-btn\s*\{[^}]*min-height:\s*44px;/,
    );
    expect(styles).toMatch(/@media \(max-width: 767px\)[\s\S]*?\.sm-icon-btn\s*\{[^}]*width:\s*44px;/);
  });

  it("marks a memory waiting for Keep with the warning tint, not a raw colour", () => {
    expect(styles).toMatch(
      /\.sm-pill\.is-warn\s*\{[^}]*background:\s*var\(--warn-bg\);[^}]*color:\s*var\(--warn\);/,
    );
  });
});
