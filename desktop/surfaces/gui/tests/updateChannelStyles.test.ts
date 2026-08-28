import { describe, expect, it } from "vitest";

import { styles } from "./cssBundle";

describe("release channel styles", () => {
  it("marks a preview build with the warm warning tint, never a saturated dot", () => {
    expect(styles).toMatch(
      /\.channel-pill-preview\s*\{[^}]*background:\s*var\(--warn-bg\);[^}]*color:\s*var\(--warn\);/,
    );
    expect(styles).toMatch(
      /\.channel-pill-stable\s*\{[^}]*background:\s*var\(--accent-tint\);[^}]*color:\s*var\(--accent-deep\);/,
    );
  });

  it("keeps the preview badge out of the shell layout and under the alert layer", () => {
    expect(styles).toMatch(/\.preview-channel-badge\s*\{[^}]*position:\s*fixed;/);
    expect(styles).toMatch(/\.preview-channel-badge\s*\{[^}]*z-index:\s*55;/);
  });

  it("uses design tokens rather than literal colours", () => {
    const blocks =
      styles.match(/\.(channel-pill[\w-]*|channel-rollback|preview-channel[\w-]*)[^{]*\{[^}]*\}/g) ||
      [];
    expect(blocks.length).toBeGreaterThan(4);
    for (const block of blocks) {
      expect(block).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    }
  });

  it("uses the friendly-precise radius band", () => {
    expect(styles).toMatch(/\.channel-rollback\s*\{[^}]*border-radius:\s*8px;/);
    expect(styles).toMatch(/\.channel-pill\s*\{[^}]*border-radius:\s*9999px;/);
  });
});
