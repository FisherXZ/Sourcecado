import { describe, expect, it } from "vitest";

import { styles } from "./cssBundle";

describe("Skills and Settings route styles", () => {
  it("uses a compact two-pane skills catalog that stacks for narrow screens", () => {
    expect(styles).toMatch(
      /\.skills-layout\s*\{[^}]*display:\s*grid;[^}]*grid-template-columns:\s*minmax\(260px,\s*340px\)\s+minmax\(0,\s*1fr\);/,
    );
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.skills-layout\s*\{[^}]*grid-template-columns:\s*1fr;/,
    );
  });

  it("keeps skill rows touch sized and disabled management visibly inert", () => {
    expect(styles).toMatch(/\.skill-row\s*\{[^}]*min-height:\s*58px;/);
    expect(styles).toMatch(
      /\.skill-management button:disabled\s*\{[^}]*cursor:\s*not-allowed;[^}]*opacity:/,
    );
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.skill-row\s*\{[^}]*min-height:\s*60px;/,
    );
  });

  it("disables skill skeleton animation when reduced motion is requested", () => {
    expect(styles).toMatch(
      /@media \(prefers-reduced-motion: reduce\)[\s\S]*?\.skill-skeleton::after\s*\{[^}]*animation:\s*none;/,
    );
  });

  it("keeps persona and recovery actions at least 44px tall on narrow layouts", () => {
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.persona-options button,\s*\.route-error button\s*\{[^}]*min-height:\s*44px;/,
    );
  });

  it("keeps workspace grant controls bounded and touch sized", () => {
    expect(styles).toMatch(/\.workspace-settings\s*\{[^}]*grid-column:\s*1\s*\/\s*-1;/);
    expect(styles).toMatch(
      /\.workspace-add-form[^}]*[\s\S]*?\.workspace-settings button[^}]*min-height:\s*40px;/,
    );
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.workspace-settings button[^}]*min-height:\s*44px;/,
    );
  });

  it("renders provider verification as compact divided operational rows", () => {
    expect(styles).toMatch(
      /\.provider-verification-list\s*\{[^}]*display:\s*grid;[^}]*list-style:\s*none;/,
    );
    expect(styles).toMatch(
      /\.provider-verification-list > li\s*\{[^}]*border-top:\s*1px solid var\(--border\);/,
    );
    expect(styles).toMatch(
      /\.provider-verification-status\.verified\s*\{[^}]*background:\s*var\(--accent-tint\);[^}]*color:\s*var\(--accent-deep\);/,
    );
  });
});
