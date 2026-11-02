import type { DomainRendererProps } from "../chat/toolRegistry";
import {
  CardBadge,
  CardField,
  CardRow,
  CardRows,
  ExpandableText,
  Gap,
  InspectButton,
  rawToolTarget,
  ResultCard,
  SkeletonRows,
  UnsupportedCard,
} from "./ResultCard";
import { useInspector } from "../chat/Inspector";

type CalendarMoment = {
  readonly valid: boolean;
  readonly allDay: boolean;
  readonly dateKey: string | null;
  readonly dateLabel: string | null;
  readonly timeLabel: string | null;
  readonly timezone: string | null;
};

const DESCRIPTION_PREVIEW_LIMIT = 280;

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
  failure,
  cardId,
}: DomainRendererProps) {
  const { select } = useInspector();
  const write = toolName === "calendar_create" || toolName === "calendar_update";
  const input = record(args);
  const common = {
    id: cardId,
    toolCallIds: [toolCallId],
    provider: "calendar" as const,
  };
  const failedState = failure?.class === "permission" ? "restricted" : "failed";
  const unsupported = (title: string) => (
    <UnsupportedCard
      cardId={cardId}
      toolCallIds={[toolCallId]}
      provider="calendar"
      title={title}
      inspect={rawToolTarget({ toolCallId, toolName, args, result }, title)}
    />
  );
  if (write && status === "loading") {
    const timezone = text(input?.timezone) ?? "America/Los_Angeles";
    const timing = eventTimeLabel(
      { dateTime: input?.start, timeZone: timezone },
      { dateTime: input?.end, timeZone: timezone },
    );
    const creating = toolName === "calendar_create";
    const summary = text(input?.summary);
    return (
      <ResultCard
        {...common}
        title="Calendar event"
        tag={creating ? "Not yet created" : "Not yet updated"}
        state="loading"
      >
        <CardRows label={creating ? "Creating Calendar event" : "Updating Calendar event"}>
          <CardField icon="doc" label="Event">
            {summary ?? <Gap>No title</Gap>}
          </CardField>
          <CardField icon="clock" label="When">
            {timing.valid ? <span>{timing.label}</span> : <Gap>{timing.label}</Gap>}
            {timing.timezone ? <CardBadge tone="neutral">{timing.timezone}</CardBadge> : null}
          </CardField>
        </CardRows>
      </ResultCard>
    );
  }
  if (write && status === "error") {
    return (
      <ResultCard
        {...common}
        title="Calendar event"
        state={failedState}
        note={`Calendar event was not ${toolName === "calendar_create" ? "created" : "updated"}.`}
      />
    );
  }
  if (write && status === "success") {
    const raw = record(result);
    const eventId = text(raw?.id) ?? text(input?.event_id);
    if (!raw || !eventId) return unsupported("Calendar event");
    const creating = toolName === "calendar_create";
    const timingChanged =
      creating || input?.start !== undefined || input?.end !== undefined;
    const summary = text(raw.summary) ?? text(input?.summary);
    const timezone = text(input?.timezone) ?? "America/Los_Angeles";
    const timing = eventTimeLabel(
      { dateTime: input?.start, timeZone: timezone },
      { dateTime: input?.end, timeZone: timezone },
    );
    const account = text(raw.accountEmail) ?? text(raw.account_email);
    const description = text(input?.description);
    const changed = calendarChangedFields(input ?? {});
    const externalUrl =
      text(raw.htmlLink) ?? text(raw.html_link) ?? text(raw.external_url);
    return (
      <ResultCard
        {...common}
        title="Calendar event"
        count="1 event"
        tag={creating ? "Created" : "Updated"}
        footer={
          <InspectButton
            target={{
              kind: "artifact",
              id: `calendar-write:${toolCallId}:${eventId}`,
              title: summary ?? "Untitled event",
              status: "success",
              provider: "Google Calendar",
              externalUrl,
              preview: timingChanged ? timing.label : `${creating ? "Created" : "Updated"} event`,
              result: {
                eventId,
                action: creating ? "created" : "updated",
                changedFields: changed,
                timezone: timing.timezone,
                account,
              },
            }}
          />
        }
      >
        <CardRows label="Calendar event">
          <CardField icon="doc" label="Event">
            {summary ?? <Gap>No title</Gap>}
          </CardField>
          {timingChanged ? (
            <CardField icon="clock" label="When">
              {timing.valid ? <span>{timing.label}</span> : <Gap>{timing.label}</Gap>}
              {timing.timezone ? (
                <CardBadge tone="neutral">{timing.timezone}</CardBadge>
              ) : null}
            </CardField>
          ) : null}
          <CardField icon="review" label="Changed">
            {changed.length > 0 ? changed.join(", ") : "none"}
          </CardField>
          {description ? (
            <CardRow tall>
              <ExpandableText
                text={description}
                limit={DESCRIPTION_PREVIEW_LIMIT}
                expandLabel="Expand event description"
                collapseLabel="Collapse event description"
                className="sourcecado-card-body-text"
              />
            </CardRow>
          ) : null}
          <CardField icon="link" label="Event ID">
            {eventId}
          </CardField>
          <CardField icon="user" label="Account">
            {account ?? <Gap>Account address unavailable</Gap>}
          </CardField>
        </CardRows>
      </ResultCard>
    );
  }
  if (toolName !== "calendar_list") return null;
  if (status === "error") {
    return (
      <ResultCard
        {...common}
        title="Calendar events"
        state={failedState}
        note="Calendar events unavailable."
      />
    );
  }
  if (status === "loading") {
    return (
      <ResultCard {...common} title="Calendar events" state="loading">
        <SkeletonRows label="Loading Calendar events" />
      </ResultCard>
    );
  }
  if (status !== "success") return null;
  const raw = record(result);
  if (!raw || !Array.isArray(raw.events)) return unsupported("Calendar events");
  const events = raw.events;
  const account = text(raw.accountEmail) ?? text(raw.account_email);
  if (events.length === 0) {
    return (
      <ResultCard
        {...common}
        title="Calendar events"
        count="0 events"
        state="empty"
        note="No upcoming events matched this Calendar window."
      />
    );
  }
  const rows = events.map((value, index) => {
    const event = record(value);
    const summary = text(event?.summary);
    const eventId = text(event?.id) ?? `calendar-event-${index + 1}`;
    const timing = eventTimeLabel(event?.start, event?.end);
    const externalUrl =
      text(event?.htmlLink) ?? text(event?.html_link) ?? text(event?.url);
    return { summary, eventId, timing, externalUrl };
  });
  const incomplete = rows.filter((row) => !row.summary || !row.timing.valid).length;
  return (
    <ResultCard
      {...common}
      title="Upcoming Calendar events"
      count={`${events.length} ${events.length === 1 ? "event" : "events"}`}
      state={incomplete > 0 ? "partial" : "ready"}
      note={
        incomplete > 0
          ? `${incomplete} of ${events.length} events lack a title or a valid date.`
          : undefined
      }
      footer={
        <InspectButton
          target={{
            kind: "source",
            id: `calendar-source:${toolCallId}`,
            title: "Google Calendar source",
            status: "success",
            provider: "Google Calendar",
            preview: account ?? "Connected Google Calendar account unavailable",
            result: { eventCount: events.length, account },
          }}
        />
      }
    >
      <CardRows label="Calendar events">
        {rows.map(({ summary, eventId, timing, externalUrl }) => (
          <CardRow key={eventId}>
            <span className="sourcecado-card-cell sourcecado-card-cell-when">
              {timing.valid ? timing.label : <Gap>Date unavailable or invalid</Gap>}
            </span>
            <span className="sourcecado-card-cell sourcecado-card-cell-name">
              <button
                type="button"
                className="sourcecado-card-name"
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
              {!summary ? <Gap>No title</Gap> : null}
            </span>
            {timing.timezone ? (
              <span className="sourcecado-card-cell">
                <CardBadge tone="neutral">{timing.timezone}</CardBadge>
              </span>
            ) : null}
          </CardRow>
        ))}
      </CardRows>
    </ResultCard>
  );
}
