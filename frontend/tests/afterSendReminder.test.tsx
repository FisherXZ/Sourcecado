import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OutreachPanel } from "../src/OutreachPanel";

const api = vi.hoisted(() => ({
  createOutreachDraft: vi.fn(), readOutreachDraft: vi.fn(), requestSendApproval: vi.fn(),
  decideApproval: vi.fn(), getAfterSendReminder: vi.fn(), saveAfterSendReminder: vi.fn(),
}));
vi.mock("../src/api", () => api);

const DRAFT = { id: "draft_1", to: "ada@example.test", subject: "Hello", body: "Hi Ada",
  body_digest: "digest_1", account: "director@example.test", sent: false };
const CONTEXT = { timezone: "Pacific/Kiritimati", operation_id: "reminder_message_1", title: "Follow up with Ada", task: null };
const TASK = { task_id: "task_saved", title: "Check the proposal", due_date: "2026-11-06", due_timezone: CONTEXT.timezone, state: "open" };

async function draftAndReview() {
  render(<OutreachPanel personId="person_1" sessionId="session_1" recipient="ada@example.test" />);
  fireEvent.click(screen.getByRole("button", { name: "Draft email" }));
  fireEvent.change(screen.getByLabelText("Subject"), { target: { value: "Hello" } });
  fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Hi Ada" } });
  fireEvent.click(screen.getByRole("button", { name: "Create Gmail draft" }));
  await screen.findByText("Not sent");
  fireEvent.click(screen.getByRole("button", { name: "Request approval to send" }));
  await screen.findByRole("button", { name: "Allow once and send" });
}

async function send() {
  await draftAndReview();
  fireEvent.click(screen.getByRole("button", { name: "Allow once and send" }));
  await screen.findByText("Sent", { exact: true });
}

async function chooseReminder() {
  await send();
  fireEvent.click(screen.getByRole("button", { name: "Set follow-up" }));
  await screen.findByLabelText("Follow-up date");
  fireEvent.change(screen.getByLabelText("Follow-up task"), { target: { value: TASK.title } });
  fireEvent.change(screen.getByLabelText("Follow-up date"), { target: { value: TASK.due_date } });
}

describe("optional reminder after a confirmed send", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    api.createOutreachDraft.mockResolvedValue(DRAFT);
    api.requestSendApproval.mockResolvedValue({ id: "approval_1", resource: { ...DRAFT, body_digest: DRAFT.body_digest } });
    api.decideApproval.mockResolvedValue({ ok: true, result: { sent: true, message_id: "message_1", thread_id: "thread_1" } });
    api.getAfterSendReminder.mockResolvedValue(CONTEXT);
    api.saveAfterSendReminder.mockResolvedValue({ task: TASK, replayed: false });
  });

  it("offers a reminder only after confirmation, with Skip that creates nothing", async () => {
    await draftAndReview();
    expect(screen.queryByRole("button", { name: "Set follow-up" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Allow once and send" }));
    fireEvent.click(await screen.findByRole("button", { name: "Skip follow-up" }));
    expect(screen.queryByRole("button", { name: "Set follow-up" })).toBeNull();
    expect(api.getAfterSendReminder).not.toHaveBeenCalled();
    expect(api.saveAfterSendReminder).not.toHaveBeenCalled();
    expect(api.decideApproval).toHaveBeenCalledTimes(1);
    expect(screen.getByText("No follow-up added.")).toHaveFocus();
  });

  it("waits for a chosen date, saves one task in the saved timezone and does not resend", async () => {
    await send();
    fireEvent.click(screen.getByRole("button", { name: "Set follow-up" }));
    const date = await screen.findByLabelText("Follow-up date");
    expect(date).toHaveValue("");
    expect(screen.getByText(/Pacific\/Kiritimati/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Save follow-up" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Follow-up task"), { target: { value: TASK.title } });
    fireEvent.change(date, { target: { value: TASK.due_date } });
    fireEvent.click(screen.getByRole("button", { name: "Save follow-up" }));
    await screen.findByText(/Follow-up saved/);
    expect(api.getAfterSendReminder).toHaveBeenCalledWith("person_1", "message_1");
    expect(api.saveAfterSendReminder).toHaveBeenCalledWith("person_1", "message_1", {
      title: TASK.title, dueDate: TASK.due_date, operationId: CONTEXT.operation_id, timezone: CONTEXT.timezone,
    });
    expect(screen.getByRole("link", { name: "View person’s tasks" })).toHaveAttribute("href", "#/people/person_1");
    expect(api.decideApproval).toHaveBeenCalledTimes(1);
    expect(api.createOutreachDraft).toHaveBeenCalledTimes(1);
  });

  it("keeps the chosen date, text and operation when a save fails, then retries only the task", async () => {
    api.saveAfterSendReminder.mockRejectedValueOnce(new Error("Local save failed."));
    await chooseReminder();
    fireEvent.click(screen.getByRole("button", { name: "Save follow-up" }));
    await screen.findByText("Local save failed.");
    expect(screen.getByLabelText("Follow-up date")).toHaveValue(TASK.due_date);
    expect(screen.getByLabelText("Follow-up task")).toHaveValue(TASK.title);
    fireEvent.click(screen.getByRole("button", { name: "Retry saving follow-up" }));
    await screen.findByText(/Follow-up saved/);
    expect(api.saveAfterSendReminder.mock.calls[0]).toEqual(api.saveAfterSendReminder.mock.calls[1]);
    expect(api.decideApproval).toHaveBeenCalledTimes(1);
  });

  it("locks the form during a save so newer typing cannot be dropped", async () => {
    let finish!: (value: unknown) => void;
    api.saveAfterSendReminder.mockReturnValue(new Promise(resolve => { finish = resolve; }));
    await chooseReminder();
    fireEvent.click(screen.getByRole("button", { name: "Save follow-up" }));
    expect(screen.getByLabelText("Follow-up task")).toBeDisabled();
    expect(screen.getByLabelText("Follow-up date")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Saving follow-up…" })).toBeDisabled();
    await act(async () => finish({ task: TASK }));
    await screen.findByText(/Follow-up saved/);
  });

  it("shows an already saved reminder after reopening without creating a second task", async () => {
    api.getAfterSendReminder.mockResolvedValue({ ...CONTEXT, task: TASK });
    await send();
    fireEvent.click(screen.getByRole("button", { name: "Set follow-up" }));
    await screen.findByText(/Follow-up saved/);
    expect(api.saveAfterSendReminder).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Save follow-up" })).toBeNull();
  });

  it.each(["failed", "unknown"])("does not offer a reminder after a %s send", async (status) => {
    api.decideApproval.mockResolvedValue({ ok: false, result: { error: `${status} outcome` } });
    await draftAndReview();
    fireEvent.click(screen.getByRole("button", { name: "Allow once and send" }));
    await screen.findByText(`${status} outcome`);
    expect(screen.queryByRole("button", { name: "Set follow-up" })).toBeNull();
    expect(screen.queryByText("Sent", { exact: true })).toBeNull();
    expect(api.saveAfterSendReminder).not.toHaveBeenCalled();
  });
});
