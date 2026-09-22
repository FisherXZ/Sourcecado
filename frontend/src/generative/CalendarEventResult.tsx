import { useId, useState } from "react";

import { useInspector } from "../chat/Inspector";
import type { DomainRendererProps } from "../chat/toolRegistry";

type CalendarMoment = {
  readonly valid: boolean;
  readonly allDay: boolean;
  readonly dateKey: string | null;
  readonly dateLabel: string | null;
  readonly timeLabel: string | null;
  readonly timezone: string | null;
};

const DESCRIPTION_PREVIEW_LIMIT = 280;

function CalendarDescription({ description }: { readonly description: string }) {
  const [expanded, setExpanded] = useState(false);
  const descriptionId = useId();
  const truncated = description.length > DESCRIPTION_PREVIEW_LIMIT;
  const visible =
    truncated && !expanded
      ? `${description.slice(0, DESCRIPTION_PREVIEW_LIMIT).trimEnd()}…`
      : description;
  return (
    <div className="sourcecado-calendar-description">
      <p id={descriptionId}>{visible}</p>
      {truncated ? (
        <button
          type="button"
          aria-controls={descriptionId}
          aria-expanded={expanded}
          onClick={() => setExpanded((current) => !current)}
        >
          {expanded ? "Collapse event description" : "Expand event description"}
        </button>
      ) : null}
    </div>
  );
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function validTimezone(value: string): boolean {
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: value }).format(new Date(0));
    return true;
  } catch {
    return false;
  }
}

function validParts(
  year: number,
  month: number,
  day: number,
  hour = 0,
  minute = 0,
  second = 0,
): Date | null {
  const probe = new Date(Date.UTC(year, month - 1, day, hour, minute, second));
  return probe.getUTCFullYear() === year &&
    probe.getUTCMonth() === month - 1 &&
    probe.getUTCDate() === day &&
    probe.getUTCHours() === hour &&
    probe.getUTCMinutes() === minute &&
    probe.getUTCSeconds() === second
    ? probe
    : null;
}

function displayDate(probe: Date): string {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(probe);
}

function displayTime(probe: Date): string {
  return new Intl.DateTimeFormat("en-US", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
    timeZone: "UTC",
  }).format(probe);
}

function displayInTimezone(instant: Date, timezone: string): CalendarMoment {
  const dateParts = new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone: timezone,
  }).formatToParts(instant);
  const part = (type: Intl.DateTimeFormatPartTypes) =>
    dateParts.find((item) => item.type === type)?.value ?? "";
  return {
    valid: true,
    allDay: false,
    dateKey: `${part("year")}-${part("month")}-${part("day")}`,
    dateLabel: new Intl.DateTimeFormat("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
      timeZone: timezone,
    }).format(instant),
    timeLabel: new Intl.DateTimeFormat("en-US", {
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
      timeZone: timezone,
    }).format(instant),
    timezone,
  };
}

function calendarMoment(value: unknown): CalendarMoment {
  const raw = record(value);
  const date = text(raw?.date);
  const dateMatch = date?.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (dateMatch) {
    const [, yearText, monthText, dayText] = dateMatch;
    const probe = validParts(Number(yearText), Number(monthText), Number(dayText));
    if (probe) {
      return {
        valid: true,
        allDay: true,
        dateKey: date,
        dateLabel: displayDate(probe),
        timeLabel: "All day",
        timezone: null,
      };
    }
  }
  const dateTime = text(raw?.dateTime);
  const timezone = text(raw?.timeZone);
  const offsetMatch = dateTime?.match(
    /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?(Z|[+-]\d{2}:\d{2})$/,
  );
  if (offsetMatch && (!timezone || validTimezone(timezone))) {
    const [, yearText, monthText, dayText, hourText, minuteText, secondText, offset] =
      offsetMatch;
    const probe = validParts(
      Number(yearText),
      Number(monthText),
      Number(dayText),
      Number(hourText),
      Number(minuteText),
      Number(secondText ?? "0"),
    );
    if (probe && !Number.isNaN(Date.parse(dateTime ?? ""))) {
      if (timezone) {
        return displayInTimezone(new Date(dateTime ?? ""), timezone);
      }
      return {
        valid: true,
        allDay: false,
        dateKey: `${yearText}-${monthText}-${dayText}`,
        dateLabel: displayDate(probe),
        timeLabel: displayTime(probe),
        timezone:
          timezone ?? (offset === "Z" ? "UTC" : `UTC${offset.replace("-", "−")}`),
      };
    }
  }
  const match = dateTime?.match(
    /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/,
  );
  if (!match || !timezone || !validTimezone(timezone)) {
    return {
      valid: false,
      allDay: false,
      dateKey: null,
      dateLabel: null,
      timeLabel: null,
      timezone,
    };
  }
  const [, yearText, monthText, dayText, hourText, minuteText, secondText] = match;
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const hour = Number(hourText);
  const minute = Number(minuteText);
  const second = Number(secondText ?? "0");
  const probe = validParts(year, month, day, hour, minute, second);
  if (!probe) {
    return {
      valid: false,
      allDay: false,
      dateKey: null,
      dateLabel: null,
      timeLabel: null,
      timezone,
    };
  }
  return {
    valid: true,
    allDay: false,
    dateKey: `${yearText}-${monthText}-${dayText}`,
    dateLabel: displayDate(probe),
    timeLabel: displayTime(probe),
    timezone,
  };
}

function eventTimeLabel(startValue: unknown, endValue: unknown) {
  const start = calendarMoment(startValue);
  const end = calendarMoment(endValue);
  if (start.valid && start.allDay && end.valid && end.allDay) {
    return {
      label: `${start.dateLabel} · All day`,
      timezone: null,
      valid: true,
    };
  }
  if (
    !start.valid ||
    !end.valid ||
    start.allDay ||
    end.allDay ||
    start.dateKey !== end.dateKey ||
    start.timezone !== end.timezone
  ) {
    return { label: "Date unavailable or invalid", timezone: start.timezone, valid: false };
  }
  return {
    label: `${start.dateLabel} · ${start.timeLabel}–${end.timeLabel}`,
    timezone: start.timezone,
    valid: true,
  };
}

function missingLabel(missing: readonly string[]): string | null {
  if (missing.length === 0) return null;
  if (missing.length === 1) return `Missing ${missing[0]} from Google Calendar source.`;
  return `Missing ${missing.slice(0, -1).join(", ")} and ${missing.at(-1)} from Google Calendar source.`;
}

function approvalDateLabel(value: unknown, timezone: string | null): string {
  const moment = calendarMoment({ dateTime: value, timeZone: timezone });
  return moment.valid && !moment.allDay
    ? `${moment.dateLabel} · ${moment.timeLabel}`
    : "Date unavailable or invalid";
}

function calendarChangedFields(args: Readonly<Record<string, unknown>>): string[] {
  return [
    text(args.summary) ? "title" : null,
    args.start !== undefined ? "start" : null,
    args.end !== undefined ? "end" : null,
    args.timezone !== undefined ? "timezone" : null,
    args.description !== undefined ? "description" : null,
  ].filter((field): field is string => field !== null);
}

export function CalendarApprovalSummary({
  args,
  toolName,
}: {
  readonly args: Readonly<Record<string, unknown>>;
  readonly toolName: "calendar_create" | "calendar_update";
}) {
  const timezone = text(args.timezone) ?? "America/Los_Angeles";
  const summary = text(args.summary);
  const start = args.start === undefined ? null : approvalDateLabel(args.start, timezone);
  const end = args.end === undefined ? null : approvalDateLabel(args.end, timezone);
  const changedFields = calendarChangedFields(args);

  return (
    <div className="sourcecado-calendar-approval-summary">
      <p>Google Calendar · Connected Google account</p>
      <dl>
        {toolName === "calendar_update" ? (
          <div>
            <dt>Event ID</dt>
            <dd>{text(args.event_id) ?? "Unavailable"}</dd>
          </div>
        ) : null}
        {summary ? (
          <div>
            <dt>Event</dt>
            <dd>{summary}</dd>
          </div>
        ) : null}
        {start ? (
          <div>
            <dt>Starts</dt>
            <dd>{start}</dd>
          </div>
        ) : null}
        {end ? (
          <div>
            <dt>Ends</dt>
            <dd>{end}</dd>
          </div>
        ) : null}
        <div>
          <dt>Timezone</dt>
          <dd>{validTimezone(timezone) ? timezone : "Timezone unavailable or invalid"}</dd>
        </div>
      </dl>
      <p>
        Changed fields: {changedFields.length > 0 ? changedFields.join(", ") : "none"}
      </p>
    </div>
  );
}

export function CalendarEventResult({
  args,
  result,
  status,
  toolCallId,
  toolName,
}: DomainRendererProps) {
  const { select } = useInspector();
  const write = toolName === "calendar_create" || toolName === "calendar_update";
  const input = record(args);
  if (write && status === "loading") {
    const timezone = text(input?.timezone) ?? "America/Los_Angeles";
    const timing = eventTimeLabel(
      { dateTime: input?.start, timeZone: timezone },
      { dateTime: input?.end, timeZone: timezone },
    );
    const creating = toolName === "calendar_create";
    return (
      <section className="sourcecado-calendar-result sourcecado-calendar-write-running">
        <h3>{creating ? "Creating" : "Updating"} Calendar event</h3>
        <p>{creating ? "Creating · Not yet created" : "Updating · Not yet updated"}</p>
        {text(input?.summary) ? <strong>{text(input?.summary)}</strong> : null}
        <p>{timing.label}</p>
        {timing.timezone ? <p>{timing.timezone}</p> : null}
      </section>
    );
  }
  if (write && status === "error") {
    return (
      <p className="sourcecado-calendar-failed">
        Calendar event was not {toolName === "calendar_create" ? "created" : "updated"}.
      </p>
    );
  }
  if (write && status === "success") {
    const raw = record(result);
    const eventId = text(raw?.id) ?? text(input?.event_id);
    if (!raw || !eventId) {
      return (
        <section className="sourcecado-calendar-result sourcecado-calendar-fallback">
          <h3>Calendar write result needs review</h3>
          <p>Sourcecado couldn’t verify the Calendar event. Use Inspect above to review it.</p>
        </section>
      );
    }
    const creating = toolName === "calendar_create";
    const timingChanged =
      creating || input?.start !== undefined || input?.end !== undefined;
    const summary = text(raw.summary) ?? text(input?.summary) ?? "Untitled event";
    const timezone = text(input?.timezone) ?? "America/Los_Angeles";
    const timing = eventTimeLabel(
      { dateTime: input?.start, timeZone: timezone },
      { dateTime: input?.end, timeZone: timezone },
    );
    const account = text(raw.accountEmail) ?? text(raw.account_email);
    const description = text(input?.description);
    const externalUrl =
      text(raw.htmlLink) ?? text(raw.html_link) ?? text(raw.external_url);
    return (
      <section className="sourcecado-calendar-result sourcecado-calendar-write-result">
        <header>
          <div>
            <h3>Calendar event {creating ? "created" : "updated"}</h3>
            <strong>{creating ? "Created" : "Updated"}</strong>
          </div>
          <p>Event ID: {eventId}</p>
        </header>
        <p>{summary}</p>
        {timingChanged ? <p>{timing.label}</p> : null}
        {timingChanged && timing.timezone ? <p>{timing.timezone}</p> : null}
        <p>Changed fields: {calendarChangedFields(input ?? {}).join(", ") || "none"}</p>
        {description ? <CalendarDescription description={description} /> : null}
        {account ? (
          <p>Google Calendar · {account}</p>
        ) : (
          <p>Google Calendar account address unavailable; event is still available.</p>
        )}
        <div className="sourcecado-calendar-actions">
          <button
            type="button"
            aria-label={`Inspect Calendar event ${eventId}`}
            onClick={(event) =>
              select(
                {
                  kind: "artifact",
                  id: `calendar-write:${toolCallId}:${eventId}`,
                  title: summary,
                  status: "success",
                  provider: "Google Calendar",
                  externalUrl,
                  preview: timingChanged ? timing.label : `${creating ? "Created" : "Updated"} event`,
                  result: {
                    eventId,
                    action: creating ? "created" : "updated",
                    changedFields: calendarChangedFields(input ?? {}),
                    timezone: timing.timezone,
                  },
                },
                event.currentTarget,
              )
            }
          >
            Event details
          </button>
          <button
            type="button"
            aria-label="Inspect Calendar source"
            onClick={(event) =>
              select(
                {
                  kind: "source",
                  id: `calendar-source:${toolCallId}`,
                  title: "Google Calendar source",
                  status: "success",
                  provider: "Google Calendar",
                  preview: account ?? "Connected Google Calendar account unavailable",
                  result: { resource: "primary calendar", account },
                },
                event.currentTarget,
              )
            }
          >
            Source
          </button>
        </div>
      </section>
    );
  }
  if (toolName !== "calendar_list") return null;
  if (status === "error") {
    return <p className="sourcecado-calendar-failed">Calendar events unavailable.</p>;
  }
  if (status === "loading") {
    return (
      <section className="sourcecado-calendar-result sourcecado-calendar-loading">
        <h3>Loading Calendar events</h3>
        <ol aria-label="Loading Calendar events" aria-busy="true">
          {[0, 1, 2].map((row) => (
            <li key={row}>
              <span />
              <span />
            </li>
          ))}
        </ol>
      </section>
    );
  }
  if (status !== "success") return null;
  const raw = record(result);
  if (!raw || !Array.isArray(raw.events)) {
    return (
      <section className="sourcecado-calendar-result sourcecado-calendar-fallback">
        <h3>Calendar result needs review</h3>
        <p>Sourcecado couldn’t summarize this Calendar result safely. Use Inspect above to review it.</p>
      </section>
    );
  }
  const events = Array.isArray(raw?.events) ? raw.events : [];
  const account = text(raw.accountEmail) ?? text(raw.account_email);
  if (events.length === 0) {
    return (
      <section className="sourcecado-calendar-result sourcecado-calendar-empty">
        <h3>No Calendar events</h3>
        <p>No upcoming events matched this Calendar window.</p>
      </section>
    );
  }

  return (
    <section className="sourcecado-calendar-result" aria-label="Calendar event list">
      <header>
        <div>
          <h3>Upcoming Calendar events</h3>
          <p>{events.length} events</p>
        </div>
        <button
          type="button"
          aria-label="Inspect Calendar source"
          onClick={(event) =>
            select(
              {
                kind: "source",
                id: `calendar-source:${toolCallId}`,
                title: "Google Calendar source",
                status: "success",
                provider: "Google Calendar",
                preview: account ?? "Connected Google Calendar account unavailable",
                result: { eventCount: events.length, account },
              },
              event.currentTarget,
            )
          }
        >
          Source
        </button>
      </header>
      <ol aria-label="Calendar events">
        {events.map((value, index) => {
          const event = record(value);
          const summary = text(event?.summary);
          const eventId = text(event?.id) ?? `calendar-event-${index + 1}`;
          const timing = eventTimeLabel(event?.start, event?.end);
          const externalUrl =
            text(event?.htmlLink) ?? text(event?.html_link) ?? text(event?.url);
          const missing = [
            !summary ? "title" : null,
            !timing.valid ? "valid date" : null,
          ].filter((field): field is string => field !== null);
          return (
            <li key={eventId}>
              <button
                type="button"
                className="sourcecado-calendar-event-title"
                aria-label={`Inspect Calendar event ${summary ?? "Untitled event"}`}
                onClick={(trigger) =>
                  select(
                    {
                      kind: "artifact",
                      id: `calendar-event:${toolCallId}:${eventId}`,
                      title: summary ?? "Untitled event",
                      status: "success",
                      provider: "Google Calendar",
                      externalUrl,
                      preview: timing.label,
                      result: {
                        eventId,
                        summary,
                        timezone: timing.timezone,
                        dateStatus: timing.valid ? "valid" : "invalid",
                      },
                    },
                    trigger.currentTarget,
                  )
                }
              >
                {summary ?? "Untitled event"}
              </button>
              <p>{timing.label}</p>
              {timing.timezone ? <p>{timing.timezone}</p> : null}
              {missingLabel(missing) ? <p>{missingLabel(missing)}</p> : null}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
