import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ChatPage } from "../src/routes/ChatPage";
import { styles } from "./cssBundle";

const api = vi.hoisted(() => ({
  getHealth: vi.fn(),
  getSessions: vi.fn(),
  getPersona: vi.fn(),
  getSchedule: vi.fn(),
  getGmail: vi.fn(),
  getConnectors: vi.fn(),
  getSettings: vi.fn(),
  getInbox: vi.fn(),
  getSession: vi.fn(),
  getCurrentRunTelemetry: vi.fn(),
  openChat: vi.fn(),
}));
const writeText = vi.fn();
const chatSend = vi.fn();

vi.mock("../src/api", () => ({
  connectCalendar: vi.fn(),
  connectDrive: vi.fn(),
  connectGmail: vi.fn(),
  connectGranola: vi.fn(),
  disconnectGmail: vi.fn(),
  getHealth: api.getHealth,
  getSessions: api.getSessions,
  getPersona: api.getPersona,
  getSchedule: api.getSchedule,
  getGmail: api.getGmail,
  getConnectors: api.getConnectors,
  getSettings: api.getSettings,
  getInbox: api.getInbox,
  getSession: api.getSession,
  getCurrentRunTelemetry: api.getCurrentRunTelemetry,
  hasToken: () => true,
  openChat: api.openChat,
  resolveInbox: vi.fn(),
  runScheduleJob: vi.fn(),
  setPersona: vi.fn(),
}));

function envelope(turn: number) {
  return {
    version: 2,
    session_id: "thread-alpha",
    run_id: `run-${turn}`,
    message_id: `message-${turn}`,
    part_id: `part-${turn}`,
  } as const;
}

const failure = {
  class: "unknown",
  connector_id: null,
  source: "Board",
  retry_safe: false,
  idempotent: false,
  summary: "A board change failed.",
  repair_route: null,
  detail: "PRIVATE_RAW_DETAIL",
  call_id: "call-1",
  run_id: "run-1",
  session_id: "thread-alpha",
  state: "failed",
} as const;

/** One operator message and the finished answer to it. */
function exchange(
  turn: number,
  prompt: string,
  answer: string,
  middle: Record<string, unknown>[] = [],
) {
  const events = [
    { type: "turn_start", state: "running" },
    ...middle,
    { type: "assistant_delta", delta: answer },
    { type: "turn_end", state: "complete", text: answer },
  ].map((event, index) => ({
    ...envelope(turn),
    event_id: `restored-${turn}-${index}`,
    ...event,
  }));
  return {
    messages: [
      { role: "user", content: prompt },
      { role: "assistant", content: answer },
    ],
    events,
  };
}

function open(...exchanges: ReturnType<typeof exchange>[]) {
  api.getSession.mockResolvedValue({
    id: "thread-alpha",
    title: "Message actions",
    queue: [],
    queue_paused: false,
    messages: exchanges.flatMap((item) => item.messages),
    events: exchanges.flatMap((item) => item.events),
  });
  return render(<ChatPage sessionId="thread-alpha" />);
}

const read = (id: string, name: string, result: Record<string, unknown>) => [
  { type: "tool_started", id, name, arguments: {} },
  { type: "tool_finished", id, name, ok: true, result },
];

const approvedDraft = [
  {
    type: "permission_required",
    id: "approval-draft",
    name: "gmail_draft",
    arguments: { to: "alyssa@example.com", subject: "Hello", body: "Body" },
    reason: "Creating a Gmail draft changes an external account.",
    requested_at: "2026-08-25T12:00:00.000Z",
    scope: "once",
  },
  {
    type: "tool_started",
    id: "approval-draft",
    name: "gmail_draft",
    arguments: { to: "alyssa@example.com", subject: "Hello", body: "Body" },
  },
  {
    type: "tool_finished",
    id: "approval-draft",
    name: "gmail_draft",
    ok: true,
    result: { draft_id: "draft-1" },
  },
  {
    type: "approval_resolved",
    id: "approval-draft",
    name: "gmail_draft",
    resolution: "allowed",
    decision: "allow",
    actor: "Fisher",
    requested_at: "2026-08-25T12:00:00.000Z",
    resolved_at: "2026-08-25T12:00:03.000Z",
    scope: "once",
    execution_status: "succeeded",
    execution_error: null,
  },
];

// A failed step has its own "Retry failed step" button. This is the bar's.
const retryButton = () => screen.getByRole("button", { name: /^Retry(?! failed step)/ });
const composerBox = () =>
  screen.getByRole("textbox", { name: "Message Sourcecado" }) as HTMLTextAreaElement;

describe("Message actions", () => {
  beforeEach(() => {
    window.localStorage.clear();
    writeText.mockReset();
    chatSend.mockReset();
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: vi.fn(),
    });
    api.getHealth.mockResolvedValue({ status: "ok", piece: "desktop", slice: 1, model: "m" });
    api.getSessions.mockResolvedValue({ sessions: [], open_id: "thread-alpha" });
    api.getPersona.mockResolvedValue({ id: "sourcing", name: "Sourcing Operator", tools: [] });
    api.getSchedule.mockResolvedValue({ jobs: [], runs: [] });
    api.getGmail.mockResolvedValue({ connected: false, email: null });
    api.getConnectors.mockResolvedValue({ connectors: [] });
    api.getSettings.mockResolvedValue({
      persona: { id: "sourcing", name: "Sourcing Operator" },
      model: "m",
      gmail: { connected: false, email: null },
      apollo: { configured: false },
    });
    api.getInbox.mockResolvedValue({ items: [] });
    api.getCurrentRunTelemetry.mockResolvedValue({
      version: 1,
      session_id: "thread-alpha",
      current_run: null,
    });
    api.openChat.mockImplementation(() => ({
      send: chatSend,
      approve: vi.fn(),
      cancel: vi.fn(),
      queue: vi.fn(),
      recover: vi.fn(),
      close: vi.fn(),
    }));
  });

  describe("the bar under an answer", () => {
    it("offers Copy, Retry and thumbs as icon buttons, and no text link", async () => {
      const { container } = open(exchange(1, "Find ML founders", "Two founders found."));

      await screen.findByText("Two founders found.");
      const bar = screen.getByRole("group", { name: "Response actions" });
      const buttons = within(bar).getAllByRole("button");
      expect(buttons.map((button) => button.getAttribute("aria-label"))).toEqual([
        "Copy response",
        "Retry",
        "Good response",
        "Bad response",
      ]);
      for (const button of buttons) {
        // An icon, no words on the button, and the same name as a tooltip.
        expect(button.querySelector("svg")).not.toBeNull();
        expect(button.textContent).toBe("");
        expect(button).toHaveAttribute("data-tooltip", button.getAttribute("aria-label"));
      }
      expect(container.querySelector(".sourcecado-copy-action")).toBeNull();
    });

    it("copies the answer prose and none of the reasoning or tool detail", async () => {
      open(
        exchange(1, "Find ML founders", "Two founders found.", [
          { type: "reasoning_delta", step: 1, at: "2026-10-26T12:00:00.000Z", delta: "SECRET_REASONING" },
          ...read("call-1", "board_query", { people: [{ name: "PRIVATE_RESULT" }] }),
        ]),
      );

      fireEvent.click(await screen.findByRole("button", { name: "Copy response" }));
      expect(writeText).toHaveBeenCalledWith("Two founders found.");
    });

    it("says Copied after a copy and Copy failed when the clipboard refuses", async () => {
      open(exchange(1, "Find ML founders", "Two founders found."));
      const copy = await screen.findByRole("button", { name: "Copy response" });

      writeText.mockResolvedValueOnce(undefined);
      fireEvent.click(copy);
      await waitFor(() => expect(copy).toHaveAttribute("data-tooltip", "Copied"));
      expect(copy).toHaveAttribute("data-state", "copied");

      writeText.mockRejectedValueOnce(new Error("blocked"));
      fireEvent.click(copy);
      await waitFor(() => expect(copy).toHaveAttribute("data-tooltip", "Copy failed"));
      expect(copy).toHaveAttribute("data-state", "failed");
    });

    it("keeps Retry on the newest answer only", async () => {
      open(
        exchange(1, "First question", "First answer."),
        exchange(2, "Second question", "Second answer."),
      );

      await screen.findByText("Second answer.");
      expect(screen.getAllByRole("button", { name: "Copy response" })).toHaveLength(2);
      expect(screen.getAllByRole("button", { name: "Good response" })).toHaveLength(2);
      const retries = screen.getAllByRole("button", { name: /^Retry/ });
      expect(retries).toHaveLength(1);
      const lastBar = screen.getAllByRole("group", { name: "Response actions" })[1]!;
      expect(lastBar).toContainElement(retries[0]!);
    });
  });

  describe("Retry", () => {
    it("sends the earlier operator message again as a new turn", async () => {
      open(exchange(1, "Find ML founders", "Two founders found."));

      fireEvent.click(await screen.findByRole("button", { name: "Retry" }));

      await waitFor(() =>
        expect(chatSend).toHaveBeenCalledWith("Find ML founders", "thread-alpha"),
      );
      expect(chatSend).toHaveBeenCalledTimes(1);
      // The new turn is shown as the operator's own message.
      expect(screen.getAllByText("Find ML founders")).toHaveLength(2);
    });

    it("sends the message that this answer replied to, not the newest one", async () => {
      open(
        exchange(1, "First question", "First answer."),
        exchange(2, "Second question", "Second answer."),
      );

      await screen.findByText("Second answer.");
      fireEvent.click(retryButton());

      await waitFor(() =>
        expect(chatSend).toHaveBeenCalledWith("Second question", "thread-alpha"),
      );
    });

    it("leaves a draft the operator is writing alone", async () => {
      open(exchange(1, "Find ML founders", "Two founders found."));
      await screen.findByText("Two founders found.");
      fireEvent.change(composerBox(), { target: { value: "Half a thought" } });

      fireEvent.click(retryButton());

      await waitFor(() => expect(chatSend).toHaveBeenCalledTimes(1));
      expect(composerBox()).toHaveValue("Half a thought");
      // The saved copy that survives a reload stays too.
      expect(window.localStorage.getItem("sourcecado.chat.draft.v1:thread-alpha")).toBe(
        "Half a thought",
      );
    });

    it("stays on for a turn that only read data", async () => {
      open(
        exchange(1, "Find ML founders", "Two founders found.", [
          ...read("call-1", "board_query", { people: [] }),
        ]),
      );

      await screen.findByText("Two founders found.");
      expect(retryButton()).not.toHaveAttribute("aria-disabled");
    });

    it("is off, with the reason, after a turn that used an approval", async () => {
      open(exchange(1, "Draft a note to Alyssa", "Draft ready for review.", approvedDraft));

      await screen.findByText("Draft ready for review.");
      const retry = retryButton();
      expect(retry).toHaveAttribute("aria-disabled", "true");
      expect(retry).toHaveAccessibleName(
        "Retry unavailable. This turn used an approval. Retry could repeat the action.",
      );
      expect(retry).toHaveAttribute("data-tooltip", expect.stringContaining("used an approval"));

      fireEvent.click(retry);
      expect(chatSend).not.toHaveBeenCalled();
    });

    it("is off when a step has no recorded result", async () => {
      open(
        exchange(1, "Find ML founders", "Two founders found.", [
          { type: "tool_started", id: "call-1", name: "apollo_search_people", arguments: {} },
        ]),
      );

      await screen.findByText("Two founders found.");
      expect(retryButton()).toHaveAttribute("aria-disabled", "true");
      expect(retryButton()).toHaveAttribute(
        "data-tooltip",
        expect.stringContaining("no recorded result"),
      );
      fireEvent.click(retryButton());
      expect(chatSend).not.toHaveBeenCalled();
    });

    it("follows the backend's retry_safe flag for a failed step", async () => {
      const failed = (id: string, retrySafe: boolean) => [
        { type: "tool_started", id, name: "board_upsert", arguments: {} },
        {
          type: "tool_finished",
          id,
          name: "board_upsert",
          ok: false,
          result: { error: "PRIVATE_RAW_DETAIL" },
          failure: { ...failure, call_id: id, retry_safe: retrySafe, idempotent: retrySafe },
        },
      ];
      const first = open(
        exchange(1, "Update the board", "The board change failed.", failed("call-1", false)),
      );
      await screen.findByText("The board change failed.");
      expect(retryButton()).toHaveAttribute("aria-disabled", "true");
      expect(retryButton()).toHaveAttribute(
        "data-tooltip",
        expect.stringContaining("not safe to repeat"),
      );
      first.unmount();

      open(exchange(1, "Update the board", "The board change failed.", failed("call-2", true)));
      await screen.findByText("The board change failed.");
      expect(retryButton()).not.toHaveAttribute("aria-disabled");
    });
  });

  describe("Thumbs", () => {
    it("show a pressed state, switch between up and down, and send nothing", async () => {
      open(exchange(1, "Find ML founders", "Two founders found."));
      const up = await screen.findByRole("button", { name: "Good response" });
      const down = screen.getByRole("button", { name: "Bad response" });
      expect(up).toHaveAttribute("aria-pressed", "false");

      fireEvent.click(up);
      expect(up).toHaveAttribute("aria-pressed", "true");
      expect(down).toHaveAttribute("aria-pressed", "false");

      fireEvent.click(down);
      expect(up).toHaveAttribute("aria-pressed", "false");
      expect(down).toHaveAttribute("aria-pressed", "true");

      fireEvent.click(down);
      expect(down).toHaveAttribute("aria-pressed", "false");
      expect(chatSend).not.toHaveBeenCalled();
    });

    it("say they are not saved, because no endpoint stores feedback", async () => {
      open(exchange(1, "Find ML founders", "Two founders found."));
      const up = await screen.findByRole("button", { name: "Good response" });

      expect(up).toHaveAttribute("data-tooltip", "Good response");
      fireEvent.click(up);
      expect(up).toHaveAttribute("data-tooltip", "Marked good. Kept on this screen only.");
    });
  });

  describe("Edit", () => {
    it("is on the operator's message and not on the answer", async () => {
      open(exchange(1, "Find ML founders", "Two founders found."));

      await screen.findByText("Two founders found.");
      const userBar = screen.getByRole("group", { name: "Message actions" });
      expect(within(userBar).getByRole("button", { name: "Edit message" })).toBeInTheDocument();
      expect(screen.getAllByRole("button", { name: "Edit message" })).toHaveLength(1);
      const answerBar = screen.getByRole("group", { name: "Response actions" });
      expect(within(answerBar).queryByRole("button", { name: /Edit/ })).toBeNull();
    });

    it("puts the text back in the composer, focuses it, and does not send", async () => {
      open(exchange(1, "Find ML founders in Berkeley", "Two founders found."));

      fireEvent.click(await screen.findByRole("button", { name: "Edit message" }));

      await waitFor(() => expect(composerBox()).toHaveValue("Find ML founders in Berkeley"));
      await waitFor(() => expect(composerBox()).toHaveFocus());
      expect(composerBox().selectionStart).toBe("Find ML founders in Berkeley".length);
      expect(chatSend).not.toHaveBeenCalled();
      // The restored draft survives a reload like any other draft.
      expect(window.localStorage.getItem("sourcecado.chat.draft.v1:thread-alpha")).toBe(
        "Find ML founders in Berkeley",
      );
    });

    it("keeps attached context lines, so the edited message loses nothing", async () => {
      const text =
        "Draft a note\n\n[Attached by the director]\nPerson: person_id p-1. Read the person file with board_get.";
      open(exchange(1, text, "Draft ready."));

      fireEvent.click(await screen.findByRole("button", { name: "Edit message" }));

      await waitFor(() => expect(composerBox()).toHaveValue(text));
    });

    it("will not overwrite a different draft, and says why", async () => {
      open(exchange(1, "Find ML founders", "Two founders found."));
      await screen.findByText("Two founders found.");
      fireEvent.change(composerBox(), { target: { value: "Half a thought" } });

      const edit = screen.getByRole("button", { name: /^Edit message/ });
      expect(edit).toHaveAttribute("aria-disabled", "true");
      expect(edit).toHaveAccessibleName(
        "Edit message unavailable. Send or clear the message you are writing first.",
      );
      fireEvent.click(edit);
      expect(composerBox()).toHaveValue("Half a thought");
    });

    it("works again once the draft is cleared", async () => {
      open(exchange(1, "Find ML founders", "Two founders found."));
      await screen.findByText("Two founders found.");
      fireEvent.change(composerBox(), { target: { value: "Half a thought" } });
      fireEvent.change(composerBox(), { target: { value: "" } });

      fireEvent.click(screen.getByRole("button", { name: "Edit message" }));

      await waitFor(() => expect(composerBox()).toHaveValue("Find ML founders"));
    });
  });

  describe("reveal and size", () => {
    const rule = (selector: string) => {
      const match = styles.match(new RegExp(`${selector}\\s*\\{([^}]*)\\}`));
      expect(match, `${selector} rule`).not.toBeNull();
      return match![1]!;
    };

    it("hides by opacity only, so a keyboard user can still tab to the buttons", () => {
      const bar = rule("\\.sourcecado-message-actions");
      expect(bar).toMatch(/opacity:\s*0;/);
      expect(bar).not.toMatch(/display:\s*none|visibility:\s*hidden/);
    });

    it("shows on hover and on focus inside the message", () => {
      expect(styles).toMatch(
        /:is\(\.sourcecado-assistant-message, \.sourcecado-user-message\):is\(:hover, :focus-within\)\s*\.sourcecado-message-actions\s*\{[^}]*opacity:\s*1;/,
      );
    });

    it("stays shown where there is no hover and on narrow screens", () => {
      expect(styles).toMatch(
        /@media \(hover: none\), \(max-width: 767px\)\s*\{\s*\.sourcecado-message-actions\s*\{[^}]*opacity:\s*1;/,
      );
    });

    it("shows the tooltip on hover and keyboard focus, at 13px or larger", () => {
      expect(styles).toMatch(
        /\.sourcecado-message-action:is\(:hover, :focus-visible\)::after\s*\{[^}]*opacity:\s*1;/,
      );
      const tooltip = rule("\\.sourcecado-message-action::after");
      expect(Number(tooltip.match(/font-size:\s*(\d+)px/)?.[1])).toBeGreaterThanOrEqual(13);
    });

    it("sets no text under 13px in the action rules", () => {
      const start = styles.indexOf(".sourcecado-message-actions {");
      const end = styles.indexOf(".sourcecado-notice {");
      const region = styles.slice(start, end);
      const sizes = Array.from(region.matchAll(/font-size:\s*(\d+)px/g)).map((match) =>
        Number(match[1]),
      );
      expect(sizes.length).toBeGreaterThan(0);
      expect(Math.min(...sizes)).toBeGreaterThanOrEqual(13);
    });
  });
});
