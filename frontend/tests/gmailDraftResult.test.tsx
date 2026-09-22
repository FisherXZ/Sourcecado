import { fireEvent, render, screen, within } from "@testing-library/react";
import type { ToolCallMessagePart } from "@assistant-ui/react";
import { describe, expect, it } from "vitest";

import { ActivityGroup } from "../src/chat/ActivityGroup";
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
      <Inspector />
    </InspectorProvider>,
  );
}

describe("Gmail draft result", () => {
  it.each([
    ["denied", { approved: false }, "Denied"],
    ["cancelled", { resolution: "cancelled" }, "Partial"],
    ["expired", { resolution: "expired" }, "Partial"],
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

      expect(
        screen.getByRole("button", {
          name: `Prepared Gmail draft · ${activityState}`,
        }),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("heading", { name: "Gmail draft ready for review" }),
      ).not.toBeInTheDocument();
      expect(container).not.toHaveTextContent("Draft ID:");
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

    expect(
      screen.getByRole("heading", { name: "Creating Gmail draft" }),
    ).toBeInTheDocument();
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

    fireEvent.click(
      screen.getByRole("button", { name: "Prepared Gmail draft · Failed" }),
    );

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Gmail couldn’t create this draft.",
    );
    expect(screen.getByText("Not sent · Gmail draft was not created.")).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Gmail draft ready for review" }),
    ).not.toBeInTheDocument();
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
        name: "Prepared Gmail draft · Completed",
      }),
    );
    expect(
      screen.getByRole("heading", { name: "Gmail draft result needs review" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Not sent")).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Gmail draft ready for review" }),
    ).not.toBeInTheDocument();
    expect(container).not.toHaveTextContent("PRIVATE_LEGACY_GMAIL_MIME");

    fireEvent.click(
      screen.getByRole("button", { name: "Inspect Prepared Gmail draft" }),
    );
    expect(
      screen.getByRole("complementary", { name: "Inspector" }),
    ).toHaveTextContent("PRIVATE_LEGACY_GMAIL_MIME");
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
        name: "Prepared Gmail draft · Completed",
      }),
    );

    expect(container).not.toHaveTextContent("javascript:alert");
    fireEvent.click(
      screen.getByRole("button", {
        name: "Inspect Gmail draft draft_unsafe",
      }),
    );
    const inspector = screen.getByRole("complementary", { name: "Inspector" });
    expect(within(inspector).getByRole("heading", { level: 2 })).toHaveTextContent(
      "Gmail draft: Sourcing update",
    );
    expect(within(inspector).getByText("External URL unavailable")).toBeInTheDocument();
    expect(within(inspector).queryByRole("link")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Inspect Gmail source" }));
    expect(within(inspector).getByRole("heading", { level: 2 })).toHaveTextContent(
      "Gmail source",
    );
    expect(within(inspector).getByText("fisher@example.com")).toBeInTheDocument();
  });

  it("renders a created draft as a review-ready not-sent artifact", () => {
    const { container } = renderDraft();

    fireEvent.click(
      screen.getByRole("button", {
        name: "Prepared Gmail draft · Completed",
      }),
    );

    expect(
      screen.getByRole("heading", { name: "Gmail draft ready for review" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Not sent")).toBeInTheDocument();
    expect(screen.getByText("Draft ID: draft_1")).toBeInTheDocument();
    expect(screen.getByText("alyssa@example.com")).toBeInTheDocument();
    expect(screen.getByText("Sourcing update")).toBeInTheDocument();
    expect(screen.getByText(/Here is the sourcing update/)).toBeInTheDocument();
    expect(
      screen.getByText("Google account address unavailable; draft is still available."),
    ).toBeInTheDocument();
    expect(container).not.toHaveTextContent("PRIVATE_RAW_MIME");
    expect(screen.queryByRole("button", { name: /send/i })).not.toBeInTheDocument();
  });
});
