import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BoardView } from "../src/Board";
import { PersonFileView } from "../src/PersonFile";

const api = vi.hoisted(() => ({
  getBoard: vi.fn(),
  getPerson: vi.fn(),
  setPersonSequence: vi.fn(),
}));

vi.mock("../src/api", () => ({
  getBoard: api.getBoard,
  getPerson: api.getPerson,
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
});
