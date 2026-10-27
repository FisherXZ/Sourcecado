import { describe, expect, it } from "vitest";

import { styles } from "./cssBundle";

describe("Warm Operator thread styles", () => {
  it("keeps first-run copy readable on the active theme surface", () => {
    const baseWelcome = styles.match(
      /\.route-page\.welcome-page\s*\{[^}]*\}\s*\.welcome-copy\s*\{([^}]*)\}/,
    );

    expect(baseWelcome?.[1]).toMatch(/background:\s*var\(--surface\);/);
    expect(styles).not.toContain("rgb(254 253 251 / 0.92)");
  });

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

  it("scrolls wide tables and long code lines inside their own block", () => {
    expect(styles).toMatch(
      /\.sourcecado-table-scroll\s*\{[^}]*max-width:\s*100%;[^}]*overflow-x:\s*auto;/,
    );
    expect(styles).toMatch(
      /\.sourcecado-table-scroll table\s*\{[^}]*width:\s*max-content;[^}]*min-width:\s*100%;/,
    );
    expect(styles).toMatch(
      /\.sourcecado-code\s*\{[^}]*min-width:\s*0;[^}]*max-width:\s*100%;[^}]*overflow:\s*hidden;/,
    );
    expect(styles).toMatch(
      /\.sourcecado-markdown \.sourcecado-code pre\s*\{[^}]*max-width:\s*100%;[^}]*overflow-x:\s*auto;/,
    );
    expect(styles).toMatch(
      /\.sourcecado-markdown \.sourcecado-code code\s*\{[^}]*white-space:\s*pre;/,
    );
  });

  it("colors syntax with Warm Operator tokens and sets no text under 13px", () => {
    const answer = styles.slice(
      styles.indexOf("Answer prose is one type scale"),
      styles.indexOf(".sourcecado-copy-action {"),
    );

    expect(answer).toContain(".sourcecado-code-head");
    // Every code color is a token. A literal hex value would skip the dark theme.
    expect(answer).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(answer).toMatch(/--code-keyword:\s*var\(--warn\);/);
    expect(answer).toMatch(/--code-string:\s*var\(--accent-deep\);/);
    expect(answer).toMatch(/--code-number:\s*var\(--error\);/);
    expect(answer).toMatch(/--code-comment:\s*var\(--muted\);/);
    // Token spans change color only, so partial code never shifts the layout.
    const tokens = answer.slice(
      answer.indexOf(".sourcecado-code :where(.hljs-comment"),
      answer.indexOf(".sourcecado-code pre:focus-visible"),
    );
    expect(tokens).toContain(".hljs-keyword");
    expect(tokens).not.toMatch(/font-(size|weight|style)|padding|margin|border/);
    const sizes = Array.from(answer.matchAll(/font(?:-size)?:\s*(?:[\d.]+ )?(\d+)px/g)).map(
      (match) => Number(match[1]),
    );
    expect(sizes.length).toBeGreaterThan(3);
    expect(Math.min(...sizes)).toBeGreaterThanOrEqual(13);
  });

  it("sizes headings from the answer body so none falls below it at any width", () => {
    const base = styles.match(
      /\.sourcecado-markdown :where\(h1, h2, h3, h4, h5, h6\)\s*\{([^}]*)\}/,
    );
    const em = (tag: string) =>
      Number(
        styles.match(
          new RegExp(`\\.sourcecado-markdown ${tag}\\s*\\{[^}]*font-size:\\s*([\\d.]+)em;`),
        )?.[1],
      );

    // h4 to h6 inherit the body size. h1 to h3 step up from it.
    expect(base?.[1]).toMatch(/font-size:\s*1em;/);
    expect(em("h3")).toBeGreaterThan(1);
    expect(em("h2")).toBeGreaterThan(em("h3"));
    expect(em("h1")).toBeGreaterThan(em("h2"));
    // The narrow-screen body is 16px, so a px heading would drop below it.
    const headings = styles.match(/\.sourcecado-markdown (?::where\(h1[^{]*\)|h[1-6])\s*\{[^}]*\}/g) ?? [];
    expect(headings).toHaveLength(4);
    for (const rule of headings) expect(rule).not.toMatch(/font-size:\s*[\d.]+px/);
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.sourcecado-assistant-message\s*\{[^}]*font-size:\s*16px;/,
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

  it("keeps one fixed composer action slot when Send becomes Stop", () => {
    expect(styles).toMatch(
      /\.sourcecado-composer-action\s*\{[^}]*width:\s*44px;[^}]*height:\s*44px;[^}]*flex:\s*0 0 44px;/,
    );
    expect(styles).toMatch(
      /\.sourcecado-composer-action-visual\s*\{[^}]*width:\s*38px;[^}]*height:\s*38px;/,
    );
  });

  it("keeps the keyboard hints, chips and menus at 13px or larger", () => {
    for (const selector of [
      ".sourcecado-composer-hints",
      ".sourcecado-context-chip",
      ".sourcecado-menu-detail",
      ".sourcecado-menu-status",
      ".sourcecado-queue-title",
      ".sourcecado-queue li > strong",
    ]) {
      const rule = styles.match(
        new RegExp(`${selector.replace(/[.>]/g, "\\$&")}\\s*\\{([^}]*)\\}`),
      );
      expect(rule, selector).not.toBeNull();
      const size = rule?.[1].match(/font-size:\s*(\d+)px/);
      expect(size, `${selector} font-size`).not.toBeNull();
      expect(Number(size?.[1]), selector).toBeGreaterThanOrEqual(13);
    }
    expect(styles).not.toContain("sourcecado-composer-running");
  });

  it("keeps the empty-thread suggestion understated above the composer", () => {
    expect(styles).toMatch(
      /\.sourcecado-composer-zone\s*\{[^}]*width:\s*min\(720px, calc\(100% - 40px\)\);/,
    );
    expect(styles).toMatch(
      /\.sourcecado-suggestion\s*\{[^}]*border:\s*0;[^}]*background:\s*transparent;/,
    );
    expect(styles).toMatch(
      /\.sourcecado-empty-thread h2\s*\{[^}]*font-size:\s*28px;[^}]*font-weight:\s*500;/,
    );
  });

  it("keeps the new composer actions usable on narrow and reduced-motion layouts", () => {
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.sourcecado-composer-zone\s*\{[^}]*width:\s*calc\(100% - 32px\);/,
    );
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.sourcecado-suggestion\s*\{[^}]*min-height:\s*44px;/,
    );
    expect(styles).toMatch(
      /@media \(prefers-reduced-motion: reduce\)[\s\S]*?\.sourcecado-suggestion[\s\S]*?\.sourcecado-composer-action,[\s\S]*?\.sourcecado-composer-action-visual\s*\{[^}]*transition:\s*none;/,
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

  it("styles the process layer as one bordered group with a bounded, internally scrolling body", () => {
    expect(styles).toMatch(
      /\.sourcecado-activity\s*\{[^}]*border:\s*1px solid var\(--border\);[^}]*border-radius:\s*8px;/,
    );
    expect(styles).toMatch(
      /\.sourcecado-activity-body\s*\{[^}]*max-height:\s*min\(320px, 40vh\);[^}]*overflow-y:\s*auto;/,
    );
    expect(styles).toMatch(
      /\.sourcecado-step-detail\s*\{[^}]*max-height:\s*240px;[^}]*overflow-y:\s*auto;/,
    );
  });

  it("keeps process layer text at 13px or larger and out of muted ink", () => {
    const block = styles.slice(
      styles.indexOf("/* Process layer (#226)"),
      styles.indexOf("/* End of the process layer (#226) */"),
    );
    expect(block.length).toBeGreaterThan(0);
    const sizes = [...block.matchAll(/font-size:\s*(\d+)px/g)].map((m) => Number(m[1]));
    expect(sizes.length).toBeGreaterThan(0);
    expect(Math.min(...sizes)).toBeGreaterThanOrEqual(13);
    // Muted ink is allowed only on decorative icons.
    const mutedRules = [...block.matchAll(/([^{}]+)\{[^}]*color:\s*var\(--muted\)[^}]*\}/g)];
    for (const [, selector] of mutedRules) {
      expect(selector).toMatch(/icon/);
    }
  });

  it("stops the running spinner for reduced motion", () => {
    expect(styles).toMatch(
      /@media \(prefers-reduced-motion: reduce\)\s*\{\s*\.sourcecado-status-icon-running\s*\{\s*animation:\s*none;/,
    );
  });

  it("keeps the usage line at 13px in text ink with stable digits and a 44px touch area on narrow screens", () => {
    expect(styles).toMatch(
      /\.sourcecado-usage-line\s*\{[^}]*flex-wrap:\s*wrap;[^}]*color:\s*var\(--text\);[^}]*font-size:\s*13px;[^}]*font-variant-numeric:\s*tabular-nums;/,
    );
    expect(styles).toMatch(
      /@media \(max-width: 767px\)\s*\{\s*\.sourcecado-usage-line\s*\{[^}]*min-height:\s*44px;/,
    );
    expect(styles).not.toMatch(/\.sourcecado-run-metrics/);
  });

  it("shows the bound person as an icon chip in ink, with a 44px touch area on narrow screens", () => {
    expect(styles).toMatch(
      /\.sourcecado-active-person svg\s*\{[^}]*color:\s*var\(--accent-deep\);/,
    );
    expect(styles).toMatch(
      /\.sourcecado-run-status,\s*\.sourcecado-active-person\s*\{[^}]*display:\s*inline-flex;[^}]*border-radius:\s*999px;[^}]*color:\s*var\(--text\);/,
    );
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.sourcecado-active-person::after\s*\{[^}]*inset:\s*-8px -4px;/,
    );
  });

  it("keeps the header on one row on wide screens and cuts the title short first", () => {
    const base = styles.match(/^\.sourcecado-thread-header\s*\{([^}]*)\}/m)?.[1] ?? "";
    expect(base).toMatch(/display:\s*flex;/);
    expect(base).not.toMatch(/flex-wrap/);
    expect(styles).toMatch(
      /\.sourcecado-thread-header h1\s*\{[^}]*flex:\s*0 4 auto;[^}]*overflow:\s*hidden;[^}]*text-overflow:\s*ellipsis;[^}]*white-space:\s*nowrap;/,
    );
    expect(styles).toMatch(
      /\.sourcecado-active-person\s*\{[^}]*flex:\s*0 1 auto;/,
    );
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.sourcecado-thread-header\s*\{[^}]*flex-wrap:\s*wrap;/,
    );
  });

  it("keeps the header text at 13px or larger and out of muted ink", () => {
    const rules = [...styles.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
      .map((match) => ({ selector: match[1]!.trim(), body: match[2]! }))
      .filter((rule) =>
        /sourcecado-(thread-header|run-status|active-person)/.test(rule.selector),
      );
    expect(rules.length).toBeGreaterThan(0);
    for (const rule of rules) {
      expect(rule.body, rule.selector).not.toMatch(/var\(--muted\)/);
      for (const [, size] of rule.body.matchAll(/font-size:\s*(\d+(?:\.\d+)?)px/g)) {
        expect(Number(size), `${rule.selector} font-size`).toBeGreaterThanOrEqual(13);
      }
    }
    expect(styles).toMatch(/\.sourcecado-run-status,[^{]*\{[^}]*font-size:\s*13px;/);
    expect(styles).not.toContain("sourcecado-persona-badge");
  });

  it("distinguishes pending approval cards from collapsed audit receipts", () => {
    expect(styles).toMatch(
      /\.sourcecado-approval-card\s*\{[^}]*border:\s*1px solid var\(--warn\);/,
    );
    expect(styles).toMatch(
      /\.sourcecado-approval-receipt\s*\{[^}]*background:\s*transparent;/,
    );
  });

  it("lays the approval card out as a bordered field table with an icon-and-text footer", () => {
    expect(styles).toMatch(
      /\.sourcecado-approval-table\s*\{[^}]*width:\s*100%;[^}]*border-collapse:\s*collapse;/,
    );
    expect(styles).toMatch(
      /\.sourcecado-approval-table :where\(th, td\)\s*\{[^}]*border-bottom:\s*1px solid var\(--border\);/,
    );
    expect(styles).toMatch(
      /\.sourcecado-approval-foot\s*\{[^}]*border-top:\s*1px solid var\(--border\);/,
    );
    expect(styles).toMatch(
      /\.sourcecado-approval-foot button\s*\{[^}]*display:\s*inline-flex;/,
    );
  });

  it("bounds the approval field table so Allow and Deny stay reachable", () => {
    expect(styles).toMatch(
      /\.sourcecado-approval-table-scroll\s*\{[^}]*max-height:\s*min\(50vh, 420px\);[^}]*overflow-y:\s*auto;/,
    );
    expect(styles).toMatch(
      /\.sourcecado-approval-details pre\s*\{[^}]*max-height:\s*240px;[^}]*overflow:\s*auto;/,
    );
  });

  it("keeps approval text at 13px or larger and the decision readable", () => {
    for (const selector of [
      "\\.sourcecado-approval-card",
      "\\.sourcecado-approval-table",
      "\\.sourcecado-approval-receipt",
      "\\.sourcecado-approval-actions kbd",
    ]) {
      expect(styles).toMatch(
        new RegExp(`${selector}\\s*\\{[^}]*font-size:\\s*13px;`),
      );
    }
    expect(styles).toMatch(
      /\.sourcecado-approval-receipt\s*\{[^}]*color:\s*var\(--text\);/,
    );
    expect(styles).not.toMatch(
      /\.sourcecado-approval-(card|receipt|table|reason|details)[^{]*\{[^}]*font-size:\s*(1[0-2]|[0-9])px;/,
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
