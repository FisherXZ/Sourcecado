import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { FollowUpPreparation, OutreachDraft } from "../src/api";

const api = vi.hoisted(() => ({
  prepareFollowUp: vi.fn(),
  createFollowUpDraft: vi.fn(),
  editOutreachDraft: vi.fn(),
  readOutreachDraft: vi.fn(),
  requestSendApproval: vi.fn(),
  decideApproval: vi.fn(),
  openPersonSourcingChat: vi.fn(),
}));

vi.mock("../src/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/api")>()),
  ...api,
}));

const { FollowUpError } = await import("../src/api");
const { FollowUpPanel } = await import("../src/FollowUpPanel");
const { readDraft } = await import("../src/chat/draftStorage");
const { readFollowUpText } = await import("../src/followUpDrafts");

const THREAD = {
  thread_id: "thread_1",
  subject: "Re: Thursday?",
  message_count: 2,
  last_direction: "inbound" as const,
  last_at: null,
  url: "https://mail.google.com/mail/u/0/#all/thread_1",
};

function preparation(overrides: Partial<FollowUpPreparation> = {}): FollowUpPreparation {
  return {
    task: {
      task_id: "task_1",
      title: "Send Ada the deck",
      details: "",
      due_date: "2026-10-28",
      state: "open",
      version: 1,
    },
    person: {
      person_id: "per_1",
      label: "Ada Lovelace",
      email: "ada@analytic.example",
      sequence_state: "in_conversation",
    },
    account: "director@sourcecado.test",
    recipient: "ada@analytic.example",
    thread: THREAD,
    threads: [THREAD],
    subject: "Re: Thursday?",
    body: "Hi Ada,\n\n",
    talking_points: [{ origin: "task", text: "Send Ada the deck" }],
    sources: [{ id: "s1", provider: "Gmail", title: null }],
    restricted_source_count: 0,
    blockers: [],
    assistant_prompt: "Write a follow-up email to Ada Lovelace for this task: Send Ada the deck.",
    ...overrides,
  };
}

function draft(body: string, digest: string): OutreachDraft {
  return {
    id: "draft_1",
    to: "ada@analytic.example",
    subject: "Re: Thursday?",
    body,
    body_digest: digest,
    account: "director@sourcecado.test",
    thread_id: "thread_1",
    sent: false,
  };
}

function panel(props: Partial<Parameters<typeof FollowUpPanel>[0]> = {}) {
  const onClose = vi.fn();
  render(
    <FollowUpPanel
      personId="per_1"
      taskId="task_1"
      personVersion={3}
      sessionId="sess_1"
      onClose={onClose}
      {...props}
    />,
  );
  return { onClose };
}

describe("Follow-up panel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    window.location.hash = "";
    api.prepareFollowUp.mockResolvedValue({
      follow_up: preparation(),
      sourcing_chat: { session_id: "sess_1", person_id: "per_1" },
    });
    api.createFollowUpDraft.mockImplementation(async (_id, input) => draft(input.body, "digest_a"));
    api.editOutreachDraft.mockImplementation(async (_id, _draftId, input) =>
      draft(input.body, "digest_b"),
    );
    api.requestSendApproval.mockImplementation(async (_id, input) => ({
      id: "send_1",
      name: "gmail_send",
      state: "pending",
      resource: {
        kind: "gmail_send_authority",
        person_id: "per_1",
        draft_id: input.draftId,
        account: "director@sourcecado.test",
        to: "ada@analytic.example",
        subject: "Re: Thursday?",
        body_digest: input.reviewedBodyDigest,
        thread_id: input.expectedThreadId,
        task_id: input.taskId,
      },
    }));
    api.decideApproval.mockResolvedValue({
      ok: true,
      result: { sent: true, to: "ada@analytic.example", thread_id: "thread_1" },
    });
  });

  it("shows the task, sender, recipient and conversation before any draft exists", async () => {
    panel();

    expect(await screen.findByText("director@sourcecado.test")).toBeTruthy();
    expect(screen.getByText("ada@analytic.example")).toBeTruthy();
    expect(screen.getByText(/Re: Thursday\? · 2 messages, last message theirs/)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Follow-up: Send Ada the deck" })).toBeTruthy();
    expect((screen.getByLabelText("Subject") as HTMLInputElement).readOnly).toBe(true);
    expect((screen.getByLabelText("Message") as HTMLTextAreaElement).value).toBe("Hi Ada,\n\n");
    expect(api.createFollowUpDraft).not.toHaveBeenCalled();
  });

  it("creates the reply in the conversation, edits it in place, and sends only the reviewed text", async () => {
    panel();
    fireEvent.change(await screen.findByLabelText("Message"), {
      target: { value: "Hi Ada,\n\nThe deck is attached." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create reply draft in Gmail" }));

    await screen.findByText("Not sent · Gmail draft");
    expect(api.createFollowUpDraft).toHaveBeenCalledWith("per_1", {
      sessionId: "sess_1",
      body: "Hi Ada,\n\nThe deck is attached.",
      threadId: "thread_1",
      taskId: "task_1",
    });

    fireEvent.click(screen.getByRole("button", { name: "Edit message" }));
    fireEvent.change(screen.getByLabelText("Message"), {
      target: { value: "Hi Ada,\n\nThe deck is attached, with notes." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save to Gmail draft" }));
    await waitFor(() =>
      expect(screen.getByLabelText("Exact message").textContent).toBe(
        "Hi Ada,\n\nThe deck is attached, with notes.",
      ),
    );
    expect(api.editOutreachDraft).toHaveBeenCalledWith("per_1", "draft_1", {
      sessionId: "sess_1",
      body: "Hi Ada,\n\nThe deck is attached, with notes.",
      expectedBodyDigest: "digest_a",
    });

    fireEvent.click(screen.getByRole("button", { name: "Request approval to send" }));
    await screen.findByRole("button", { name: "Allow once and send" });
    expect(api.requestSendApproval).toHaveBeenCalledWith("per_1", {
      sessionId: "sess_1",
      draftId: "draft_1",
      reviewedBodyDigest: "digest_b",
      expectedThreadId: "thread_1",
      taskId: "task_1",
    });

    fireEvent.click(screen.getByRole("button", { name: "Allow once and send" }));
    expect(await screen.findByText(/The task is still open/)).toBeTruthy();
    expect(api.decideApproval).toHaveBeenCalledWith("send_1", "allow");
    expect(readFollowUpText("per_1", "task_1")).toBeNull();
  });

  it("denies a pending approval before saving a changed message", async () => {
    panel();
    fireEvent.click(await screen.findByRole("button", { name: "Create reply draft in Gmail" }));
    fireEvent.click(await screen.findByRole("button", { name: "Request approval to send" }));
    await screen.findByRole("button", { name: "Allow once and send" });

    fireEvent.click(screen.getByRole("button", { name: "Edit message" }));
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Changed" } });
    fireEvent.click(screen.getByRole("button", { name: "Save to Gmail draft" }));

    await waitFor(() => expect(api.editOutreachDraft).toHaveBeenCalled());
    expect(api.decideApproval).toHaveBeenCalledWith("send_1", "deny");
    expect(screen.getByRole("button", { name: "Request approval to send" })).toBeTruthy();
  });

  it("lets the director write without an email address but not create a draft", async () => {
    api.prepareFollowUp.mockResolvedValue({
      follow_up: preparation({
        recipient: null,
        thread: null,
        threads: [],
        subject: "Send Ada the deck",
        blockers: [
          {
            code: "no_recipient",
            message: "No email address on file. You can write now, but sending waits until one is added.",
          },
        ],
      }),
      sourcing_chat: null,
    });
    panel({ sessionId: null });

    expect(await screen.findByText("No email on file")).toBeTruthy();
    expect(screen.getByText(/sending waits until one is added/)).toBeTruthy();
    const message = screen.getByLabelText("Message");
    fireEvent.change(message, { target: { value: "Hi Ada, here is the deck." } });
    expect(readFollowUpText("per_1", "task_1")?.body).toBe("Hi Ada, here is the deck.");
    const create = screen.getByRole("button", { name: "Create Gmail draft" }) as HTMLButtonElement;
    expect(create.disabled).toBe(true);
    expect(api.openPersonSourcingChat).not.toHaveBeenCalled();
  });

  it("keeps the text when Gmail would not keep the reply in the conversation", async () => {
    api.createFollowUpDraft.mockRejectedValue(
      new FollowUpError("not preserved", 409, "thread_not_preserved", null),
    );
    panel();
    fireEvent.change(await screen.findByLabelText("Message"), {
      target: { value: "Hi Ada, deck attached." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create reply draft in Gmail" }));

    expect(await screen.findByText(/Gmail put this reply in a different thread/)).toBeTruthy();
    expect((screen.getByLabelText("Message") as HTMLTextAreaElement).value).toBe(
      "Hi Ada, deck attached.",
    );
    expect(screen.queryByRole("button", { name: "Request approval to send" })).toBeNull();
  });

  it("shows Gmail's current text when an edit collides with a change made there", async () => {
    api.editOutreachDraft.mockRejectedValue(
      new FollowUpError("changed", 409, "stale_draft", draft("Edited in Gmail.", "digest_g")),
    );
    panel();
    fireEvent.click(await screen.findByRole("button", { name: "Create reply draft in Gmail" }));
    fireEvent.click(await screen.findByRole("button", { name: "Edit message" }));
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "My edit" } });
    fireEvent.click(screen.getByRole("button", { name: "Save to Gmail draft" }));

    expect(await screen.findByText(/The draft changed in Gmail/)).toBeTruthy();
    // The director's unsaved edit is still in the editor.
    expect((screen.getByLabelText("Message") as HTMLTextAreaElement).value).toBe("My edit");
    fireEvent.click(screen.getByRole("button", { name: "Discard edit" }));
    expect(screen.getByLabelText("Exact message").textContent).toBe("Edited in Gmail.");
  });

  it("asks the assistant through the person's chat without sending the request", async () => {
    api.openPersonSourcingChat.mockResolvedValue({
      created: false,
      session: { id: "sess_1", title: "Sourcing · Ada", n_msgs: 4 },
      active_person: { person_id: "per_1", version: 3, label: "Ada" },
    });
    panel();
    fireEvent.click(await screen.findByRole("button", { name: "Ask assistant to write" }));

    await waitFor(() => expect(window.location.hash).toBe("#/chat/sess_1/person/per_1"));
    expect(readDraft("sess_1")).toContain("Send Ada the deck");
    expect(api.createFollowUpDraft).not.toHaveBeenCalled();
  });

  it("explains a failed preparation and retries it", async () => {
    api.prepareFollowUp.mockRejectedValueOnce(new FollowUpError("Only an open task can be followed up.", 409, "task_not_open", null));
    const { onClose } = panel();

    expect(await screen.findByText(/Only an open task can be followed up/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByLabelText("Message")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Close follow-up" }));
    expect(onClose).toHaveBeenCalled();
  });
});
