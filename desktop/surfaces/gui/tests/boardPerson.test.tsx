import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BoardView } from "../src/Board";
import { PersonFileView } from "../src/PersonFile";

const api = vi.hoisted(() => ({
  getBoard: vi.fn(),
  getPerson: vi.fn(),
  openPersonSourcingChat: vi.fn(),
  revertPerson: vi.fn(),
  setPersonSequence: vi.fn(),
}));

vi.mock("../src/api", () => ({
  getBoard: api.getBoard,
  getPerson: api.getPerson,
  openPersonSourcingChat: api.openPersonSourcingChat,
  revertPerson: api.revertPerson,
  setPersonSequence: api.setPersonSequence,
}));

describe("Board and person-file routes", () => {
  beforeEach(() => {
    api.getBoard.mockResolvedValue({
      open: [
        {
          person_id: "person one",
          first_name: "Alyssa",
          last_name: "Lee",
          title: "Sourcing lead",
          company: "Codeology",
          sequence_state: "open",
        },
      ],
      in_conversation: [],
      done: [],
    });
    api.revertPerson.mockResolvedValue({
      person: { person_id: "person one", sequence_state: "open", version: 3, title: "Founder" },
    });
    api.getPerson.mockResolvedValue({
      person: { person_id: "person one", sequence_state: "open", version: 2 },
      brief: {
        who: "Alyssa Lee",
        why: "Strong sourcing fit.",
        learned: ["Led university sourcing."],
        missing: ["Timing"],
        sources: ["Apollo"],
      },
      timeline: [
        {
          event_id: "event-1",
          source: "Apollo",
          kind: "search",
          summary: "Found in a sourcing search.",
          payload: {},
        },
      ],
      versions: [
        { version: 1, created_at: "2026-08-26T10:00:00Z" },
        { version: 2, created_at: "2026-08-26T11:00:00Z" },
      ],
      sourcing_chat: null,
    });
    api.openPersonSourcingChat.mockResolvedValue({
      created: true,
      session: { id: "thread one", title: "Sourcing · Alyssa Lee", n_msgs: 0 },
      active_person: { person_id: "person one", version: 2, label: "Alyssa Lee" },
    });
    api.setPersonSequence.mockResolvedValue({
      person: { person_id: "person one", sequence_state: "in_conversation" },
    });
  });

  it("renders labeled Board buckets and safe person links", async () => {
    render(<BoardView />);

    expect(await screen.findByRole("heading", { level: 1, name: "Board" })).toBeInTheDocument();
    const open = screen.getByRole("region", { name: "Open" });
    expect(within(open).getByRole("link", { name: /Alyssa Lee/ })).toHaveAttribute(
      "href",
      "#/people/person%20one",
    );
    expect(screen.getByRole("region", { name: "In conversation" })).toHaveTextContent("None");
  });

  it("updates a person sequence through labeled pressed-state controls", async () => {
    const { container } = render(<PersonFileView personId="person one" />);

    expect(await screen.findByRole("heading", { level: 1, name: "Alyssa Lee" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "In conversation" }));

    await waitFor(() =>
      expect(api.setPersonSequence).toHaveBeenCalledWith(
        "person one",
        "in_conversation",
      ),
    );
    expect(screen.getByText("Found in a sourcing search.")).toBeInTheDocument();
    expect(container.querySelector(".tool-card")).not.toBeInTheDocument();
  });

  it("refreshes Board buckets after a person-file write", async () => {
    render(<BoardView />);
    await screen.findByRole("link", { name: /Alyssa Lee/ });
    window.dispatchEvent(new CustomEvent("sourcecado:board-changed"));
    await waitFor(() => expect(api.getBoard).toHaveBeenCalledTimes(2));
  });

  it("can inspect and revert a prior person-file version", async () => {
    render(<PersonFileView personId="person one" />);
    expect(await screen.findByRole("heading", { name: "Version history" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Revert to version 1" }));
    await waitFor(() =>
      expect(api.revertPerson).toHaveBeenCalledWith("person one", {
        toVersion: 1,
        expectedVersion: 2,
        rationaleSummary: "Restore version 1 from person history.",
      }),
    );
  });

  it("creates the person's sourcing chat and navigates with both identities", async () => {
    window.location.hash = "#/people/person%20one";
    render(<PersonFileView personId="person one" />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Create sourcing chat" }),
    );

    await waitFor(() =>
      expect(api.openPersonSourcingChat).toHaveBeenCalledWith("person one", 2),
    );
    await waitFor(() =>
      expect(window.location.hash).toBe(
        "#/chat/thread%20one/person/person%20one",
      ),
    );
  });

  it("offers the same single action as Open when the person is already bound", async () => {
    api.getPerson.mockResolvedValueOnce({
      ...(await api.getPerson()),
      sourcing_chat: { session_id: "thread one", person_id: "person one" },
    });
    render(<PersonFileView personId="person one" />);

    expect(
      await screen.findByRole("button", { name: "Open sourcing chat" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Create sourcing chat" }),
    ).not.toBeInTheDocument();
  });

  it("keeps a stale person-chat failure visible without navigating", async () => {
    window.location.hash = "#/people/person%20one";
    api.openPersonSourcingChat.mockRejectedValueOnce(new Error("stale version"));
    render(<PersonFileView personId="person one" />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Create sourcing chat" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Refresh the person file",
    );
    expect(window.location.hash).toBe("#/people/person%20one");
  });

  it("shows missing person recovery without offering a chat action", async () => {
    api.getPerson.mockRejectedValueOnce(new Error("not found"));
    render(<PersonFileView personId="missing-person" />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Couldn’t load this person file",
    );
    expect(
      screen.queryByRole("button", { name: /sourcing chat/i }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to Board" })).toHaveAttribute(
      "href",
      "#/board",
    );
  });
});
