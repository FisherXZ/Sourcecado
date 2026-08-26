import { fireEvent, render, screen, within } from "@testing-library/react";
import type { ToolCallMessagePart } from "@assistant-ui/react";
import { describe, expect, it } from "vitest";

import { ActivityGroup } from "../src/chat/ActivityGroup";
import { Inspector, InspectorProvider } from "../src/chat/Inspector";

function tool(
  toolCallId: string,
  toolName: string,
  args: Record<string, unknown>,
  result: unknown,
  overrides: Partial<ToolCallMessagePart> = {},
): ToolCallMessagePart {
  return {
    type: "tool-call",
    toolCallId,
    toolName,
    args,
    argsText: "{}",
    result,
    ...overrides,
  };
}

function renderEvidence(tools: readonly ToolCallMessagePart[]) {
  return render(
    <InspectorProvider threadId="thread-evidence">
      <ActivityGroup tools={tools} messageState="complete" />
      <Inspector />
    </InspectorProvider>,
  );
}

describe("Evidence set result", () => {
  it("renders Drive folder children as evidence", () => {
    renderEvidence([
      tool(
        "call-drive-folder",
        "drive_list_folder",
        { folder_id: "fall-folder" },
        {
          folder_id: "fall-folder",
          files: [
            {
              id: "masterdoc",
              name: "Codeology Fall '26 Sourcing Masterdoc",
              mimeType: "application/vnd.google-apps.document",
            },
          ],
        },
      ),
    ]);

    fireEvent.click(
      screen.getByRole("button", { name: "Listed Drive folder · Completed" }),
    );

    expect(
      screen.getByText("Codeology Fall '26 Sourcing Masterdoc"),
    ).toBeInTheDocument();
    expect(screen.getByText(/Folder child/)).toBeInTheDocument();
  });

  it("uses evidence-shaped rows while a source is loading", () => {
    renderEvidence([
      tool("call-drive-loading", "drive_search", { query: "Q3 sourcing" }, undefined),
    ]);

    expect(
      screen.getByRole("heading", { name: "Loading evidence" }),
    ).toBeInTheDocument();
    const skeleton = screen.getByRole("list", { name: "Loading evidence" });
    expect(skeleton).toHaveAttribute("aria-busy", "true");
    expect(skeleton.querySelectorAll("li")).toHaveLength(3);
  });

  it("keeps query and connector context in a helpful empty state", () => {
    renderEvidence([
      tool(
        "call-drive-empty",
        "drive_search",
        { query: "Q3 sourcing" },
        { files: [] },
      ),
      tool(
        "call-granola-empty",
        "mcp__granola__list_meetings",
        { query: "hiring" },
        { meetings: [] },
      ),
    ]);
    fireEvent.click(
      screen.getByRole("button", { name: "Checked 2 sources · Completed" }),
    );

    expect(
      screen.getByRole("heading", { name: "No evidence found" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "No evidence matched Q3 sourcing or hiring across Google Drive and Granola.",
      ),
    ).toBeInTheDocument();
  });

  it("renders a truthful unavailable state when every evidence source fails", () => {
    const failed = (
      toolCallId: string,
      toolName: string,
      source: "Google Drive" | "Granola",
    ) =>
      tool(toolCallId, toolName, {}, { error: `PRIVATE_${source}_ERROR` }, {
        isError: true,
        providerMetadata: {
          sourcecado: {
            failure: {
              source,
              summary: `${source} evidence is unavailable.`,
              retry_safe: true,
              idempotent: true,
            },
          },
        },
      });
    const { container } = renderEvidence([
      failed("call-drive-failed", "drive_search", "Google Drive"),
      failed("call-granola-failed", "mcp__granola__list_meetings", "Granola"),
    ]);
    fireEvent.click(
      screen.getByRole("button", { name: "Checked 2 sources · Failed" }),
    );

    expect(
      screen.getByRole("heading", { name: "Evidence unavailable" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Google Drive and Granola evidence unavailable."),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Retry failed step" })).toHaveLength(2);
    expect(container).not.toHaveTextContent("PRIVATE_Google Drive_ERROR");
    expect(container).not.toHaveTextContent("PRIVATE_Granola_ERROR");
  });

  it("falls back safely for malformed legacy evidence payloads", () => {
    const { container } = renderEvidence([
      tool(
        "call-drive-malformed",
        "drive_search",
        { query: "legacy" },
        { files: "PRIVATE_LEGACY_DRIVE_PAYLOAD" },
      ),
      tool(
        "call-granola-malformed",
        "mcp__granola__list_meetings",
        {},
        { unexpected: "PRIVATE_LEGACY_GRANOLA_PAYLOAD" },
      ),
    ]);
    fireEvent.click(
      screen.getByRole("button", { name: "Checked 2 sources · Completed" }),
    );

    expect(
      screen.getByRole("heading", { name: "Evidence result needs review" }),
    ).toBeInTheDocument();
    expect(container).not.toHaveTextContent("PRIVATE_LEGACY_DRIVE_PAYLOAD");
    expect(container).not.toHaveTextContent("PRIVATE_LEGACY_GRANOLA_PAYLOAD");
    fireEvent.click(
      screen.getByRole("button", { name: "Inspect Searched Drive" }),
    );
    expect(
      screen.getByRole("complementary", { name: "Inspector" }),
    ).toHaveTextContent("PRIVATE_LEGACY_DRIVE_PAYLOAD");
  });

  it("clamps excerpts and opens exact stale truncated source provenance safely", () => {
    const longContent = `Useful first line\n${"Evidence context ".repeat(35)}PRIVATE_EVIDENCE_TAIL`;
    const { container } = renderEvidence([
      tool(
        "call-drive-long",
        "drive_read",
        { file_id: "drive-long" },
        {
          id: "drive-long",
          name: "Long Drive document",
          content: longContent,
          stale: true,
          truncated: true,
          url: "javascript:alert('unsafe')",
        },
      ),
    ]);
    fireEvent.click(
      screen.getByRole("button", { name: "Read Drive evidence · Completed" }),
    );

    expect(screen.getByText("Cached stale")).toBeInTheDocument();
    expect(screen.getByText("Truncated")).toBeInTheDocument();
    expect(container).not.toHaveTextContent("PRIVATE_EVIDENCE_TAIL");
    const expand = screen.getByRole("button", { name: "Expand evidence excerpt" });
    expand.focus();
    fireEvent.click(expand, { detail: 0 });
    expect(container).toHaveTextContent("PRIVATE_EVIDENCE_TAIL");
    expect(
      screen.getByRole("button", { name: "Collapse evidence excerpt" }),
    ).toHaveFocus();

    fireEvent.click(
      screen.getByRole("button", { name: "Inspect evidence Long Drive document" }),
    );
    const inspector = screen.getByRole("complementary", { name: "Inspector" });
    expect(within(inspector).getByRole("heading", { level: 2 })).toHaveTextContent(
      "Long Drive document",
    );
    expect(within(inspector).getByText("Cached stale")).toBeInTheDocument();
    expect(within(inspector).getByText("Truncated")).toBeInTheDocument();
    expect(within(inspector).getByText("External URL unavailable")).toBeInTheDocument();
    expect(within(inspector).queryByRole("link")).not.toBeInTheDocument();
  });

  it("bounds large evidence sets behind a keyboard-operable show-more control", () => {
    renderEvidence([
      tool(
        "call-drive-many",
        "drive_search",
        { query: "sourcing" },
        {
          files: Array.from({ length: 12 }, (_, index) => ({
            id: `drive-many-${index + 1}`,
            name: `Evidence file ${String(index + 1).padStart(2, "0")}`,
            mimeType: "text/plain",
          })),
        },
      ),
    ]);
    fireEvent.click(
      screen.getByRole("button", { name: "Searched Drive · Completed" }),
    );
    const evidence = screen.getByRole("list", { name: "Evidence items" });
    expect(evidence.querySelectorAll(":scope > li")).toHaveLength(5);
    expect(screen.queryByText("Evidence file 06")).not.toBeInTheDocument();

    const firstPage = screen.getByRole("button", { name: "Show 5 more evidence items" });
    firstPage.focus();
    fireEvent.click(firstPage, { detail: 0 });
    expect(evidence.querySelectorAll(":scope > li")).toHaveLength(10);
    expect(
      screen.getByRole("button", { name: "Show 2 more evidence items" }),
    ).toHaveFocus();
    fireEvent.click(
      screen.getByRole("button", { name: "Show 2 more evidence items" }),
    );
    expect(evidence.querySelectorAll(":scope > li")).toHaveLength(12);
    expect(
      screen.queryByRole("button", { name: /more evidence items/ }),
    ).not.toBeInTheDocument();
  });

  it.each([
    ["Granola", "Google Drive", "drive_read", "mcp__granola__list_meetings"],
    ["Google Drive", "Granola", "mcp__granola__list_meetings", "drive_read"],
  ] as const)(
    "keeps %s failure partial while preserving %s evidence",
    (failedSource, successfulSource, successToolName, failedToolName) => {
      const success =
        successToolName === "drive_read"
          ? tool(
              "call-success",
              successToolName,
              { file_id: "source-success" },
              {
                id: "source-success",
                name: "Successful Google Drive evidence",
                content: "The successful Drive excerpt remains visible.",
              },
            )
          : tool(
              "call-success",
              successToolName,
              {},
              {
                meetings: [
                  {
                    id: "source-success",
                    title: "Successful Granola evidence",
                    excerpt: "The successful Granola excerpt remains visible.",
                  },
                ],
              },
            );
      const failed = tool(
        "call-failed",
        failedToolName,
        {},
        { error: "PRIVATE_EVIDENCE_TRANSPORT_ERROR" },
        {
          isError: true,
          providerMetadata: {
            sourcecado: {
              failure: {
                source: failedSource,
                summary: `${failedSource} evidence is unavailable.`,
                retry_safe: true,
                idempotent: true,
                repair_route:
                  failedSource === "Granola"
                    ? "#/connections/granola"
                    : "#/connections/drive",
              },
            },
          },
        },
      );
      const { container } = renderEvidence([success, failed]);

      fireEvent.click(
        screen.getByRole("button", { name: "Checked 2 sources · Partial" }),
      );

      expect(screen.getByText("Partial evidence")).toBeInTheDocument();
      expect(
        screen.getByText(
          `${failedSource} evidence unavailable. ${successfulSource} evidence remains visible.`,
        ),
      ).toBeInTheDocument();
      expect(
        screen.getByText(`Successful ${successfulSource} evidence`),
      ).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Retry failed step" })).toBeInTheDocument();
      expect(container).not.toHaveTextContent("PRIVATE_EVIDENCE_TRANSPORT_ERROR");
    },
  );

  it("combines Drive search/read and Granola outcomes into one deduplicated evidence set", () => {
    const { container } = renderEvidence([
      tool(
        "call-drive-search",
        "drive_search",
        { query: "Q3 sourcing" },
        {
          files: [
            {
              id: "drive-file-1",
              name: "Q3 sourcing plan",
              mimeType: "text/plain",
              modifiedTime: "2026-08-01T00:00:00Z",
            },
          ],
        },
      ),
      tool(
        "call-drive-read",
        "drive_read",
        { file_id: "drive-file-1" },
        {
          id: "drive-file-1",
          name: "Q3 sourcing plan",
          mimeType: "text/plain",
          content: "Priority roles\nTarget companies",
          truncated: false,
          rawToken: "PRIVATE_DRIVE_TOKEN",
        },
      ),
      tool(
        "call-granola-list",
        "mcp__granola__list_meetings",
        { query: "hiring" },
        {
          meetings: [
            {
              id: "granola-meeting-1",
              title: "Hiring sync",
              excerpt: "Discussed the sourcing plan and interview loop.",
              url: "https://example.com/granola/hiring-sync",
            },
          ],
          authorization: "PRIVATE_GRANOLA_TOKEN",
        },
      ),
    ]);

    fireEvent.click(
      screen.getByRole("button", { name: "Checked 3 sources · Completed" }),
    );

    expect(screen.getByRole("heading", { name: "Evidence set" })).toBeInTheDocument();
    expect(screen.getByText("2 evidence items")).toBeInTheDocument();
    expect(screen.getByText("Google Drive and Granola")).toBeInTheDocument();
    expect(screen.getByText("Q3 sourcing plan")).toBeInTheDocument();
    expect(screen.getByText(/Priority roles/)).toBeInTheDocument();
    expect(screen.getByText("Hiring sync")).toBeInTheDocument();
    expect(screen.getByText(/Discussed the sourcing plan/)).toBeInTheDocument();
    expect(container).not.toHaveTextContent("PRIVATE_DRIVE_TOKEN");
    expect(container).not.toHaveTextContent("PRIVATE_GRANOLA_TOKEN");
  });
});
