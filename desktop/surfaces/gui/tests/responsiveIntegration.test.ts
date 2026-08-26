import { describe, expect, it } from "vitest";

import { styles } from "./cssBundle";

function atRuleBlocks(marker: string): string[] {
  const blocks: string[] = [];
  let from = 0;
  while (true) {
    const start = styles.indexOf(marker, from);
    if (start < 0) return blocks;
    const open = styles.indexOf("{", start);
    let depth = 0;
    for (let index = open; index < styles.length; index += 1) {
      if (styles[index] === "{") depth += 1;
      if (styles[index] === "}") depth -= 1;
      if (depth === 0) {
        blocks.push(styles.slice(start, index + 1));
        from = index + 1;
        break;
      }
    }
  }
}

describe("whole-app responsive integration", () => {
  it("keeps the full chat height chain when the tablet shell stops using flex", () => {
    expect(styles).toMatch(
      /\.app-shell\s*\{[^}]*height:\s*100%;[^}]*min-height:\s*0;/,
    );
    expect(styles).toMatch(
      /\.shell-content\s*\{[^}]*height:\s*100%;[^}]*min-height:\s*0;/,
    );
    expect(styles).toMatch(
      /\.chat-page\s*\{[^}]*height:\s*100%;[^}]*min-height:\s*0;/,
    );
    expect(styles).toMatch(
      /\.sourcecado-chat-workspace\s*\{[^}]*height:\s*100%;[^}]*min-height:\s*0;/,
    );
  });

  it("keeps every narrow form control at a readable non-zooming size", () => {
    expect(styles).toMatch(
      /@media \(max-width: 1179px\)[\s\S]*?#root :where\(input, textarea, select\)\s*\{[^}]*font-size:\s*16px;/,
    );
  });

  it("reserves tablet and short-landscape header space for the rail trigger", () => {
    const tabletBlocks = atRuleBlocks("@media (max-width: 1179px)");
    expect(
      tabletBlocks.some((block) =>
        /\.sourcecado-thread-header\s*\{[^}]*padding-top:\s*72px;/.test(block),
      ),
    ).toBe(true);
  });
});
