import { describe, expect, it } from "vitest";

import {
  canOfferAlways,
  executionText,
  receiptOf,
  visibleReason,
  type ApprovalAudit,
  type ShellCommandResource,
} from "../src/chat/approvalModel";

const ELIGIBLE_SHELL: ShellCommandResource = {
  executionTarget: "host",
  commandSummary: "bash · 1 argument",
  commandDisplay: "bash scripts/inspect.sh",
  environmentKeys: [],
  cwd: "/Users/operator/Workspace",
  fingerprint: "abcdef1234567890",
  unsandboxed: true,
  permanentEligible: true,
};

function audit(overrides: Partial<ApprovalAudit>): ApprovalAudit {
  return {
    approvalState: "allowed",
    actor: null,
    requestedAt: null,
    resolvedAt: null,
    scope: "once",
    executionStatus: "succeeded",
    executionError: null,
    ...overrides,
  };
}

describe("standing permission", () => {
  it("is offered only for one eligible, fingerprinted, unsandboxed shell command", () => {
    expect(canOfferAlways("shell_exec", ELIGIBLE_SHELL)).toBe(true);
    expect(canOfferAlways("shell_exec", { ...ELIGIBLE_SHELL, permanentEligible: false })).toBe(false);
    expect(canOfferAlways("shell_exec", { ...ELIGIBLE_SHELL, fingerprint: null })).toBe(false);
    expect(canOfferAlways("shell_exec", { ...ELIGIBLE_SHELL, unsandboxed: false })).toBe(false);
    expect(canOfferAlways("shell_exec", null)).toBe(false);
  });

  it.each([
    "gmail_send",
    "apollo_enrich_contact",
    "gmail_draft",
    "calendar_create",
    "board_delete",
    "fs_trash",
    "some_new_tool",
  ])("is never offered for %s, even with an eligible shell resource", (toolName) => {
    expect(canOfferAlways(toolName, ELIGIBLE_SHELL)).toBe(false);
  });
});

describe("visible reason", () => {
  it("hides the backend policy sentence", () => {
    expect(visibleReason("consequential: waits for Allow or Deny", "gmail_send")).toBeNull();
    expect(visibleReason("  Consequential: anything  ", "board_delete")).toBeNull();
    expect(visibleReason(undefined, "gmail_send")).toBeNull();
    expect(visibleReason("", "gmail_send")).toBeNull();
  });

  it("keeps a reason that tells the operator something", () => {
    expect(visibleReason("Creating a draft changes Gmail.", "gmail_draft")).toBe(
      "Creating a draft changes Gmail.",
    );
  });

  it("never shows a masked Apollo surname", () => {
    expect(
      visibleReason("Spends 1 Apollo credit to look up Fisher Zh***g.", "apollo_enrich_contact"),
    ).toBe("Spends 1 Apollo credit to look up Fisher (surname hidden by Apollo)");
  });
});

describe("receipt", () => {
  it("is one decision word per state", () => {
    expect(receiptOf(audit({ approvalState: "allowed" })).label).toBe("Allowed");
    expect(receiptOf(audit({ approvalState: "denied", executionStatus: "not_run" })).label).toBe("Denied");
    expect(receiptOf(audit({ approvalState: "expired" })).label).toBe("Expired");
    expect(receiptOf(audit({ approvalState: "cancelled" })).label).toBe("Cancelled");
    expect(receiptOf(audit({ approvalState: "resolved-elsewhere" })).label).toBe("Resolved elsewhere");
  });

  it("never attaches a failure note to a denial, whatever the execution status says", () => {
    const denied = receiptOf(audit({ approvalState: "denied", executionStatus: "failed" }));
    expect(denied.label).toBe("Denied");
    expect(denied.note).toBeNull();
    expect(denied.tone).toBe("neutral");
  });

  it("reports an interrupted execution as unknown, not as allowed", () => {
    const unknown = receiptOf(audit({ executionStatus: "interrupted" }));
    expect(unknown.label).toBe("Outcome unknown");
    expect(unknown.tone).toBe("warn");
  });

  it("flags an allowed action that failed", () => {
    const failed = receiptOf(audit({ executionStatus: "failed" }));
    expect(failed).toMatchObject({ label: "Allowed", tone: "warn", note: "Action failed" });
  });

  it("writes execution status as words", () => {
    expect(executionText("not_run")).toBe("not run");
    expect(executionText("succeeded")).toBe("succeeded");
    expect(executionText(null)).toBe("not recorded");
  });
});
