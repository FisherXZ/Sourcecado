import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FindSavedPerson } from "../src/FindSavedPerson";

const api = vi.hoisted(() => ({
  searchSavedPeople: vi.fn(),
  setPersonSequence: vi.fn(),
  curateApolloCandidates: vi.fn(),
}));

vi.mock("../src/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/api")>()),
  searchSavedPeople: api.searchSavedPeople,
  setPersonSequence: api.setPersonSequence,
  curateApolloCandidates: api.curateApolloCandidates,
}));

function match(overrides: Record<string, unknown> = {}) {
  return {
    person_id: "person-1",
    first_name: "Maya",
    last_name: "Chen",
    last_name_status: "known",
    title: "Engineer",
    company: "Acme Labs",
    sequence_state: null,
    board_lane: "backlog",
    has_email: false,
    apollo_id: "a1",
    match_kind: "name",
    ...overrides,
  };
}

describe("Find saved person finder", () => {
  beforeEach(() => {
    api.searchSavedPeople.mockReset();
    api.setPersonSequence.mockReset();
    api.curateApolloCandidates.mockReset();
    window.location.hash = "#/board";
  });
  afterEach(() => vi.clearAllMocks());

  it("shows the idle prompt before any query", () => {
    render(<FindSavedPerson onClose={() => {}} />);
    expect(
      screen.getByText(/saved person, including people kept before outreach/i),
    ).toBeTruthy();
    expect(api.searchSavedPeople).not.toHaveBeenCalled();
  });

  it("searches on input and renders a result with sequence context", async () => {
    api.searchSavedPeople.mockResolvedValue({
      results: [match()],
      count: 1,
      query: "maya",
    });
    render(<FindSavedPerson onClose={() => {}} />);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "maya" } });

    await waitFor(() => expect(screen.getByText("Maya Chen")).toBeTruthy());
    expect(screen.getByText(/Saved — not in a sequence/)).toBeTruthy();
    expect(screen.getByText(/No email on file/)).toBeTruthy();
  });

  it("renders a distinct no-match state", async () => {
    api.searchSavedPeople.mockResolvedValue({ results: [], count: 0, query: "zzz" });
    render(<FindSavedPerson onClose={() => {}} />);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "zzz" } });
    await waitFor(() =>
      expect(screen.getByText(/No saved person matches/)).toBeTruthy(),
    );
  });

  it("renders an error state with Retry when the search fails", async () => {
    api.searchSavedPeople.mockRejectedValueOnce(new Error("boom"));
    render(<FindSavedPerson onClose={() => {}} />);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "maya" } });
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());

    // Retry re-issues the search (restart).
    api.searchSavedPeople.mockResolvedValueOnce({
      results: [match()],
      count: 1,
      query: "maya",
    });
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    await waitFor(() => expect(screen.getByText("Maya Chen")).toBeTruthy());
  });

  it("opens a result as pure navigation without starting a sequence", async () => {
    const onClose = vi.fn();
    api.searchSavedPeople.mockResolvedValue({
      results: [match()],
      count: 1,
      query: "maya",
    });
    render(<FindSavedPerson onClose={onClose} />);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "maya" } });
    await waitFor(() => expect(screen.getByText("Maya Chen")).toBeTruthy());

    fireEvent.click(screen.getByRole("option", { name: /Maya Chen/ }));

    expect(window.location.hash).toBe("#/people/person-1");
    expect(onClose).toHaveBeenCalled();
    // No mutation: opening never starts a sequence or keeps anyone.
    expect(api.setPersonSequence).not.toHaveBeenCalled();
    expect(api.curateApolloCandidates).not.toHaveBeenCalled();
  });

  it("opens the keyboard-selected result on Enter", async () => {
    const onClose = vi.fn();
    api.searchSavedPeople.mockResolvedValue({
      results: [match(), match({ person_id: "person-2", first_name: "Noor", last_name: "Ali" })],
      count: 2,
      query: "a",
    });
    render(<FindSavedPerson onClose={onClose} />);
    const input = screen.getByRole("combobox");
    fireEvent.change(input, { target: { value: "a" } });
    await waitFor(() => expect(screen.getByText("Noor Ali")).toBeTruthy());

    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(window.location.hash).toBe("#/people/person-2");
    expect(onClose).toHaveBeenCalled();
  });

  it("opens the Tab-focused result on Enter, not the highlighted first row", async () => {
    const onClose = vi.fn();
    api.searchSavedPeople.mockResolvedValue({
      results: [match(), match({ person_id: "person-2", first_name: "Noor", last_name: "Ali" })],
      count: 2,
      query: "a",
    });
    render(<FindSavedPerson onClose={onClose} />);
    const input = screen.getByRole("combobox");
    fireEvent.change(input, { target: { value: "a" } });
    await waitFor(() => expect(screen.getByText("Noor Ali")).toBeTruthy());

    const noor = screen.getByRole("option", { name: /Noor Ali/ });
    act(() => noor.focus());
    expect(noor.getAttribute("aria-selected")).toBe("true");

    // The finder must leave Enter on a focused row to the button itself.
    const notPrevented = fireEvent.keyDown(noor, { key: "Enter" });
    expect(notPrevented).toBe(true);
    expect(window.location.hash).toBe("#/board");

    // jsdom does not synthesize the native Enter→click on a button.
    fireEvent.click(noor);
    expect(window.location.hash).toBe("#/people/person-2");
    expect(onClose).toHaveBeenCalled();
  });

  it("closes on Escape", async () => {
    const onClose = vi.fn();
    render(<FindSavedPerson onClose={onClose} />);
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("shows the masked-surname note when Apollo hid the surname", async () => {
    api.searchSavedPeople.mockResolvedValue({
      results: [match({ last_name: null, last_name_status: "hidden_by_apollo" })],
      count: 1,
      query: "maya",
    });
    render(<FindSavedPerson onClose={() => {}} />);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "maya" } });
    await waitFor(() =>
      expect(screen.getByText(/Surname hidden by Apollo/)).toBeTruthy(),
    );
  });
});
