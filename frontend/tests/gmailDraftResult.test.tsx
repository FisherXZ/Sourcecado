import { fireEvent, render, screen, within } from "@testing-library/react";
import type { ToolCallMessagePart } from "@assistant-ui/react";
import { describe, expect, it } from "vitest";

import { ActivityGroup } from "../src/chat/ActivityGroup";
import { AnswerResults } from "../src/chat/AnswerResults";
import { Inspector, InspectorProvider } from "../src/chat/Inspector";

function gmailDraft(
  overrides: Partial<ToolCallMessagePart> = {},
): ToolCallMessagePart {
  return {
    type: "tool-call",
    toolCallId: "gmail-draft-call-1",
    toolName: "gmail_draft",
    args: {
      to: "alyssa@example.com",
      subject: "Sourcing update",
      body: "Hi Alyssa,\n\nHere is the sourcing update.",
    },
    argsText: "{}",
    result: {
      id: "draft_1",
      to: "alyssa@example.com",
      subject: "Sourcing update",
      drafted: true,
      sent: false,
      rawMime: "PRIVATE_RAW_MIME",
    },
    ...overrides,
  };
}

function renderDraft(tool = gmailDraft()) {
  return render(
    <InspectorProvider threadId="thread-gmail">
      <ActivityGroup tools={[tool]} messageState="complete" />
      <AnswerResults tools={[tool]} />
      <Inspector />
    </InspectorProvider>,
  );
}

describe("Gmail draft result", () => {
  it.each([
    ["denied", { approved: false }, "Denied"],
    ["cancelled", { resolution: "cancelled" }, "Interrupted"],
    ["expired", { resolution: "expired" }, "Interrupted"],
  ] as const)(
    "never presents a %s approval as a successful Gmail draft",
    (_approvalState, resolution, activityState) => {
      const { container } = renderDraft(
        gmailDraft({
          result: undefined,
          approval: {
            id: `approval-${_approvalState}`,
            reason: "Creating a Gmail draft requires approval.",
            ...resolution,
          },
        }),
      );

      const step = screen.getByRole("button", { name: /^Preparing Gmail draft/ });
      expect(step).toHaveTextContent(activityState);
      expect(step).not.toHaveTextContent("Completed");
      expect(
        screen.queryByRole("region", { name: "Gmail draft" }),
      ).not.toBeInTheDocument();
      expect(container).not.toHaveTextContent("Draft ID");
    },
  );

  it("shows a not-sent draft-shaped state while approved creation is running", () => {
    renderDraft(
      gmailDraft({
        result: undefined,
        approval: {
          id: "approval-gmail-draft",
          approved: true,
          reason: "Creating a Gmail draft changes an external account.",
        },
      }),
    );

    const card = screen.getByRole("region", { name: "Gmail draft" });
    expect(card).toHaveAttribute("data-state", "loading");
    expect(within(card).getByText("Loading")).toBeInTheDocument();
    expect(screen.getByText("Not sent")).toBeInTheDocument();
    expect(
      screen.getByRole("list", { name: "Creating Gmail draft preview" }),
    ).toHaveAttribute("aria-busy", "true");
    expect(screen.queryByRole("button", { name: /send/i })).not.toBeInTheDocument();
  });

  it("keeps a failed Gmail draft explicitly not sent without exposing its payload", () => {
    const { container } = renderDraft(
      gmailDraft({
        isError: true,
        result: { error: "PRIVATE_GMAIL_PROVIDER_PAYLOAD" },
        providerMetadata: {
          sourcecado: {
            failure: {
              summary: "Gmail couldn’t create this draft.",
            },
          },
        },
      }),
    );

    expect(
      screen.getByRole("button", { name: /^Preparing Gmail draft/ }),
    ).toHaveTextContent("Failed");

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Gmail couldn’t create this draft.",
    );
    expect(screen.getByText("Not sent · Gmail draft was not created.")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Gmail draft" })).toHaveAttribute(
      "data-state",
      "failed",
    );
    expect(container).not.toHaveTextContent("PRIVATE_GMAIL_PROVIDER_PAYLOAD");
    expect(screen.queryByRole("button", { name: /send/i })).not.toBeInTheDocument();
  });

  it("falls back safely when a legacy result cannot prove a reviewable draft", () => {
    const { container } = renderDraft(
      gmailDraft({
        result: {
          sent: true,
          rawMime: "PRIVATE_LEGACY_GMAIL_MIME",
        },
      }),
    );

    fireEvent.click(
      screen.getByRole("button", {
        name: /^Prepared Gmail draft/,
      }),
    );
    expect(screen.getByRole("region", { name: "Gmail draft" })).toHaveAttribute(
      "data-state",
      "unsupported",
    );
    // The record says it was sent. The card must not contradict it with "Not sent".
    expect(screen.queryByText("Not sent")).not.toBeInTheDocument();
    expect(container).not.toHaveTextContent("PRIVATE_LEGACY_GMAIL_MIME");

    fireEvent.click(
      screen.getByRole("button", { name: "Inspect Prepared Gmail draft" }),
    );
    expect(
      screen.getByRole("complementary", { name: "Inspector" }),
    ).toHaveTextContent("PRIVATE_LEGACY_GMAIL_MIME");
  });

  it("still says Not sent on an unreadable record that does not claim a send", () => {
    renderDraft(gmailDraft({ result: { drafted: false } }));
    expect(screen.getByRole("region", { name: "Gmail draft" })).toHaveAttribute(
      "data-state",
      "unsupported",
    );
    expect(screen.getByText("Not sent")).toBeInTheDocument();
  });

  it("opens stable draft and source targets while keeping unsafe URLs non-clickable", () => {
    const { container } = renderDraft(
      gmailDraft({
        result: {
          id: "draft_unsafe",
          drafted: true,
          sent: false,
          external_url: "javascript:alert('unsafe')",
          account_email: "fisher@example.com",
        },
      }),
    );
    fireEvent.click(
      screen.getByRole("button", {
        name: /^Prepared Gmail draft/,
      }),
    );

    expect(container).not.toHaveTextContent("javascript:alert");
    fireEvent.click(
      screen.getByRole("button", {
        name: "Open in inspector: Gmail draft: Sourcing update",
      }),
    );
    const inspector = screen.getByRole("complementary", { name: "Inspector" });
    expect(within(inspector).getByRole("heading", { level: 2 })).toHaveTextContent(
      "Gmail draft: Sourcing update",
    );
    expect(within(inspector).getByText("External URL unavailable")).toBeInTheDocument();
    expect(within(inspector).queryByRole("link")).not.toBeInTheDocument();
    expect(inspector).toHaveTextContent("fisher@example.com");
  });

  it("renders a created draft as a review-ready not-sent artifact", () => {
    const { container } = renderDraft();

    fireEvent.click(
      screen.getByRole("button", {
        name: /^Prepared Gmail draft/,
      }),
    );

    const card = screen.getByRole("region", { name: "Gmail draft" });
    expect(card).toHaveAttribute("data-state", "ready");
    expect(screen.getByText("Not sent")).toBeInTheDocument();
    expect(within(card).getByText("draft_1")).toBeInTheDocument();
    expect(screen.getByText("alyssa@example.com")).toBeInTheDocument();
    expect(screen.getByText("Sourcing update")).toBeInTheDocument();
    expect(screen.getByText(/Here is the sourcing update/)).toBeInTheDocument();
    // No account address came back, so the card shows a gap and does not guess one.
    expect(screen.getByText("Account address unavailable")).toBeInTheDocument();
    expect(container).not.toHaveTextContent("PRIVATE_RAW_MIME");
    expect(screen.queryByRole("button", { name: /send/i })).not.toBeInTheDocument();
  });
});
