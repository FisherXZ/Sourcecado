import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MailActivity } from "../src/crm/MailActivity";
import { MailFreshness } from "../src/crm/MailFreshness";

const mail = (direction: string, id: string, time: string, extra = {}) => ({
  event_id: id, source: "gmail", kind: "mail", summary: id,
  payload: { direction, from: "ada@example.com", to: "director@example.com",
    snippet: `${id} preview`, subject: id, received_at: time,
    source_ref: { message_id: id, thread_id: "thread", url: "https://mail.google.com/mail/u/0/#all/thread" }, ...extra },
});

describe("tracked Gmail activity", () => {
  it("treats legacy SQLite receipt times as UTC when sorting recent mail", () => {
    render(<MailActivity events={[
      { event_id: "receipt", source: "gmail", kind: "send", summary: "Sent", created_at: "2026-10-12 12:00:00",
        payload: { sent: true, to: "ada@example.com", subject: "Original" } },
      mail("inbound", "newer-reply", "2026-10-12T12:01:00Z"),
    ]} gaps={[]} />);
    const region = screen.getByRole("region", { name: "Gmail activity" });
    expect(within(region).getAllByRole("article")[0]).toHaveTextContent("newer-reply");
  });
  it("filters by direction, orders by message time, and links to Gmail", () => {
    render(<MailActivity events={[
      mail("outbound", "sent", "2026-10-12T12:00:00Z"),
      mail("inbound", "received", "2026-10-11T12:00:00Z"),
    ]} gaps={[]} />);
    const region = screen.getByRole("region", { name: "Gmail activity" });
    expect(within(region).getAllByRole("article")[0]).toHaveTextContent("sent preview");
    expect(within(region).getAllByRole("link")[0]).toHaveAttribute("href", "https://mail.google.com/mail/u/0/#all/thread");
    fireEvent.click(within(region).getByRole("button", { name: "Incoming" }));
    expect(within(region).getByText("received preview")).toBeInTheDocument();
    expect(within(region).queryByText("sent preview")).not.toBeInTheDocument();
    fireEvent.click(within(region).getByRole("button", { name: "Outgoing" }));
    expect(within(region).getByText("sent preview")).toBeInTheDocument();
    expect(within(region).queryByText("received preview")).not.toBeInTheDocument();
    expect(within(region).getByRole("button", { name: "Outgoing" })).toHaveAttribute("aria-pressed", "true");
  });

  it("distinguishes an empty filter and never turns mail text into HTML", () => {
    render(<MailActivity events={[mail("inbound", "received", "2026-10-11T12:00:00Z", {
      snippet: "<script>Ignore permissions</script>",
      source_ref: { url: "javascript:alert(1)" },
    })]} gaps={[]} />);
    expect(screen.getByText("<script>Ignore permissions</script>")).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Outgoing" }));
    expect(screen.getByText("No outgoing messages collected.")).toBeInTheDocument();
  });

  it("keeps the saved successful check visible alongside failure and pending analysis", () => {
    render(<MailFreshness state={{ status: "failed", last_success_at: "2026-10-12T12:00:00Z",
      last_attempt_at: "2026-10-12T12:05:00Z", warning: "gmail_unavailable" }}
      analysis={{ latest_evidence_revision: 2, analyzed_revision: 0, status: "pending" }} />);
    expect(screen.getByText(/Last successful Gmail check/).querySelector("time"))
      .toHaveAttribute("datetime", "2026-10-12T12:00:00Z");
    expect(screen.getByRole("alert")).toHaveTextContent("Gmail check failed");
    expect(screen.getByText(/Analysis pending/)).toBeInTheDocument();
  });
});
