import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ToolCallMessagePartProps } from "@assistant-ui/react";
import { describe, expect, it, vi } from "vitest";

import { ApprovalCard } from "../src/chat/ApprovalCard";

function approvalPart(
  approvalState: string = "pending",
): ToolCallMessagePartProps {
  return {
    type: "tool-call",
    toolCallId: "call-approval",
    toolName: "gmail_draft",
    args: {
      to: "alyssa@example.com",
      subject: "Hello",
      body: "Full body",
    },
    argsText: "{}",
    approval: {
      id: "approval-1",
      reason: "Draft creation needs approval.",
    },
    providerMetadata: {
      sourcecado: {
        approvalState,
        actor: approvalState === "pending" ? null : "Another operator",
        requestedAt: "2026-08-25T12:00:00Z",
        resolvedAt: approvalState === "pending" ? null : "2026-08-25T12:01:00Z",
        scope: "once",
        executionStatus: "not_run",
        executionError: null,
      },
    },
    addResult: vi.fn(),
    resume: vi.fn(),
    respondToApproval: vi.fn(),
    status: { type: "requires-action", reason: "tool-calls" },
  } as ToolCallMessagePartProps;
}

describe("ApprovalCard", () => {
  it("shows Calendar create fields, timezone, and account before approval", () => {
    const part = {
      ...approvalPart(),
      toolName: "calendar_create",
      args: {
        summary: "Candidate interview",
        start: "2026-08-25T10:00:00",
        end: "2026-08-25T10:30:00",
        timezone: "America/Los_Angeles",
        description: "Discuss the sourcing role.",
      },
      approval: {
        id: "approval-calendar-create",
        reason: "Creating an event changes Google Calendar.",
      },
    } as ToolCallMessagePartProps;

    render(<ApprovalCard part={part} onDecision={vi.fn()} />);

    expect(
      screen.getByRole("heading", { name: "Prepared calendar event" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Google Calendar · Connected Google account")).toBeInTheDocument();
    expect(screen.getByText("Candidate interview")).toBeInTheDocument();
    expect(screen.getByText("Aug 25, 2026 · 10:00 AM")).toBeInTheDocument();
    expect(screen.getByText("Aug 25, 2026 · 10:30 AM")).toBeInTheDocument();
    expect(screen.getByText("America/Los_Angeles")).toBeInTheDocument();
    expect(
      screen.getByText("Changed fields: title, start, end, timezone, description"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Creating an event changes Google Calendar."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Allow once" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Deny" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /delete/i })).not.toBeInTheDocument();
  });

  it.each([
    ["allowed", "Allowed"],
    ["denied", "Denied"],
    ["cancelled", "Cancelled"],
    ["expired", "Expired"],
  ])("renders a truthful Calendar %s approval receipt", (state, label) => {
    const part = {
      ...approvalPart(state),
      toolName: "calendar_update",
      args: { event_id: "event-existing", summary: "Updated title" },
    } as ToolCallMessagePartProps;

    render(<ApprovalCard part={part} onDecision={vi.fn()} />);

    expect(
      screen.getByRole("button", {
        name: `Updated calendar event · ${label}`,
      }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /delete/i })).not.toBeInTheDocument();
  });

  it("shows a review-ready Gmail draft with an explicit not-sent invariant before approval", () => {
    render(<ApprovalCard part={approvalPart()} onDecision={vi.fn()} />);

    expect(
      screen.getByRole("heading", { name: "Prepared Gmail draft" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Gmail draft · Connected Google account")).toBeInTheDocument();
    expect(screen.getByText("Not sent")).toBeInTheDocument();
    expect(screen.getByText("alyssa@example.com")).toBeInTheDocument();
    expect(screen.getByText("Hello")).toBeInTheDocument();
    expect(screen.getByText("Full body")).toBeInTheDocument();
    expect(screen.getByText("Draft creation needs approval.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /send/i })).not.toBeInTheDocument();
  });

  it("clamps a long Gmail body behind a focused accessible disclosure", () => {
    const longBody = [
      "Hi Alyssa,",
      "",
      "I wanted to share a sourcing update. ".repeat(12),
      "PRIVATE_BODY_TAIL",
    ].join("\n");
    const part = {
      ...approvalPart(),
      args: {
        ...approvalPart().args,
        body: longBody,
      },
    } as ToolCallMessagePartProps;

    const { container } = render(
      <ApprovalCard part={part} onDecision={vi.fn()} />,
    );

    expect(container).not.toHaveTextContent("PRIVATE_BODY_TAIL");
    const expand = screen.getByRole("button", { name: "Expand draft body" });
    expect(expand).toHaveAttribute("aria-expanded", "false");
    expand.focus();
    fireEvent.click(expand, { detail: 0 });
    expect(container).toHaveTextContent("PRIVATE_BODY_TAIL");
    const collapse = screen.getByRole("button", { name: "Collapse draft body" });
    expect(collapse).toHaveAttribute("aria-expanded", "true");
    expect(collapse).toHaveFocus();
  });

  it("discloses Apollo credit use before the existing approval action", () => {
    const onDecision = vi.fn();
    const part = {
      ...approvalPart(),
      toolName: "apollo_enrich_contact",
      args: {
        firstName: "Tim",
        lastName: "Zheng",
        organizationName: "Apollo",
      },
      approval: {
        id: "approval-apollo",
        reason: "This enrichment needs approval.",
      },
    } as ToolCallMessagePartProps;

    render(<ApprovalCard part={part} onDecision={onDecision} />);

    expect(
      screen.getByText(
        "Apollo enrichment uses credits. No credit is spent until you choose Allow once.",
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Allow once" }));
    expect(onDecision).toHaveBeenCalledWith(true);
  });

  it("does not re-offer a queued decision and explains it is waiting for the connection", async () => {
    const onDecision = vi.fn().mockResolvedValue("queued");
    render(<ApprovalCard part={approvalPart()} onDecision={onDecision} />);

    fireEvent.click(screen.getByRole("button", { name: "Allow once" }));

    expect(
      await screen.findByText(/waiting for the connection/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Allow once" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Deny" })).toBeDisabled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText("Submitting decision…")).not.toBeInTheDocument();
  });

  it("retains enabled recovery after a failed submission", async () => {
    const onDecision = vi.fn().mockRejectedValue(new Error("offline"));
    render(<ApprovalCard part={approvalPart()} onDecision={onDecision} />);

    fireEvent.click(screen.getByRole("button", { name: "Allow once" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "decision couldn’t be saved",
    );
    expect(screen.getByRole("button", { name: "Allow once" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Deny" })).toBeEnabled();
  });

  it.each([
    ["expired", "Expired"],
    ["cancelled", "Cancelled"],
    ["resolved-elsewhere", "Resolved elsewhere"],
  ])("renders %s as a non-denial receipt", async (state, label) => {
    render(<ApprovalCard part={approvalPart(state)} onDecision={vi.fn()} />);

    const receipt = screen.getByRole("button", {
      name: `Prepared Gmail draft · ${label}`,
    });
    expect(receipt).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Deny" })).not.toBeInTheDocument();
    await waitFor(() => expect(receipt).not.toHaveTextContent("Denied"));
  });

  it("shows a truthful sending title and scope for a pending gmail_send approval", () => {
    const part = {
      ...approvalPart(),
      toolName: "gmail_send",
      args: { draft_id: "draft-123" },
      approval: {
        id: "approval-gmail-send",
        reason: "Sending this draft changes Gmail.",
      },
    } as ToolCallMessagePartProps;

    render(<ApprovalCard part={part} onDecision={vi.fn()} />);

    expect(
      screen.getByRole("heading", { name: "Send Gmail draft" }),
    ).toBeInTheDocument();
    expect(screen.getByText("draft-123")).toBeInTheDocument();
    expect(
      screen.getByText("Sending this draft changes Gmail."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Not sent")).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "Review full request and policy" }),
    );
    expect(
      screen.getByText("Scope: allow once. Sourcecado will send this email now."),
    ).toBeInTheDocument();
  });

  it("names the recipient, subject, and account when the sidecar resolved the gmail_send resource", () => {
    const part = {
      ...approvalPart(),
      toolName: "gmail_send",
      args: { draft_id: "draft-123" },
      providerMetadata: {
        sourcecado: {
          resource: {
            kind: "gmail_draft",
            draft_id: "draft-123",
            to: "alyssa@example.com",
            subject: "Sourcing update",
            account: "fisher@example.com",
          },
        },
      },
    } as ToolCallMessagePartProps;

    render(<ApprovalCard part={part} onDecision={vi.fn()} />);

    expect(screen.getByText("Gmail · fisher@example.com")).toBeInTheDocument();
    expect(screen.getByText("alyssa@example.com")).toBeInTheDocument();
    expect(screen.getByText("Sourcing update")).toBeInTheDocument();
    expect(
      screen.queryByText(
        "Sourcecado can’t show the recipient or subject for this approval.",
        { exact: false },
      ),
    ).not.toBeInTheDocument();
  });

  it("says plainly when a resolved field is unknown instead of hiding or blanking it", () => {
    const part = {
      ...approvalPart(),
      toolName: "gmail_send",
      args: { draft_id: "draft-123" },
      providerMetadata: {
        sourcecado: {
          resource: {
            kind: "gmail_draft",
            draft_id: "draft-123",
            to: null,
            subject: "Sourcing update",
            account: null,
          },
        },
      },
    } as ToolCallMessagePartProps;

    render(<ApprovalCard part={part} onDecision={vi.fn()} />);

    expect(
      screen.getByText("Recipient could not be determined"),
    ).toBeInTheDocument();
    expect(screen.getByText("Sourcing update")).toBeInTheDocument();
    expect(
      screen.getByText("Gmail · Account could not be determined"),
    ).toBeInTheDocument();
    expect(screen.queryByText("null", { exact: false })).not.toBeInTheDocument();
  });

  it("never renders body, tokens, or headers even if the resource payload carried them", () => {
    const part = {
      ...approvalPart(),
      toolName: "gmail_send",
      args: { draft_id: "draft-123" },
      providerMetadata: {
        sourcecado: {
          resource: {
            kind: "gmail_draft",
            draft_id: "draft-123",
            to: "alyssa@example.com",
            subject: "Sourcing update",
            account: "fisher@example.com",
            body: "PRIVATE_BODY_LEAK",
            headers: { "X-Auth-Token": "PRIVATE_TOKEN_LEAK" },
          },
        },
      },
    } as ToolCallMessagePartProps;

    const { container } = render(
      <ApprovalCard part={part} onDecision={vi.fn()} />,
    );

    expect(container).not.toHaveTextContent("PRIVATE_BODY_LEAK");
    expect(container).not.toHaveTextContent("PRIVATE_TOKEN_LEAK");
  });

  it("falls back to the honest caveat when no resource was resolved at all", () => {
    const part = {
      ...approvalPart(),
      toolName: "gmail_send",
      args: { draft_id: "draft-123" },
    } as ToolCallMessagePartProps;

    render(<ApprovalCard part={part} onDecision={vi.fn()} />);

    expect(screen.getByText("Gmail · Connected Google account")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Sourcecado can’t show the recipient or subject for this approval. Review the drafted email before choosing Allow once.",
      ),
    ).toBeInTheDocument();
  });

  it("never renders a sent state when a gmail_send approval is denied or cancelled", () => {
    render(<ApprovalCard part={approvalPart("denied")} onDecision={vi.fn()} />);

    const part = {
      ...approvalPart("denied"),
      toolName: "gmail_send",
      args: { draft_id: "draft-denied" },
    } as ToolCallMessagePartProps;

    const { unmount } = render(<ApprovalCard part={part} onDecision={vi.fn()} />);
    const receipt = screen.getByRole("button", {
      name: "Send Gmail draft · Denied",
    });
    expect(receipt).toBeInTheDocument();
    fireEvent.click(receipt);
    expect(screen.queryByText(/sent/i)).not.toBeInTheDocument();
    unmount();
  });

  it("gives calendar approvals an accurate scope statement instead of an email non-sequitur", () => {
    const part = {
      ...approvalPart(),
      toolName: "calendar_create",
      args: {
        summary: "Candidate interview",
        start: "2026-08-25T10:00:00",
        end: "2026-08-25T10:30:00",
      },
    } as ToolCallMessagePartProps;

    render(<ApprovalCard part={part} onDecision={vi.fn()} />);

    fireEvent.click(
      screen.getByRole("button", { name: "Review full request and policy" }),
    );
    expect(
      screen.getByText(
        "Scope: allow once. This changes Google Calendar and will not send email.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Scope: allow once. Sourcecado will not send email."),
    ).not.toBeInTheDocument();
  });

  it("renders a restarted execution as outcome unknown instead of allowed", () => {
    const part = approvalPart("allowed");
    part.providerMetadata = {
      sourcecado: {
        ...(part.providerMetadata?.sourcecado as Record<string, unknown>),
        executionStatus: "interrupted",
        executionError:
          "Outcome is unknown after Sourcecado restarted. Verify the external resource before retrying.",
      },
    };

    render(<ApprovalCard part={part} onDecision={vi.fn()} />);

    const receipt = screen.getByRole("button", {
      name: "Prepared Gmail draft · Outcome unknown",
    });
    expect(receipt).not.toHaveTextContent("Allowed");
    fireEvent.click(receipt);
    expect(
      screen.getByText(/verify the external resource before retrying/i),
    ).toBeInTheDocument();
  });

  it("moves out of Submitting decision… into an explicit unknown-outcome state when no receipt ever arrives", () => {
    vi.useFakeTimers();
    try {
      const onDecision = vi.fn().mockResolvedValue(undefined);
      render(<ApprovalCard part={approvalPart()} onDecision={onDecision} />);

      fireEvent.click(screen.getByRole("button", { name: "Allow once" }));
      expect(screen.getByText("Submitting decision…")).toBeInTheDocument();

      act(() => {
        vi.advanceTimersByTime(60_000);
      });

      expect(
        screen.queryByText("Submitting decision…"),
      ).not.toBeInTheDocument();
      expect(screen.getByText(/Outcome is unknown/i)).toBeInTheDocument();
      // Must not imply denial, failure, or resolution.
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(screen.queryByText(/denied/i)).not.toBeInTheDocument();
      expect(
        screen.queryByRole("heading", { name: /Allowed|Denied/ }),
      ).not.toBeInTheDocument();
      // Already submitted: do not re-offer the decision.
      expect(screen.getByRole("button", { name: "Allow once" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Deny" })).toBeDisabled();
      expect(
        document.querySelector(".sourcecado-approval-card"),
      ).not.toHaveAttribute("aria-busy", "true");
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not flip to unknown-outcome once a real resolution arrives before the deadline", () => {
    vi.useFakeTimers();
    try {
      const onDecision = vi.fn().mockResolvedValue(undefined);
      const { rerender } = render(
        <ApprovalCard part={approvalPart()} onDecision={onDecision} />,
      );
      fireEvent.click(screen.getByRole("button", { name: "Allow once" }));

      act(() => {
        vi.advanceTimersByTime(5_000);
      });
      rerender(
        <ApprovalCard part={approvalPart("allowed")} onDecision={onDecision} />,
      );

      act(() => {
        vi.advanceTimersByTime(60_000);
      });

      expect(screen.queryByText(/Outcome is unknown/i)).not.toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Prepared Gmail draft · Allowed" }),
      ).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});
