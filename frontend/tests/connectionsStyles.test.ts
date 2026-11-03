import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync("src/styles/connections.css", "utf8");
const narrow = css.slice(css.indexOf("@media (max-width: 767px)"));

describe("Connections styles", () => {
  it("gives every connection control a 44px touch target on narrow screens", () => {
    expect(narrow).toMatch(/\.connection-button\s*\{[^}]*min-height:\s*44px;[^}]*min-width:\s*44px;/);
    expect(narrow).toMatch(/\.connection-icon-button\s*\{[^}]*width:\s*44px;[^}]*height:\s*44px;/);
    expect(narrow).toMatch(/\.connections-search\s*\{[^}]*height:\s*44px;/);
  });

  it("replaces the catalog with the detail panel below 1180px", () => {
    expect(css).toMatch(
      /@media \(max-width: 1179px\)[\s\S]*?\.connections-page\.has-detail \.connections-catalog,\s*\.connections-page\.has-detail \.connections-actions\s*\{\s*display:\s*none;/,
    );
  });

  it("uses tinted pills with dark text, not saturated dots", () => {
    expect(css).toMatch(/\.connection-status\.tone-ok\s*\{[^}]*background:\s*var\(--accent-tint\);[^}]*color:\s*var\(--accent-deep\);/);
    expect(css).toMatch(/\.connection-status\.tone-warn\s*\{[^}]*background:\s*var\(--warn-bg\);/);
    expect(css).toMatch(/\.connection-status\.tone-error\s*\{[^}]*background:\s*var\(--error-bg\);/);
  });

  it("keeps focus visible and stops motion for reduced-motion users", () => {
    expect(css).toMatch(/\.connection-row:focus-visible,[\s\S]*?outline:\s*2px solid var\(--accent\);/);
    expect(css).toMatch(
      /@media \(prefers-reduced-motion: reduce\)[\s\S]*?\.connection-skeleton-row,[\s\S]*?animation:\s*none;/,
    );
  });
});
