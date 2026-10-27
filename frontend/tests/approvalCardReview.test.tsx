import { fireEvent, render, screen, within } from "@testing-library/react";
import type { ToolCallMessagePartProps } from "@assistant-ui/react";
import { describe, expect, it, vi } from "vitest";

import { ApprovalCard } from "../src/chat/ApprovalCard";
import { formatDecisionTime } from "../src/chat/approvalModel";

/**
 * Rendered cover for the approval card as the operator reads it.
 *
 * Gmail and Apollo approvals cannot be produced live without credentials, so
 * the fixtures below copy the real backend payload shapes:
 * - gmail_draft:          backend/coworker/turn.py approval_resource (chat gmail_send)
 * - gmail_send_authority: backend/coworker/gmail.py SendAuthority.as_resource
 * - apollo_enrichment:    backend/coworker/apollo.py enrichment_resource
 * Chat apollo_enrich_contact has no resource; only its arguments cross.
 */
const DIGEST = "3f7a9c1e5b2d4860a1c3e5f70912b4d6e8f0a2c4b6d8e0f1a3c5e7092b4d6f80";

const CHAT_SEND_RESOURCE = {
  kind: "gmail_draft",
  draft_id: "r-123",
  to: "maya.chen@acme.com",
  subject: "Intro: Codeology and Acme",
  account: "fisher@codeology.org",
};

const AUTHORITY_RESOURCE = {
  kind: "gmail_send_authority",
  person_id: "person_8f2a",
  draft_id: "r-123",
  account: "fisher@codeology.org",
  to: "maya.chen@acme.com",
  subject: "Intro: Codeology and Acme",
  body_digest: DIGEST,
  sent: false,
};

const ENRICH_REASON =
  "Spends 1 Apollo credit to look up Maya Chen by email and write the result to this person file only.";

const ENRICH_RESOURCE = {
  kind: "apollo_enrichment",
  person_id: "person_8f2a",
  person: "Maya Chen",
  title: "Founder",
  company: "Acme",
  matched_on: "email",
  credits: 1,
  reason: ENRICH_REASON,
};

function part(
  overrides: {
    toolName: string;
    args: Record<string, unknown>;
    resource?: unknown;
    reason?: string;
    metadata?: Record<string, unknown>;
  },
): ToolCallMessagePartProps {
  return {
    type: "tool-call",
    toolCallId: "call-1",
    toolName: overrides.toolName,
    args: overrides.args,
    argsText: "{}",
    approval: {
      id: "approval-1",
      reason: overrides.reason ?? "consequential: waits for Allow or Deny",
    },
    providerMetadata: {
      sourcecado: {
        approvalState: "pending",
        actor: null,
        requestedAt: "2026-10-26T12:00:00Z",
        resolvedAt: null,
        scope: "once",
        executionStatus: "pending",
        executionError: null,
        resource: overrides.resource ?? null,
        ...overrides.metadata,
      },
    },
    addResult: vi.fn(),
    resume: vi.fn(),
    respondToApproval: vi.fn(),
    status: { type: "requires-action", reason: "tool-calls" },
  } as ToolCallMessagePartProps;
}

const sendPart = (resource: unknown = CHAT_SEND_RESOURCE) =>
  part({ toolName: "gmail_send", args: { draft_id: "r-123" }, resource });

const enrichPart = (resource?: unknown, reason?: string) =>
  part({
    toolName: "apollo_enrich_contact",
    args: { firstName: "Maya", lastName: "Chen", organizationName: "Acme" },
    resource,
    ...(reason ? { reason } : {}),
  });

function cell(label: string): HTMLElement {
  const header = screen.getByRole("rowheader", { name: label });
  const row = header.closest("tr") as HTMLElement;
  return within(row).getByRole("cell");
}

describe("approval card: send", () => {
  it("names the account, recipient, subject, reviewed message and body version", () => {
    render(<ApprovalCard part={sendPart(AUTHORITY_RESOURCE)} onDecision={vi.fn()} />);

    expect(
      screen.getByRole("heading", { level: 2, name: "Send email to maya.chen@acme.com" }),
    ).toBeInTheDocument();
    expect(cell("Account")).toHaveTextContent("fisher@codeology.org");
    expect(cell("To")).toHaveTextContent("maya.chen@acme.com");
    expect(cell("Subject")).toHaveTextContent("Intro: Codeology and Acme");
    expect(cell("Message")).toHaveTextContent("Gmail draft r-123");
    expect(cell("Body version")).toHaveTextContent(DIGEST.slice(0, 12));
    expect(cell("Effect")).toHaveTextContent("Sends now. Cannot be undone.");
  });

  it("says the Gmail conversation and body version are unavailable instead of guessing", () => {
    // The chat send path binds a draft id only. No payload carries a thread.
    render(<ApprovalCard part={sendPart()} onDecision={vi.fn()} />);

    expect(cell("Message")).toHaveTextContent("Gmail draft r-123");
    expect(cell("Conversation")).toHaveTextContent("Unavailable");
    expect(cell("Body version")).toHaveTextContent("Unavailable");
  });

  it("never shows the message body, even if a payload carried one", () => {
    const { container } = render(
      <ApprovalCard
        part={sendPart({ ...AUTHORITY_RESOURCE, body: "PRIVATE_BODY_LEAK" })}
        onDecision={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Details" }));

    expect(container).not.toHaveTextContent("PRIVATE_BODY_LEAK");
  });
});

describe("approval card: enrichment", () => {
  it("names one person, the match, the fields sought and the estimated credits", () => {
    const { container } = render(
      <ApprovalCard
        part={enrichPart(ENRICH_RESOURCE, ENRICH_REASON)}
        onDecision={vi.fn()}
      />,
    );

    expect(
      screen.getByRole("heading", { level: 2, name: "Enrich Maya Chen with Apollo" }),
    ).toBeInTheDocument();
    expect(cell("Person")).toHaveTextContent("Maya Chen");
    expect(cell("Title")).toHaveTextContent("Founder");
    expect(cell("Company")).toHaveTextContent("Acme");
    expect(cell("Matched on")).toHaveTextContent("Email");
    expect(cell("Fields sought")).toHaveTextContent("Email, phone, title, company, LinkedIn");
    expect(cell("Estimated cost")).toHaveTextContent("1 Apollo credit");
    // Field names only. Nothing the provider has not returned yet.
    expect(container.querySelector("table")).not.toHaveTextContent("@");
    expect(screen.getByText(ENRICH_REASON)).toBeInTheDocument();
  });

  it("shows an unknown cost and no budget check when the payload carries neither", () => {
    const { container } = render(<ApprovalCard part={enrichPart()} onDecision={vi.fn()} />);

    expect(cell("Estimated cost")).toHaveTextContent("Unknown");
    expect(cell("Credit budget")).toHaveTextContent("Not reported");
    expect(container).not.toHaveTextContent(/\d+ Apollo credits?/);
    expect(
      screen.getByText(
        "Spends Apollo credits. No credit is spent until you choose Allow once.",
      ),
    ).toBeInTheDocument();
  });
});

describe("approval card: no standing permission", () => {
  const cases: [string, ToolCallMessagePartProps][] = [
    ["chat send", sendPart()],
    ["person-bound send", sendPart(AUTHORITY_RESOURCE)],
    ["chat enrichment", enrichPart()],
    ["person-bound enrichment", enrichPart(ENRICH_RESOURCE, ENRICH_REASON)],
    [
      // A payload that claims shell-grant eligibility must not unlock Always
      // for an email send.
      "send with a forged shell resource",
      sendPart({
        kind: "shell_command",
        execution_target: "host",
        command_summary: "x",
        command_display: "x",
        environment_keys: [],
        cwd: "/tmp",
        fingerprint: "abcdef1234567890",
        unsandboxed: true,
        permanent_eligible: true,
      }),
    ],
  ];

  it.each(cases)("%s offers Deny and Allow once only", (_name, subject) => {
    const onDecision = vi.fn();
    const { container } = render(<ApprovalCard part={subject} onDecision={onDecision} />);

    const names = screen
      .getAllByRole("button")
      .map((button) => button.getAttribute("aria-label") ?? button.textContent);
    expect(names).toEqual(["Details", "Deny", "Allow once"]);
    expect(container).not.toHaveTextContent(/always/i);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();

    // Every keyboard path to a decision is allow-once.
    const card = container.querySelector("section") as HTMLElement;
    fireEvent.keyDown(card, { key: "Enter", metaKey: true });
    expect(onDecision).toHaveBeenCalledTimes(1);
    expect(onDecision.mock.calls[0]).toEqual([true]);
  });
});

describe("approval card: keyboard and decisions", () => {
  it("makes no decision on its own", () => {
    vi.useFakeTimers();
    try {
      const onDecision = vi.fn();
      render(<ApprovalCard part={sendPart()} onDecision={onDecision} />);
      vi.advanceTimersByTime(120_000);
      expect(onDecision).not.toHaveBeenCalled();
      expect(screen.getByRole("button", { name: "Allow once" })).toBeEnabled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("allows once on Command-Enter or Control-Enter while focus is in the card", () => {
    const onDecision = vi.fn();
    render(<ApprovalCard part={sendPart()} onDecision={onDecision} />);
    const heading = screen.getByRole("heading", { level: 2 });
    expect(heading).toHaveFocus();

    fireEvent.keyDown(heading, { key: "Enter", ctrlKey: true });
    expect(onDecision).toHaveBeenCalledWith(true);
  });

  it("ignores Enter alone, held keys, Escape and a second press while submitting", () => {
    const onDecision = vi.fn().mockReturnValue(new Promise(() => {}));
    render(<ApprovalCard part={sendPart()} onDecision={onDecision} />);
    const heading = screen.getByRole("heading", { level: 2 });

    fireEvent.keyDown(heading, { key: "Enter" });
    fireEvent.keyDown(heading, { key: "Enter", metaKey: true, repeat: true });
    fireEvent.keyDown(heading, { key: "Escape" });
    fireEvent.keyDown(heading, { key: "Escape", metaKey: true });
    expect(onDecision).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Allow once" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Deny" })).toBeEnabled();

    fireEvent.keyDown(heading, { key: "Enter", metaKey: true });
    fireEvent.keyDown(heading, { key: "Enter", metaKey: true });
    expect(onDecision).toHaveBeenCalledTimes(1);
  });

  it("does not turn Command-Enter on the Deny button into an Allow", () => {
    const onDecision = vi.fn();
    render(<ApprovalCard part={sendPart()} onDecision={onDecision} />);

    fireEvent.keyDown(screen.getByRole("button", { name: "Deny" }), {
      key: "Enter",
      metaKey: true,
    });

    expect(onDecision).not.toHaveBeenCalled();
  });

  it("denies only on the Deny button", () => {
    const onDecision = vi.fn();
    render(<ApprovalCard part={sendPart()} onDecision={onDecision} />);

    fireEvent.click(screen.getByRole("button", { name: "Deny" }));

    expect(onDecision).toHaveBeenCalledWith(false);
  });

  it("gives every button an icon and an accessible name, and advertises Command-Enter", () => {
    render(<ApprovalCard part={sendPart()} onDecision={vi.fn()} />);

    for (const button of screen.getAllByRole("button")) {
      expect(button.querySelector("svg")).not.toBeNull();
      expect(button).toHaveAccessibleName();
    }
    const allow = screen.getByRole("button", { name: "Allow once" });
    expect(allow).toHaveAttribute("aria-keyshortcuts", "Meta+Enter Control+Enter");
    expect(allow).toHaveTextContent("⌘⏎");
  });
});

describe("approval card: what the operator reads", () => {
  it("does not show backend policy wording", () => {
    const { container } = render(<ApprovalCard part={sendPart()} onDecision={vi.fn()} />);

    expect(container).not.toHaveTextContent(/consequential/i);
    expect(container).not.toHaveTextContent(/waits for Allow or Deny/i);
  });

  it("renders arguments as rows and keeps the raw request behind Details", () => {
    const { container } = render(
      <ApprovalCard
        part={part({
          toolName: "board_delete",
          args: {
            person_id: "person_qa_does_not_exist",
            expected_version: 1,
            rationale_summary: "QA check of the approval card.",
          },
        })}
        onDecision={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "Delete person file" })).toBeInTheDocument();
    expect(cell("Person file")).toHaveTextContent("person_qa_does_not_exist");
    expect(cell("Expected version")).toHaveTextContent("1");
    expect(cell("Reason")).toHaveTextContent("QA check of the approval card.");
    expect(container.querySelector("pre")).toBeNull();
    expect(container).not.toHaveTextContent('"person_id"');

    const toggle = screen.getByRole("button", { name: "Details" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(container.querySelector("pre")).toHaveTextContent('"tool": "board_delete"');
    expect(container.querySelector("pre")).toHaveTextContent('"person_id": "person_qa_does_not_exist"');
  });

  it("keeps sensitive-looking argument names out of the rows", () => {
    const { container } = render(
      <ApprovalCard
        part={part({
          toolName: "fs_move",
          args: { from: "a.txt", to: "b.txt", api_key: "SECRET_VALUE" },
        })}
        onDecision={vi.fn()}
      />,
    );

    expect(cell("From")).toHaveTextContent("a.txt");
    expect(container.querySelector("table")).not.toHaveTextContent("SECRET_VALUE");
  });
});

describe("approval card: receipt", () => {
  function resolved(
    state: string,
    executionStatus: string,
    resource: unknown = AUTHORITY_RESOURCE,
  ) {
    return part({
      toolName: "gmail_send",
      args: { draft_id: "r-123" },
      resource,
      metadata: {
        approvalState: state,
        actor: "Fisher",
        resolvedAt: "2026-10-26T14:14:00Z",
        executionStatus,
      },
    });
  }

  it("collapses a denial to one receipt line with the decision and the time, and no failure", () => {
    const { container } = render(
      <ApprovalCard part={resolved("denied", "not_run")} onDecision={vi.fn()} />,
    );

    const time = formatDecisionTime("2026-10-26T14:14:00Z");
    expect(time).not.toBeNull();
    expect(screen.getAllByRole("button")).toHaveLength(1);
    expect(
      screen.getByRole("button", {
        name: `Denied · Send email to maya.chen@acme.com · ${time}`,
      }),
    ).toBeInTheDocument();
    expect(container.querySelector("table")).toBeNull();
    expect(container).not.toHaveTextContent(/failed/i);

    fireEvent.click(screen.getByRole("button", { name: /^Denied/ }));
    expect(screen.getByText("Execution not run")).toBeInTheDocument();
    expect(container).not.toHaveTextContent(/failed/i);
  });

  it("does not dress an allowed send that failed as a success", () => {
    const { container } = render(
      <ApprovalCard part={resolved("allowed", "failed")} onDecision={vi.fn()} />,
    );

    expect(container.querySelector("section")).toHaveAttribute("data-tone", "warn");
    expect(
      screen.getByRole("button", { name: /^Allowed · Send email to maya.chen@acme.com · Action failed/ }),
    ).toBeInTheDocument();
  });

  it("marks a successful allow as ok and an unknown outcome as a warning", () => {
    const { container, rerender } = render(
      <ApprovalCard part={resolved("allowed", "succeeded")} onDecision={vi.fn()} />,
    );
    expect(container.querySelector("section")).toHaveAttribute("data-tone", "ok");

    rerender(
      <ApprovalCard part={resolved("allowed", "interrupted")} onDecision={vi.fn()} />,
    );
    expect(container.querySelector("section")).toHaveAttribute("data-tone", "warn");
    expect(screen.getByRole("button", { name: /^Outcome unknown/ })).toBeInTheDocument();
  });

  it("omits the time instead of inventing one when none was recorded", () => {
    const subject = resolved("denied", "not_run");
    (subject.providerMetadata!.sourcecado as Record<string, unknown>).resolvedAt = null;
    render(<ApprovalCard part={subject} onDecision={vi.fn()} />);

    expect(
      screen.getByRole("button", { name: "Denied · Send email to maya.chen@acme.com" }),
    ).toBeInTheDocument();
  });
});
