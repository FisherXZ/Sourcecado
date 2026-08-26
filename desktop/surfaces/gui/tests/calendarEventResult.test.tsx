import { fireEvent, render, screen, within } from "@testing-library/react";
import type { ToolCallMessagePart } from "@assistant-ui/react";
import { describe, expect, it } from "vitest";

import { ActivityGroup } from "../src/chat/ActivityGroup";
import { Inspector, InspectorProvider } from "../src/chat/Inspector";

function calendarList(
  overrides: Partial<ToolCallMessagePart> = {},
): ToolCallMessagePart {
  return {
    type: "tool-call",
    toolCallId: "calendar-list-1",
    toolName: "calendar_list",
    args: { max_results: 10 },
    argsText: "{}",
    result: {
      events: [
        {
          id: "event-valid",
          summary: "Sourcing standup",
          start: {
            dateTime: "2026-08-25T10:00:00",
            timeZone: "America/Los_Angeles",
          },
          end: {
            dateTime: "2026-08-25T10:30:00",
            timeZone: "America/Los_Angeles",
          },
        },
        {
          id: "event-partial",
          summary: null,
          start: { dateTime: "not-a-date", timeZone: "Mars/Olympus" },
          end: null,
        },
      ],
      rawPayload: "PRIVATE_CALENDAR_PAYLOAD",
    },
    ...overrides,
  };
}

function calendarWrite(
  toolName: "calendar_create" | "calendar_update" = "calendar_create",
  overrides: Partial<ToolCallMessagePart> = {},
): ToolCallMessagePart {
  return {
    type: "tool-call",
    toolCallId: `${toolName}-1`,
    toolName,
    args:
      toolName === "calendar_create"
        ? {
            summary: "Candidate interview",
            start: "2026-08-25T10:00:00",
            end: "2026-08-25T10:30:00",
            timezone: "America/Los_Angeles",
            description: "Discuss the role.",
          }
        : {
            event_id: "event-existing",
            summary: "Candidate interview updated",
          },
    argsText: "{}",
    result: {
      id: "event-created",
      summary: "Candidate interview",
      htmlLink: "https://calendar.google.com/event?eid=created",
    },
    ...overrides,
  };
}

function renderCalendar(tool = calendarList()) {
  return render(
    <InspectorProvider threadId="thread-calendar">
      <ActivityGroup tools={[tool]} messageState="complete" />
      <Inspector />
    </InspectorProvider>,
  );
}

describe("Calendar event result", () => {
  it("shows a truthful event-shaped state while an approved create is running", () => {
    renderCalendar(
      calendarWrite("calendar_create", {
        result: undefined,
        approval: {
          id: "approval-calendar-create",
          approved: true,
          reason: "Creating an event changes Google Calendar.",
        },
      }),
    );

    expect(
      screen.getByRole("heading", { name: "Creating Calendar event" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Creating · Not yet created")).toBeInTheDocument();
    expect(screen.getByText("Aug 25, 2026 · 10:00 AM–10:30 AM")).toBeInTheDocument();
    expect(screen.getByText("America/Los_Angeles")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /delete/i })).not.toBeInTheDocument();
  });

  it("renders a created Calendar event as a truthful review artifact", () => {
    renderCalendar(calendarWrite("calendar_create"));
    fireEvent.click(
      screen.getByRole("button", { name: "Prepared calendar event · Completed" }),
    );

    expect(
      screen.getByRole("heading", { name: "Calendar event created" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Created")).toBeInTheDocument();
    expect(screen.getByText("Event ID: event-created")).toBeInTheDocument();
    expect(screen.getByText("Candidate interview")).toBeInTheDocument();
    expect(screen.getByText("Aug 25, 2026 · 10:00 AM–10:30 AM")).toBeInTheDocument();
    expect(screen.getByText("America/Los_Angeles")).toBeInTheDocument();
    expect(
      screen.getByText("Google Calendar account address unavailable; event is still available."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /delete/i })).not.toBeInTheDocument();
  });

  it("renders only the exact fields changed by a Calendar update", () => {
    renderCalendar(
      calendarWrite("calendar_update", {
        result: { id: "event-existing", summary: "Candidate interview updated" },
      }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Updated calendar event · Completed" }),
    );

    expect(
      screen.getByRole("heading", { name: "Calendar event updated" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Updated")).toBeInTheDocument();
    expect(screen.getByText("Event ID: event-existing")).toBeInTheDocument();
    expect(screen.getByText("Candidate interview updated")).toBeInTheDocument();
    expect(screen.getByText("Changed fields: title")).toBeInTheDocument();
    expect(screen.queryByText("Date unavailable or invalid")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /delete/i })).not.toBeInTheDocument();
  });

  it.each([
    ["calendar_create", "Prepared calendar event", "created"],
    ["calendar_update", "Updated calendar event", "updated"],
  ] as const)(
    "keeps a failed %s write truthful",
    (toolName, activityLabel, verb) => {
      const { container } = renderCalendar(
        calendarWrite(toolName, {
          isError: true,
          result: { error: "PRIVATE_CALENDAR_WRITE_ERROR" },
          providerMetadata: {
            sourcecado: {
              failure: { summary: "Google Calendar couldn’t save this event." },
            },
          },
        }),
      );
      fireEvent.click(
        screen.getByRole("button", { name: `${activityLabel} · Failed` }),
      );

      expect(screen.getByRole("alert")).toHaveTextContent(
        "Google Calendar couldn’t save this event.",
      );
      expect(
        screen.getByText(`Calendar event was not ${verb}.`),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("heading", { name: `Calendar event ${verb}` }),
      ).not.toBeInTheDocument();
      expect(container).not.toHaveTextContent("PRIVATE_CALENDAR_WRITE_ERROR");
    },
  );

  it("clamps descriptions and opens safe write artifact provenance", () => {
    const description = `Discuss the sourcing role. ${"Context ".repeat(45)}PRIVATE_DESCRIPTION_TAIL`;
    const { container } = renderCalendar(
      calendarWrite("calendar_create", {
        args: {
          summary: "Candidate interview",
          start: "2026-08-25T10:00:00",
          end: "2026-08-25T10:30:00",
          timezone: "America/Los_Angeles",
          description,
        },
        result: {
          id: "event-created",
          summary: "Candidate interview",
          htmlLink: "javascript:alert('unsafe')",
          account_email: "operator@example.com",
        },
      }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Prepared calendar event · Completed" }),
    );

    expect(container).not.toHaveTextContent("PRIVATE_DESCRIPTION_TAIL");
    const expand = screen.getByRole("button", { name: "Expand event description" });
    expand.focus();
    fireEvent.click(expand, { detail: 0 });
    expect(container).toHaveTextContent("PRIVATE_DESCRIPTION_TAIL");
    expect(
      screen.getByRole("button", { name: "Collapse event description" }),
    ).toHaveFocus();

    fireEvent.click(
      screen.getByRole("button", { name: "Inspect Calendar event event-created" }),
    );
    const inspector = screen.getByRole("complementary", { name: "Inspector" });
    expect(within(inspector).getByRole("heading", { level: 2 })).toHaveTextContent(
      "Candidate interview",
    );
    expect(within(inspector).getByText("External URL unavailable")).toBeInTheDocument();
    expect(within(inspector).queryByRole("link")).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "Inspect Calendar source" }),
    );
    expect(within(inspector).getByRole("heading", { level: 2 })).toHaveTextContent(
      "Google Calendar source",
    );
    expect(within(inspector).getByText("operator@example.com")).toBeInTheDocument();
  });

  it("uses event-shaped rows while Calendar is loading", () => {
    renderCalendar(calendarList({ result: undefined }));

    expect(
      screen.getByRole("heading", { name: "Loading Calendar events" }),
    ).toBeInTheDocument();
    const skeleton = screen.getByRole("list", {
      name: "Loading Calendar events",
    });
    expect(skeleton).toHaveAttribute("aria-busy", "true");
    expect(skeleton.querySelectorAll("li")).toHaveLength(3);
  });

  it("renders a clear empty Calendar window without inventing actions", () => {
    renderCalendar(calendarList({ result: { events: [] } }));
    fireEvent.click(
      screen.getByRole("button", { name: "Checked calendar · Completed" }),
    );

    expect(
      screen.getByRole("heading", { name: "No Calendar events" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("No upcoming events matched this Calendar window."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /delete/i })).not.toBeInTheDocument();
  });

  it("falls back safely for malformed Calendar results and keeps payloads in Inspector", () => {
    const { container } = renderCalendar(
      calendarList({ result: { events: "PRIVATE_LEGACY_CALENDAR_PAYLOAD" } }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Checked calendar · Completed" }),
    );

    expect(
      screen.getByRole("heading", { name: "Calendar result needs review" }),
    ).toBeInTheDocument();
    expect(container).not.toHaveTextContent("PRIVATE_LEGACY_CALENDAR_PAYLOAD");
    fireEvent.click(
      screen.getByRole("button", { name: "Inspect Checked calendar" }),
    );
    expect(
      screen.getByRole("complementary", { name: "Inspector" }),
    ).toHaveTextContent("PRIVATE_LEGACY_CALENDAR_PAYLOAD");
  });

  it("keeps Calendar list failures contextual without exposing provider payloads", () => {
    const { container } = renderCalendar(
      calendarList({
        isError: true,
        result: { error: "PRIVATE_CALENDAR_PROVIDER_ERROR" },
        providerMetadata: {
          sourcecado: {
            failure: { summary: "Calendar couldn’t load upcoming events." },
          },
        },
      }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Checked calendar · Failed" }),
    );

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Calendar couldn’t load upcoming events.",
    );
    expect(screen.getByText("Calendar events unavailable.")).toBeInTheDocument();
    expect(container).not.toHaveTextContent("PRIVATE_CALENDAR_PROVIDER_ERROR");
  });

  it("formats authoritative all-day and offset dates but rejects ambiguous local time", () => {
    renderCalendar(
      calendarList({
        result: {
          events: [
            {
              id: "all-day",
              summary: "Team offsite",
              start: { date: "2026-08-26" },
              end: { date: "2026-08-27" },
            },
            {
              id: "offset-event",
              summary: "Candidate interview",
              start: { dateTime: "2026-08-25T09:00:00-07:00" },
              end: { dateTime: "2026-08-25T09:30:00-07:00" },
            },
            {
              id: "ambiguous-event",
              summary: "Ambiguous hold",
              start: { dateTime: "2026-08-25T11:00:00" },
              end: { dateTime: "2026-08-25T11:30:00" },
            },
            {
              id: "zoned-instant",
              summary: "Converted interview",
              start: {
                dateTime: "2026-08-25T17:00:00Z",
                timeZone: "America/Los_Angeles",
              },
              end: {
                dateTime: "2026-08-25T17:30:00Z",
                timeZone: "America/Los_Angeles",
              },
            },
          ],
        },
      }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Checked calendar · Completed" }),
    );

    expect(screen.getByText("Aug 26, 2026 · All day")).toBeInTheDocument();
    expect(screen.getByText("Aug 25, 2026 · 9:00 AM–9:30 AM")).toBeInTheDocument();
    expect(screen.getByText("UTC−07:00")).toBeInTheDocument();
    expect(screen.getByText("Aug 25, 2026 · 10:00 AM–10:30 AM")).toBeInTheDocument();
    expect(screen.getByText("Converted interview")).toBeInTheDocument();
    expect(screen.getAllByText("Date unavailable or invalid")).toHaveLength(1);
  });

  it("opens stable event and source targets while keeping unsafe URLs non-clickable", () => {
    renderCalendar(
      calendarList({
        result: {
          account_email: "operator@example.com",
          events: [
            {
              id: "event-inspect",
              summary: "Inspect this event",
              start: {
                dateTime: "2026-08-25T14:00:00",
                timeZone: "America/Los_Angeles",
              },
              end: {
                dateTime: "2026-08-25T14:30:00",
                timeZone: "America/Los_Angeles",
              },
              htmlLink: "javascript:alert('unsafe')",
            },
          ],
        },
      }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Checked calendar · Completed" }),
    );
    fireEvent.click(
      screen.getByRole("button", {
        name: "Inspect Calendar event Inspect this event",
      }),
    );

    const inspector = screen.getByRole("complementary", { name: "Inspector" });
    expect(within(inspector).getByRole("heading", { level: 2 })).toHaveTextContent(
      "Inspect this event",
    );
    expect(within(inspector).getByText("External URL unavailable")).toBeInTheDocument();
    expect(within(inspector).queryByRole("link")).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "Inspect Calendar source" }),
    );
    expect(within(inspector).getByRole("heading", { level: 2 })).toHaveTextContent(
      "Google Calendar source",
    );
    expect(within(inspector).getByText("operator@example.com")).toBeInTheDocument();
  });

  it("renders valid events while visibly preserving invalid partial rows", () => {
    const { container } = renderCalendar();
    fireEvent.click(
      screen.getByRole("button", { name: "Checked calendar · Completed" }),
    );

    expect(
      screen.getByRole("heading", { name: "Upcoming Calendar events" }),
    ).toBeInTheDocument();
    expect(screen.getByText("2 events")).toBeInTheDocument();
    expect(screen.getByText("Sourcing standup")).toBeInTheDocument();
    expect(screen.getByText("Aug 25, 2026 · 10:00 AM–10:30 AM")).toBeInTheDocument();
    expect(screen.getByText("America/Los_Angeles")).toBeInTheDocument();
    expect(screen.getByText("Untitled event")).toBeInTheDocument();
    expect(screen.getByText("Date unavailable or invalid")).toBeInTheDocument();
    expect(
      screen.getByText("Missing title and valid date from Google Calendar source."),
    ).toBeInTheDocument();
    expect(container).not.toHaveTextContent("PRIVATE_CALENDAR_PAYLOAD");
    expect(screen.queryByRole("button", { name: /delete/i })).not.toBeInTheDocument();
  });
});
