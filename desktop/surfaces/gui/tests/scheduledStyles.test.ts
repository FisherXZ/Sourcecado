import { describe, expect, it } from "vitest";

import { styles as css } from "./cssBundle";

describe("Scheduled route styles", () => {
  it("keeps schedule actions and form controls at least 44px tall", () => {
    expect(css).toMatch(/\.scheduled-page button[\s\S]*?min-height:\s*44px;/);
    expect(css).toMatch(
      /\.schedule-create-form :where\(input, select\)[\s\S]*?min-height:\s*44px;/,
    );
    expect(css).toMatch(/\.schedule-thread-link[\s\S]*?min-height:\s*44px;/);
  });

  it("stacks job and form actions on narrow screens", () => {
    expect(css).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.scheduled-page-header,[\s\S]*?\.schedule-job-header[\s\S]*?flex-direction:\s*column;/,
    );
    expect(css).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.schedule-job-header button[\s\S]*?width:\s*100%;/,
    );
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
