import { describe, expect, it } from "vitest";

import { styles } from "./cssBundle";

// Regression guard for the M4 touch-target finding (QA2 review round):
// recovery, activity, approval-disclosure, copy, queue, and rail controls
// stayed under 44px on touch viewports even though a `min-height: 44px`
// rule already existed elsewhere in the file. Width is the half that gets
// missed — it comes incidentally from padding plus label length, so a short
// label like "Retry", "Up", or "Copy response" silently lands under 44px
// even once height is fixed. Each check below requires BOTH dimensions (or
// `width`, for the fixed-size thread-action square) to be at least 44px,
// asserted inside the same rule body, inside a
// `@media (max-width: 767px)` block, on the exact selector that was fixed.
//
// What this catches: the touch-sizing declaration disappearing, shrinking
// back under 44px, or losing the width half while keeping the height half
// — exactly how M4 slipped past the pre-existing suite, which only ever
// asserted min-height.
// What this does NOT catch: the actual rendered/computed box size. A
// higher-specificity rule elsewhere, inline styles, intrinsic content
// sizing, or real browser/jsdom layout are outside what a CSS-text
// assertion can see — this is a stylesheet-text guard, not a layout test.
describe("44px touch targets on narrow viewports (M4 regression guard)", () => {
  it("sizes recovery actions (Retry/Repair/Continue without/Failure details)", () => {
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.sourcecado-recovery-actions :where\(button, a\)\s*\{[^}]*min-height:\s*44px;[^}]*min-width:\s*44px;/,
    );
  });

  it("sizes the activity trace disclosure", () => {
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.sourcecado-activity > button\s*\{[^}]*min-height:\s*44px;[^}]*min-width:\s*44px;/,
    );
  });

  it("sizes the approval-card review-details disclosure", () => {
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.sourcecado-approval-card > button,[\s\S]*?min-height:\s*44px;[^}]*min-width:\s*44px;/,
    );
  });

  it("sizes the audit-receipt disclosure", () => {
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.sourcecado-approval-receipt > button,[\s\S]*?min-height:\s*44px;[^}]*min-width:\s*44px;/,
    );
  });

  it("sizes the copy-response action", () => {
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.sourcecado-copy-action\s*\{[^}]*min-height:\s*44px;[^}]*min-width:\s*44px;/,
    );
  });

  it("sizes the queue's Resume-queue control (not just the 5-slot action grid)", () => {
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.sourcecado-queue > button,[\s\S]*?min-height:\s*44px;[^}]*min-width:\s*44px;/,
    );
  });

  it("sizes the queue's per-row action slots (Up/Down/Edit/Retry/Remove)", () => {
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.sourcecado-queue-actions button,[\s\S]*?min-height:\s*44px;[^}]*min-width:\s*44px;/,
    );
  });

  it("sizes the queue's Save-edit control", () => {
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.sourcecado-queue form button\s*\{[^}]*min-height:\s*44px;[^}]*min-width:\s*44px;/,
    );
  });

  it("sizes the unavailable-thread recovery link", () => {
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.unavailable-thread-page a\s*\{[^}]*min-height:\s*44px;[^}]*min-width:\s*44px;/,
    );
  });

  it("sizes Cmd-K search results", () => {
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.command-results button\s*\{[^}]*min-height:\s*44px;/,
    );
  });

  it("widens the rail's rename/pin thread-action square (min-height alone left width at 28px)", () => {
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.thread-action\s*\{[^}]*width:\s*44px;/,
    );
  });
});
