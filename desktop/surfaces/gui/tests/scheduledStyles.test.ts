import { describe, expect, it } from "vitest";

import { styles as css } from "./cssBundle";

describe("Scheduled route styles", () => {
  it("keeps schedule actions and form controls at least 44px tall", () => {
    expect(css).toMatch(/\.scheduled-page button[\s\S]*?min-height:\s*44px;/);
    expect(css).toMatch(
      /\.schedule-create-form :where\(input, select\)[\s\S]*?min-height:\s*44px;/,
    );
    expect(css).toMatch(/\.schedule-thread-link[\s\S]*?min-height:\s*44px;/);
    expect(css).toMatch(/\.schedule-history button[\s\S]*?min-height:\s*44px;/);
  });

  it("uses compact task cards and a dedicated history-detail grid", () => {
    expect(css).toMatch(/\.schedule-task-list\s*\{[^}]*display:\s*grid;/);
    expect(css).toMatch(/\.scheduled-page\s*\{[^}]*max-width:\s*900px;/);
    expect(css).toMatch(/\.schedule-task-card\s*\{[^}]*max-width:\s*405px;/);
    expect(css).toMatch(
      /\.schedule-detail-grid\s*\{[^}]*grid-template-columns:\s*320px\s+minmax\(0,\s*1fr\);[^}]*gap:\s*42px;/,
    );
    expect(css).toMatch(/\.schedule-markdown-table\s*\{[^}]*overflow-x:\s*auto;/);
  });

  it("stacks list and detail actions on narrow screens", () => {
    expect(css).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.scheduled-page-header,[\s\S]*?\.schedule-detail-header[\s\S]*?flex-direction:\s*column;/,
    );
    expect(css).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.schedule-detail-grid[\s\S]*?grid-template-columns:\s*minmax\(0,\s*1fr\);/,
    );
  });

  it("keeps keyboard focus visible and disabled lifecycle stubs honest", () => {
    expect(css).toMatch(/\.schedule-task-card:focus-visible[\s\S]*?outline:/);
    expect(css).toMatch(
      /\.schedule-detail-actions button:disabled[\s\S]*?cursor:\s*not-allowed;/,
    );
    expect(css).toMatch(
      /\.schedule-active-switch input\s*\{[^}]*appearance:\s*none;[^}]*border-radius:\s*999px;/,
    );
    expect(css).toMatch(/\.schedule-active-switch input\s*\{[^}]*width:\s*39px;[^}]*height:\s*23px;/);
    expect(css).toMatch(/\.schedule-markdown a\s*\{[^}]*color:\s*var\(--accent-deep\);/);
  });

  it("visually distinguishes waiting, partial, failed, and unknown receipts", () => {
    expect(css).toMatch(/\.schedule-receipt\.receipt-waiting_approval\s*\{[^}]*border-color:\s*var\(--warn\);/);
    expect(css).toMatch(/\.schedule-receipt\.receipt-partial\s*\{[^}]*border-color:\s*var\(--warn\);/);
    expect(css).toMatch(/\.schedule-receipt\.receipt-failed\s*\{[^}]*border-color:\s*var\(--error\);/);
    expect(css).toMatch(/\.schedule-receipt\.receipt-unknown\s*\{[^}]*border-color:\s*var\(--warn\);/);
  });

  it("disables schedule skeleton animation for reduced motion and styles the rail Inbox badge", () => {
    expect(css).toMatch(
      /@media \(prefers-reduced-motion: reduce\)[\s\S]*?\.schedule-job-skeleton[\s\S]*?animation:\s*none;/,
    );
    expect(css).toMatch(/\.rail-inbox-badge\s*\{[^}]*min-width:\s*20px;/);
  });
});
