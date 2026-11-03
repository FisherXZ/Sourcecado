import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OutreachPanel } from "../src/OutreachPanel";

const api = vi.hoisted(() => ({
  createOutreachDraft: vi.fn(),
  readOutreachDraft: vi.fn(),
  requestSendApproval: vi.fn(),
  decideApproval: vi.fn(),
  openPersonSourcingChat: vi.fn(),
}));

vi.mock("../src/api", () => api);

const DRAFT = {
  id: "draft_1",
  to: "ada@analytic.example",
  subject: "Thursday?",
  body: "Hi Ada,\n\nWould Thursday work?",
  body_digest: "abc123def456789",
  account: "director@sourcecado.test",
  sent: false,
};

function approvalFor(digest: string) {
  return {
    id: "send_1",
    name: "gmail_send",
    state: "pending",
    resource: {
      kind: "gmail_send_authority" as const,
      person_id: "per_1",
      draft_id: "draft_1",
      account: "director@sourcecado.test",
      to: "ada@analytic.example",
      subject: "Thursday?",
      body_digest: digest,
    },
  };
}

function panel(overrides: Record<string, unknown> = {}) {
  return render(
    <OutreachPanel
      personId="per_1"
      sessionId="sess_1"
      recipient="ada@analytic.example"
      {...overrides}
    />,
  );
}

async function composeAndDraft() {
  panel();
  fireEvent.click(screen.getByRole("button", { name: "Draft email" }));
  fireEvent.change(screen.getByLabelText("Subject"), {
    target: { value: "Thursday?" },
  });
  fireEvent.change(screen.getByLabelText("Message"), {
    target: { value: "Hi Ada,\n\nWould Thursday work?" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Create Gmail draft" }));
  await screen.findByText("Not sent");
}

describe("Outreach panel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.createOutreachDraft.mockResolvedValue(DRAFT);
    api.readOutreachDraft.mockResolvedValue(DRAFT);
    api.requestSendApproval.mockResolvedValue(approvalFor(DRAFT.body_digest));
    api.decideApproval.mockResolvedValue({
      ok: true,
      result: { message_id: "msg_1", thread_id: "thread_1", sent: true },
    });
  });

  it("opens the person's sourcing chat from Draft email when there is none", async () => {
    api.openPersonSourcingChat.mockResolvedValue({
      created: true,
      session: { id: "sess_new", title: null, n_msgs: 0 },
      active_person: { person_id: "per_1", version: 3, label: "Ada" },
    });
    const onChatOpened = vi.fn();
    panel({ sessionId: null, personVersion: 3, onChatOpened });

    // Draft email shows even before a chat exists; nothing opens until it is clicked.
    expect(screen.queryByText(/sourcing chat first/)).toBeNull();
    expect(api.openPersonSourcingChat).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Draft email" }));

    expect(await screen.findByLabelText("Subject")).toBeInTheDocument();
    expect(api.openPersonSourcingChat).toHaveBeenCalledWith("per_1", 3);
    expect(onChatOpened).toHaveBeenCalledTimes(1);

    // The draft is made in the chat that was just opened.
    fireEvent.change(screen.getByLabelText("Subject"), { target: { value: "Thursday?" } });
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Hi Ada" } });
    fireEvent.click(screen.getByRole("button", { name: "Create Gmail draft" }));
    await screen.findByText("Not sent");
    expect(api.createOutreachDraft).toHaveBeenCalledWith("per_1", {
      sessionId: "sess_new",
      subject: "Thursday?",
      body: "Hi Ada",
    });
  });

  it("refuses a chat bound to someone else and drafts nothing", async () => {
    api.openPersonSourcingChat.mockResolvedValue({
      created: false,
      session: { id: "sess_other", title: null, n_msgs: 2 },
      active_person: { person_id: "per_2", version: 1, label: "Someone else" },
    });
    const onChatOpened = vi.fn();
    panel({ sessionId: null, personVersion: 3, onChatOpened });
    fireEvent.click(screen.getByRole("button", { name: "Draft email" }));

    expect((await screen.findByRole("alert")).textContent).toContain("different person");
    expect(screen.queryByLabelText("Subject")).toBeNull();
    expect(onChatOpened).not.toHaveBeenCalled();
    expect(api.createOutreachDraft).not.toHaveBeenCalled();
  });

  it("refuses to draft to a person with no email", () => {
    panel({ recipient: null });

    expect(screen.getByText("No email address yet")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Draft email" })).toBeNull();
    expect(screen.queryByLabelText("Subject")).toBeNull();
    expect(api.createOutreachDraft).not.toHaveBeenCalled();
  });

  it("shows the recipient from the person file, not a field to type into", async () => {
    panel();

    expect(screen.getByText("ada@analytic.example")).toBeTruthy();
    expect(screen.queryByLabelText("To")).toBeNull();
  });

  it("starts with one Draft email button that reveals the form with Subject focused", () => {
    panel();

    expect(screen.queryByLabelText("Subject")).toBeNull();
    expect(screen.queryByLabelText("Message")).toBeNull();
    expect(screen.queryByRole("button", { name: "Create Gmail draft" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Draft email" }));

    expect(screen.getByLabelText("Subject")).toHaveFocus();
    expect(screen.getByLabelText("Message")).toBeInTheDocument();
    expect(screen.getByText("ada@analytic.example")).toBeTruthy();
    expect(api.createOutreachDraft).not.toHaveBeenCalled();
  });

  it("collapses on Cancel only while both fields are empty", () => {
    panel();
    fireEvent.click(screen.getByRole("button", { name: "Draft email" }));

    fireEvent.change(screen.getByLabelText("Subject"), { target: { value: "Thursday?" } });
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    expect(screen.getByLabelText("Subject")).toHaveValue("Thursday?");

    fireEvent.change(screen.getByLabelText("Subject"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByLabelText("Subject")).toBeNull();
    expect(screen.getByRole("button", { name: "Draft email" })).toHaveFocus();
    expect(api.createOutreachDraft).not.toHaveBeenCalled();
  });

  it("creates a draft and shows it as not sent", async () => {
    await composeAndDraft();

    expect(api.createOutreachDraft).toHaveBeenCalledWith("per_1", {
      sessionId: "sess_1",
      subject: "Thursday?",
      body: "Hi Ada,\n\nWould Thursday work?",
    });
    expect(screen.getByText("Not sent")).toBeTruthy();
    expect(screen.getByText("Would Thursday work?", { exact: false })).toBeTruthy();
    expect(screen.getByText("abc123def456")).toBeTruthy();
    expect(api.decideApproval).not.toHaveBeenCalled();
  });

  it("binds the approval to the body version on screen", async () => {
    await composeAndDraft();

    fireEvent.click(
      screen.getByRole("button", { name: "Request approval to send" }),
    );

    await waitFor(() =>
      expect(api.requestSendApproval).toHaveBeenCalledWith("per_1", {
        sessionId: "sess_1",
        draftId: "draft_1",
        reviewedBodyDigest: "abc123def456789",
      }),
    );
    expect(api.decideApproval).not.toHaveBeenCalled();
  });

  it("re-reads an edit made in Gmail and warns that the approval is stale", async () => {
    await composeAndDraft();
    fireEvent.click(
      screen.getByRole("button", { name: "Request approval to send" }),
    );
    await screen.findByRole("button", { name: "Allow once and send" });
    api.readOutreachDraft.mockResolvedValue({
      ...DRAFT,
      body: "Hi Ada,\n\nWould Friday work?",
      body_digest: "999999999999zzz",
    });

    fireEvent.click(
      screen.getByRole("button", { name: "Re-read draft from Gmail" }),
    );

    await screen.findByText("Would Friday work?", { exact: false });
    expect(screen.getByRole("alert").textContent).toContain(
      "allowing now sends nothing",
    );
    expect(api.decideApproval).not.toHaveBeenCalled();
  });

  it("reports the Gmail message and thread after an approved send", async () => {
    await composeAndDraft();
    fireEvent.click(
      screen.getByRole("button", { name: "Request approval to send" }),
    );
    await screen.findByRole("button", { name: "Allow once and send" });

    fireEvent.click(screen.getByRole("button", { name: "Allow once and send" }));

    const card = (await screen.findByText("Sent")).closest(".person-outreach-sent") as HTMLElement;
    expect(api.decideApproval).toHaveBeenCalledWith("send_1", "allow");
    expect(api.decideApproval).toHaveBeenCalledTimes(1);
    // Recipient and the reviewed subject sit in one aligned grid.
    expect(within(card).getByText("ada@analytic.example")).toBeInTheDocument();
    expect(within(card).getByText("Thursday?")).toBeInTheDocument();
    // Raw Gmail ids stay off screen; the Open in Gmail link carries them.
    expect(screen.queryByText("msg_1")).toBeNull();
    const gmail = screen.getByRole("link", { name: "Open in Gmail" });
    expect(gmail).toHaveAttribute("href", "https://mail.google.com/mail/u/0/#all/thread_1");
    expect(gmail).toHaveAttribute("title", expect.stringContaining("msg_1"));
    expect(gmail).toHaveAttribute("title", expect.stringContaining("thread_1"));
  });

  it("reports a refused send without claiming anything went out", async () => {
    api.decideApproval.mockResolvedValue({
      ok: false,
      result: { error: "The draft body changed after this send was approved." },
    });
    await composeAndDraft();
    fireEvent.click(
      screen.getByRole("button", { name: "Request approval to send" }),
    );
    await screen.findByRole("button", { name: "Allow once and send" });

    fireEvent.click(screen.getByRole("button", { name: "Allow once and send" }));

    await screen.findByText(/The draft body changed/);
    expect(screen.queryByText("Sent")).toBeNull();
  });

  it("denies without sending", async () => {
    await composeAndDraft();
    fireEvent.click(
      screen.getByRole("button", { name: "Request approval to send" }),
    );
    await screen.findByRole("button", { name: "Deny" });

    fireEvent.click(screen.getByRole("button", { name: "Deny" }));

    await waitFor(() =>
      expect(api.decideApproval).toHaveBeenCalledWith("send_1", "deny"),
    );
    expect(api.decideApproval).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("Sent")).toBeNull();
    expect(screen.getByText("Not sent")).toBeTruthy();
  });
});
