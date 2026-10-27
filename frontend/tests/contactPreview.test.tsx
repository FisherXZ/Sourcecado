import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ContactPreview } from "../src/ContactPreview";

const api = vi.hoisted(() => {
  class StatusSaveError extends Error {
    status: number;
    code: string | null;
    current: unknown;
    constructor(message: string, status: number, code: string | null, current: unknown) {
      super(message);
      this.name = "StatusSaveError";
      this.status = status;
      this.code = code;
      this.current = current;
    }
  }
  return {
    getPerson: vi.fn(),
    setPersonSequence: vi.fn(),
    undoStatusChange: vi.fn(),
    StatusSaveError,
  };
});

const { StatusSaveError } = api;

vi.mock("../src/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/api")>()),
  ...(await import("./taskApi")).taskApiMock(),
  getPerson: api.getPerson,
  setPersonSequence: api.setPersonSequence,
  undoStatusChange: api.undoStatusChange,
  StatusSaveError: api.StatusSaveError,
}));

function personFile(overrides: Record<string, unknown> = {}) {
  return {
    person: {
      person_id: "person one",
      first_name: "Maya",
      last_name: "Lopez",
      title: "Engineer",
      company: "Acme",
      sequence_state: "open",
      version: 2,
      email: "maya@acme.com",
      phone: "+1 415 555 0132",
      ...overrides,
    },
    brief: {},
    descriptions: {
      general: { slot: "general", text: "Short note.", authority: "assistant", source_refs: [], version: 1 },
      detailed: { slot: "detailed", text: "Longer note.", authority: "assistant", source_refs: [], version: 1 },
    },
    timeline: [
      { event_id: "e1", source: "Apollo", kind: "search", summary: "Found in a search.", payload: {} },
      { event_id: "e2", source: "Gmail", kind: "mail", summary: "We wrote.", payload: {} },
    ],
    versions: [{ version: 2, created_at: "2026-10-01T10:00:00Z" }],
    sourcing_chat: null,
  };
}

describe("ContactPreview", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    api.getPerson.mockResolvedValue(personFile());
  });

  it("shows identity, copyable contact details and reserved non-spending slots", async () => {
    render(<ContactPreview personId="person one" onClose={() => {}} onBoardChanged={() => {}} />);

    expect(await screen.findByRole("heading", { name: "Maya Lopez" })).toBeInTheDocument();
    expect(screen.getByText("Engineer · Acme")).toBeInTheDocument();
    expect(screen.getByText("maya@acme.com")).toBeInTheDocument();
    // Reserved for #240/#243; present but inert, so nothing spends credits.
    expect(screen.getByRole("button", { name: "⬡ Find missing contact info" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "✎ Correct details" })).toBeDisabled();
    expect(api.setPersonSequence).not.toHaveBeenCalled();
  });

  it("omits the Contact section when no email or phone exist", async () => {
    api.getPerson.mockResolvedValue(personFile({ email: null, phone: null }));
    render(<ContactPreview personId="person one" onClose={() => {}} onBoardChanged={() => {}} />);
    await screen.findByRole("heading", { name: "Maya Lopez" });
    expect(screen.queryByRole("heading", { name: "Contact" })).not.toBeInTheDocument();
  });

  it("copies an available detail to the clipboard", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(<ContactPreview personId="person one" onClose={() => {}} onBoardChanged={() => {}} />);

    fireEvent.click(await screen.findByRole("button", { name: "Copy email" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("maya@acme.com"));
    expect(await screen.findByText("Copied")).toBeInTheDocument();
  });

  it("changes status through the pills and offers an undo", async () => {
    const onBoardChanged = vi.fn();
    api.setPersonSequence.mockResolvedValue({
      person: { ...personFile().person, sequence_state: "in_conversation", version: 3 },
      change: { change_id: "chg_abc", entity_type: "status", action: "update" },
      replayed: false,
    });
    api.undoStatusChange.mockResolvedValue({
      person: { ...personFile().person, sequence_state: "open", version: 4 },
      change: null,
      replayed: false,
    });
    render(<ContactPreview personId="person one" onClose={() => {}} onBoardChanged={onBoardChanged} />);

    fireEvent.click(await screen.findByRole("button", { name: "In conversation" }));

    await waitFor(() =>
      expect(api.setPersonSequence).toHaveBeenCalledWith("person one", "in_conversation", {
        expectedVersion: 2,
        operationId: expect.any(String),
      }),
    );
    expect(onBoardChanged).toHaveBeenCalled();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "In conversation" })).toHaveAttribute(
        "aria-pressed",
        "true",
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: "↩ Undo status change" }));
    await waitFor(() =>
      expect(api.undoStatusChange).toHaveBeenCalledWith("person one", "chg_abc", {
        operationId: expect.any(String),
      }),
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Open" })).toHaveAttribute("aria-pressed", "true"),
    );
    expect(
      screen.queryByRole("button", { name: "↩ Undo status change" }),
    ).not.toBeInTheDocument();
  });

  it("keeps the pills usable and shows the current status on a conflict", async () => {
    api.setPersonSequence.mockRejectedValue(
      new StatusSaveError("conflict", 409, "status_conflict", {
        ...personFile().person,
        sequence_state: "done",
        version: 5,
      }),
    );
    render(<ContactPreview personId="person one" onClose={() => {}} onBoardChanged={() => {}} />);

    fireEvent.click(await screen.findByRole("button", { name: "In conversation" }));

    expect(await screen.findByText(/current value/i)).toBeInTheDocument();
    // The control now reflects the server's current status and stays interactive.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Done" })).toHaveAttribute("aria-pressed", "true"),
    );
    expect(screen.getByRole("button", { name: "Open" })).not.toBeDisabled();
  });

  it("shows a loading state, then an error with retry that recovers", async () => {
    let reject: (reason?: unknown) => void = () => {};
    api.getPerson.mockReturnValueOnce(new Promise((_, r) => (reject = r)));
    render(<ContactPreview personId="person one" onClose={() => {}} onBoardChanged={() => {}} />);

    expect(screen.getByText("Loading preview…")).toBeInTheDocument();
    reject(new Error("boom"));

    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn’t load this preview.");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("heading", { name: "Maya Lopez" })).toBeInTheDocument();
  });

  it("reveals the detailed description only on request", async () => {
    render(<ContactPreview personId="person one" onClose={() => {}} onBoardChanged={() => {}} />);
    await screen.findByRole("heading", { name: "Maya Lopez" });

    expect(screen.queryByRole("textbox", { name: "Detailed description" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show detailed description" }));
    expect(screen.getByRole("textbox", { name: "Detailed description" })).toBeInTheDocument();
  });

  it("closes via Back to list", async () => {
    const onClose = vi.fn();
    render(<ContactPreview personId="person one" onClose={onClose} onBoardChanged={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "← Back to list" }));
    expect(onClose).toHaveBeenCalled();
  });
});
