import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BoardView } from "../src/Board";
import { PersonFileView } from "../src/PersonFile";

const api = vi.hoisted(() => ({
  getBoard: vi.fn(),
  getBoardRecord: vi.fn(),
  getBoardRecords: vi.fn(),
  getPerson: vi.fn(),
  revertBoardRecord: vi.fn(),
  setPersonSequence: vi.fn(),
}));

vi.mock("../src/api", () => ({
  getBoard: api.getBoard,
  getBoardRecord: api.getBoardRecord,
  getBoardRecords: api.getBoardRecords,
  getPerson: api.getPerson,
  revertBoardRecord: api.revertBoardRecord,
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
    api.getPerson.mockResolvedValue({
      person: { person_id: "person one", sequence_state: "open" },
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
    });
    api.setPersonSequence.mockResolvedValue({
      person: { person_id: "person one", sequence_state: "in_conversation" },
    });
    api.getBoardRecords.mockResolvedValue({
      records: [
        {
          id: "contact_ada",
          type: "contact",
          fields: { name: "Ada Lovelace", title: "Founder" },
          source_refs: ["source_web"],
          version: 2,
          restricted: false,
          created_at: "2026-08-26T10:00:00Z",
          updated_at: "2026-08-26T11:00:00Z",
        },
      ],
      count: 1,
    });
    api.getBoardRecord.mockResolvedValue({
      record: {
        id: "contact_ada",
        type: "contact",
        fields: { name: "Ada Lovelace", title: "Founder" },
        source_refs: ["source_web"],
        version: 2,
        restricted: false,
        created_at: "2026-08-26T10:00:00Z",
        updated_at: "2026-08-26T11:00:00Z",
      },
      links: [],
      receipts: [
        { id: "r1", operation: "create", after: { version: 1 }, created_at: "1" },
        { id: "r2", operation: "patch", after: { version: 2 }, created_at: "2" },
      ],
    });
    api.revertBoardRecord.mockResolvedValue({
      record: { id: "contact_ada", version: 3, fields: { name: "Ada Lovelace" } },
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

  it("shows sourcing-index records with inspectable and revertible version history", async () => {
    render(<BoardView />);

    expect(await screen.findByRole("heading", { name: "Sourcing index" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Inspect Ada Lovelace" }));

    expect(await screen.findByRole("heading", { name: "Version history" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Revert to version 1" }));
    await waitFor(() =>
      expect(api.revertBoardRecord).toHaveBeenCalledWith("contact_ada", {
        toVersion: 1,
        expectedVersion: 2,
        rationaleSummary: "Restore version 1 from Board history.",
      }),
    );
  });

  it("refreshes both Board stores after an agent Board write", async () => {
    render(<BoardView />);
    await screen.findByText("Ada Lovelace");

    window.dispatchEvent(new CustomEvent("sourcecado:board-changed"));

    await waitFor(() => expect(api.getBoard).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(api.getBoardRecords).toHaveBeenCalledTimes(2));
  });
});
