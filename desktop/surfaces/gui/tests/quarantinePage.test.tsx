import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { QuarantinePage } from "../src/routes/QuarantinePage";
import { parseHash } from "../src/app/route";

const api = vi.hoisted(() => ({
  getQuarantinedEffects: vi.fn(),
  settleQuarantinedEffect: vi.fn(),
}));

vi.mock("../src/api", () => ({
  getQuarantinedEffects: api.getQuarantinedEffects,
  settleQuarantinedEffect: api.settleQuarantinedEffect,
}));

const HELD_SEND = {
  effectId: "effect-1",
  runId: "run-1",
  toolName: "gmail_send",
  approvalId: "send_1",
  dispatchedAt: "2026-08-27T09:12:00+00:00",
  reason: "the owner process died mid-send",
  status: "ambiguous",
  inboxClaim: "interrupted",
  supersedesInbox: true,
  needsAPerson: true,
  sessionId: "sess-1",
  personId: "person-1",
  approval: {
    id: "send_1",
    name: "gmail_send",
    requestedAt: "2026-08-27T09:11:00+00:00",
    resource: {
      kind: "gmail_send_authority",
      to: "ada@analytic.example",
      subject: "Thursday?",
      account: "director@sourcecado.test",
    },
  },
};

describe("the external-effect review queue", () => {
  beforeEach(() => {
    api.getQuarantinedEffects.mockReset();
    api.settleQuarantinedEffect.mockReset();
  });

  it("says the outcome is unknown rather than calling it a failure", async () => {
    api.getQuarantinedEffects.mockResolvedValue([HELD_SEND]);

    render(<QuarantinePage />);

    await screen.findByText(/Send email/);
    const page = screen.getByRole("main");
    expect(page.textContent).toMatch(/never found out whether it finished/);
    // Never "failed": that is a claim about the world nobody is entitled to.
    expect(page.textContent).not.toMatch(/\bfailed\b/i);
    // And no control offers to run it again. Saying "nothing will retry it"
    // is the promise; a retry button would break it.
    for (const button of screen.getAllByRole("button")) {
      expect(button.textContent).not.toMatch(/retry|send again|try again/i);
    }
  });

  it("shows what was authorized and never the message body", async () => {
    api.getQuarantinedEffects.mockResolvedValue([
      {
        ...HELD_SEND,
        approval: {
          ...HELD_SEND.approval,
          resource: {
            ...HELD_SEND.approval.resource,
            body: "Hi Ada, would Thursday work?",
          },
        },
      },
    ]);

    render(<QuarantinePage />);

    await screen.findByText("ada@analytic.example");
    expect(screen.getByText("Thursday?")).toBeTruthy();
    expect(screen.getByText("director@sourcecado.test")).toBeTruthy();
    // The binding the director read is enough to settle it. The body is not
    // needed and never leaves the transcript.
    expect(screen.getByRole("main").textContent).not.toMatch(/would Thursday work/);
  });

  it("names the three answers a person may give and no machine outcome", async () => {
    api.getQuarantinedEffects.mockResolvedValue([HELD_SEND]);

    render(<QuarantinePage />);

    await screen.findByRole("button", { name: "It happened" });
    expect(screen.getByRole("button", { name: "It did not happen" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Stop tracking it" })).toBeTruthy();
    // "succeeded" and "failed" belong to the process that watched the call.
    expect(screen.queryByRole("button", { name: /succeeded/i })).toBeNull();
  });

  it("tells the operator that the approval record does not settle this", async () => {
    api.getQuarantinedEffects.mockResolvedValue([HELD_SEND]);

    render(<QuarantinePage />);

    const contested = await screen.findByText(/describes the request, not the action/);
    expect(contested.textContent).toMatch(/interrupted/);
  });

  it("does not show that line when the two records agree", async () => {
    api.getQuarantinedEffects.mockResolvedValue([
      { ...HELD_SEND, supersedesInbox: false, inboxClaim: null },
    ]);

    render(<QuarantinePage />);

    await screen.findByText(/Send email/);
    expect(screen.queryByText(/describes the request, not the action/)).toBeNull();
  });

  it("settles one effect with a named person and reloads the queue", async () => {
    api.getQuarantinedEffects
      .mockResolvedValueOnce([HELD_SEND])
      .mockResolvedValueOnce([]);
    api.settleQuarantinedEffect.mockResolvedValue(undefined);

    render(<QuarantinePage />);

    await screen.findByRole("button", { name: "It happened" });
    fireEvent.change(screen.getByLabelText("Your name"), {
      target: { value: "Fisher" },
    });
    fireEvent.change(screen.getByLabelText("What did you find? (optional)"), {
      target: { value: "Checked the Sent folder" },
    });
    fireEvent.click(screen.getByRole("button", { name: "It happened" }));

    await waitFor(() =>
      expect(api.settleQuarantinedEffect).toHaveBeenCalledWith(
        "effect-1",
        "resolved_succeeded",
        "Fisher",
        "Checked the Sent folder",
      ),
    );
    await screen.findByText(/Nothing is waiting/);
  });

  it("refuses to settle an effect without saying who decided", async () => {
    api.getQuarantinedEffects.mockResolvedValue([HELD_SEND]);

    render(<QuarantinePage />);

    fireEvent.click(await screen.findByRole("button", { name: "It happened" }));

    // The store refuses an unnamed operator decision. The surface says so
    // before the request rather than surfacing a raw server error.
    await screen.findByRole("alert");
    expect(screen.getByRole("alert").textContent).toMatch(/Enter your name/);
    expect(api.settleQuarantinedEffect).not.toHaveBeenCalled();
  });

  it("keeps the row when settling fails, so nothing looks resolved", async () => {
    api.getQuarantinedEffects.mockResolvedValue([HELD_SEND]);
    api.settleQuarantinedEffect.mockRejectedValue(new Error("effect is settled"));

    render(<QuarantinePage />);

    await screen.findByRole("button", { name: "It happened" });
    fireEvent.change(screen.getByLabelText("Your name"), {
      target: { value: "Fisher" },
    });
    fireEvent.click(screen.getByRole("button", { name: "It happened" }));

    await screen.findByRole("alert");
    expect(screen.getByRole("alert").textContent).toMatch(/effect is settled/);
    expect(screen.getByText(/Send email/)).toBeTruthy();
  });

  it("says the queue is empty without implying anything is wrong", async () => {
    api.getQuarantinedEffects.mockResolvedValue([]);

    render(<QuarantinePage />);

    await screen.findByText(/Every action Sourcecado started has a recorded outcome/);
    expect(screen.queryByLabelText("Your name")).toBeNull();
  });

  it("is reachable at its own route", () => {
    expect(parseHash("#/quarantine")).toEqual({ kind: "quarantine" });
  });
});
