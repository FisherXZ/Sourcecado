import { describe, expect, it } from "vitest";

import {
  cleanApolloText,
  executionText,
  receiptOf,
  visibleReason,
  type ApprovalAudit,
} from "../src/chat/approvalModel";

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

describe("masked Apollo surname", () => {
  it("replaces the mask with words and does not repeat the phrase the backend adds", () => {
    expect(cleanApolloText("Maya C***n (surname hidden by Apollo)")).toBe(
      "Maya (surname hidden by Apollo)",
    );
  });

  it("leaves a name with no mask alone", () => {
    expect(cleanApolloText("Maya Chen")).toBe("Maya Chen");
    expect(cleanApolloText("Maya (surname hidden by Apollo)")).toBe(
      "Maya (surname hidden by Apollo)",
    );
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
