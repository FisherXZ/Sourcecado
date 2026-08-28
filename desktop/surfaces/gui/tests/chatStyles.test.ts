import { describe, expect, it } from "vitest";

import { styles } from "./cssBundle";

describe("Warm Operator thread styles", () => {
  it("keeps the thread and Markdown content inside the available width", () => {
    expect(styles).toMatch(
      /\.sourcecado-thread\s*\{[^}]*min-width:\s*0;[^}]*overflow:\s*hidden;/,
    );
    expect(styles).toMatch(
      /\.sourcecado-transcript\s*\{[^}]*overflow-x:\s*hidden;[^}]*overflow-y:\s*auto;/,
    );
    expect(styles).toMatch(
      /\.sourcecado-markdown\s*\{[^}]*overflow-wrap:\s*anywhere;/,
    );
  });

  it("uses plain assistant prose and an avocado-tinted user bubble", () => {
    expect(styles).toMatch(
      /\.sourcecado-assistant-message\s*\{[^}]*background:\s*transparent;/,
    );
    expect(styles).toMatch(
      /\.sourcecado-user-message\s*\{[^}]*background:\s*var\(--accent-tint\);/,
    );
  });

  it("bounds composer growth and keeps narrow inputs at 16px", () => {
    expect(styles).toMatch(
      /\.sourcecado-composer textarea\s*\{[^}]*max-height:[^;]+;[^}]*overflow-y:\s*auto;/,
    );
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.sourcecado-composer textarea\s*\{[^}]*font-size:\s*16px;/,
    );
  });

  it("keeps persisted queue rows bounded with touch-sized narrow controls", () => {
    expect(styles).toMatch(
      /\.sourcecado-queue\s*\{[^}]*min-width:\s*0;[^}]*max-width:[^;]+;/,
    );
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.sourcecado-queue-actions button[,\s]*\{?[^}]*min-height:\s*44px;/,
    );
  });

  it("styles grouped activity as a subtle disclosure instead of stacked cards", () => {
    expect(styles).toMatch(
      /\.sourcecado-activity\s*\{[^}]*border-top:\s*1px solid var\(--border\);[^}]*background:\s*transparent;/,
    );
    expect(styles).toMatch(
      /\.sourcecado-activity > button\s*\{[^}]*background:\s*transparent;/,
    );
  });

  it("keeps current-run measurements compact and numerically stable", () => {
    expect(styles).toMatch(
      /\.sourcecado-run-metrics\s*\{[^}]*display:\s*flex;[^}]*flex-wrap:\s*wrap;[^}]*font-variant-numeric:\s*tabular-nums;/,
    );
    expect(styles).toMatch(
      /\.sourcecado-run-metrics > span\s*\{[^}]*border-right:\s*1px solid var\(--border\);/,
    );
  });

  it("keeps the bound person visible as a compact header link", () => {
    expect(styles).toMatch(
      /\.sourcecado-active-person\s*\{[^}]*display:\s*inline-flex;[^}]*color:\s*var\(--accent-deep\);/,
    );
  });

  it("distinguishes pending approval cards from collapsed audit receipts", () => {
    expect(styles).toMatch(
      /\.sourcecado-approval-card\s*\{[^}]*border:\s*1px solid var\(--warn\);/,
    );
    expect(styles).toMatch(
      /\.sourcecado-approval-receipt\s*\{[^}]*background:\s*transparent;/,
    );
  });

  it("keeps failed-step recovery actions inline and raw details bounded", () => {
    expect(styles).toMatch(
      /\.sourcecado-recovery-actions\s*\{[^}]*display:\s*flex;/,
    );
    expect(styles).toMatch(
      /\.sourcecado-failure-detail\s*\{[^}]*overflow-x:\s*auto;/,
    );
  });

  it("uses a 360px desktop inspector, tablet overlay, and narrow full-screen sheet", () => {
    expect(styles).toMatch(
      /\.sourcecado-chat-workspace:has\(\.sourcecado-inspector\)\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\) 360px;/,
    );
    expect(styles).toMatch(
      /@media \(max-width: 1179px\)[\s\S]*?\.sourcecado-inspector\s*\{[^}]*position:\s*fixed;/,
    );
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.sourcecado-inspector\s*\{[^}]*width:\s*100%;/,
    );
  });
});
