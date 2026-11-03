import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DescriptionEditor, describeDescription } from "../src/DescriptionEditor";
import { DescriptionSaveError, type PersonDescription } from "../src/api";

const patchPersonDescription = vi.fn();

vi.mock("../src/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/api")>()),
  patchPersonDescription: (...args: unknown[]) => patchPersonDescription(...args),
}));

function description(over: Partial<PersonDescription> = {}): PersonDescription {
  return {
    slot: "general",
    text: null,
    authority: "assistant",
    source_refs: [],
    version: 0,
    ...over,
  };
}

const descriptionsFrom = (next: PersonDescription) => ({
  general: next.slot === "general" ? next : description(),
  detailed: next.slot === "detailed" ? next : description({ slot: "detailed" }),
});

beforeEach(() => {
  patchPersonDescription.mockReset();
});

afterEach(() => {
  window.localStorage.clear();
});

describe("describeDescription", () => {
  it("reads an untouched slot as unsummarized", () => {
    expect(describeDescription(description())).toEqual({
      text: "Not summarized yet",
      muted: true,
    });
  });

  it("reads a human blank as a protected dash, not unsummarized", () => {
    expect(
      describeDescription(description({ authority: "human", text: "" })),
    ).toEqual({ text: "—", muted: true });
  });

  it("shows the text when there is some", () => {
    expect(
      describeDescription(description({ authority: "human", text: "Warm intro." })),
    ).toEqual({ text: "Warm intro.", muted: false });
  });
});

describe("DescriptionEditor", () => {
  it("saves edited text and reports the new descriptions", async () => {
    const saved = description({ authority: "human", text: "Concise.", version: 1 });
    patchPersonDescription.mockResolvedValue({
      descriptions: descriptionsFrom(saved),
      description: saved,
      saved: true,
    });
    const onSaved = vi.fn();
    render(
      <DescriptionEditor personId="p1" description={description()} onSaved={onSaved} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Add description" }));
    const field = screen.getByLabelText("Short description");
    fireEvent.change(field, { target: { value: "Concise." } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(patchPersonDescription).toHaveBeenCalledWith("p1", "general", {
        text: "Concise.",
        expectedVersion: 0,
      }),
    );
    expect(onSaved).toHaveBeenCalledWith(descriptionsFrom(saved));
  });

  it("keeps the unsaved text when a save fails on a stale version", async () => {
    patchPersonDescription.mockRejectedValue(
      new DescriptionSaveError(409, "stale record version"),
    );
    render(
      <DescriptionEditor personId="p1" description={description()} onSaved={vi.fn()} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Add description" }));
    const field = screen.getByLabelText("Short description");
    fireEvent.change(field, { target: { value: "Attempted." } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/saved copy changed/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Short description")).toHaveValue("Attempted.");
  });

  it("flags over-limit text and blocks the save", () => {
    render(
      <DescriptionEditor personId="p1" description={description()} onSaved={vi.fn()} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Add description" }));
    fireEvent.change(screen.getByLabelText("Short description"), {
      target: { value: "x".repeat(501) },
    });
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(screen.getByText("501 / 500")).toBeInTheDocument();
  });

  it("offers release only on a human-owned slot and sends it", async () => {
    const human = description({ authority: "human", text: "Mine.", version: 2 });
    const released = description({ authority: "assistant", text: "Mine.", version: 3 });
    patchPersonDescription.mockResolvedValue({
      descriptions: descriptionsFrom(released),
      description: released,
      saved: true,
    });
    const onSaved = vi.fn();
    render(<DescriptionEditor personId="p1" description={human} onSaved={onSaved} />);
    fireEvent.click(
      screen.getByRole("button", { name: "Let the assistant maintain this" }),
    );
    await waitFor(() =>
      expect(patchPersonDescription).toHaveBeenCalledWith("p1", "general", {
        release: true,
        expectedVersion: 2,
      }),
    );
    expect(onSaved).toHaveBeenCalledWith(descriptionsFrom(released));
  });

  it("has no release control on an assistant-owned slot", () => {
    render(
      <DescriptionEditor personId="p1" description={description()} onSaved={vi.fn()} />,
    );
    expect(
      screen.queryByRole("button", { name: "Let the assistant maintain this" }),
    ).not.toBeInTheDocument();
  });

  it("restores an unsaved draft after remount (survives relaunch)", () => {
    const first = render(
      <DescriptionEditor personId="p1" description={description()} onSaved={vi.fn()} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Add description" }));
    fireEvent.change(screen.getByLabelText("Short description"), {
      target: { value: "Half-written." },
    });
    first.unmount();
    render(
      <DescriptionEditor personId="p1" description={description()} onSaved={vi.fn()} />,
    );
    expect(screen.getByLabelText("Short description")).toHaveValue("Half-written.");
  });

  it("keeps text typed while a save is in flight instead of losing it", async () => {
    let resolveSave!: (value: unknown) => void;
    patchPersonDescription.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveSave = resolve;
        }),
    );
    const saved = description({ authority: "human", text: "First.", version: 1 });
    const onSaved = vi.fn();
    const onClose = vi.fn();
    render(
      <DescriptionEditor
        personId="p1"
        description={description()}
        onSaved={onSaved}
        onClose={onClose}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Add description" }));
    const field = screen.getByLabelText("Short description");
    fireEvent.change(field, { target: { value: "First." } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    // The request carries only the text present when Save was pressed...
    expect(patchPersonDescription).toHaveBeenCalledWith("p1", "general", {
      text: "First.",
      expectedVersion: 0,
    });
    // ...but the user keeps typing while it is still in flight.
    fireEvent.change(field, { target: { value: "First. And more." } });
    resolveSave({
      descriptions: descriptionsFrom(saved),
      description: saved,
      saved: true,
    });
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(descriptionsFrom(saved)));
    // The newer keystrokes survive the save's success handler.
    expect(screen.getByLabelText("Short description")).toHaveValue("First. And more.");
    // The editor stays open so the pending edit is not silently dropped.
    expect(onClose).not.toHaveBeenCalled();
  });

  it("cancel discards the draft back to the saved text", () => {
    const saved = description({ authority: "human", text: "Saved copy.", version: 1 });
    render(<DescriptionEditor personId="p1" description={saved} onSaved={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit description" }));
    const field = screen.getByLabelText("Short description");
    expect(field).toHaveValue("Saved copy.");
    fireEvent.change(field, { target: { value: "Scratch." } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    // Cancel closes the editor back to the saved text and drops the draft.
    expect(screen.queryByLabelText("Short description")).toBeNull();
    expect(screen.getByText("Saved copy.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit description" })).toHaveFocus();
    fireEvent.click(screen.getByRole("button", { name: "Edit description" }));
    expect(screen.getByLabelText("Short description")).toHaveValue("Saved copy.");
  });

  it("shows one Add button for an empty slot and reveals the editor on click", () => {
    render(
      <DescriptionEditor personId="p1" description={description()} onSaved={vi.fn()} />,
    );
    // No empty textarea, counter or disabled Save/Cancel/Clear at rest.
    expect(screen.queryByLabelText("Short description")).toBeNull();
    expect(screen.queryByText("0 / 500")).toBeNull();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Clear" })).toBeNull();
    expect(screen.queryByText("Assistant")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Add description" }));

    expect(screen.getByLabelText("Short description")).toHaveFocus();
    // The counter waits until the text nears the limit.
    expect(screen.queryByText("0 / 500")).toBeNull();
    expect(screen.queryByRole("button", { name: "Add description" })).toBeNull();
    fireEvent.change(screen.getByLabelText("Short description"), {
      target: { value: "x".repeat(420) },
    });
    expect(screen.getByText("420 / 500")).toBeInTheDocument();
  });

  it("closes an unchanged editor on Escape and asks twice before dropping changes", () => {
    const saved = description({ authority: "human", text: "Saved copy.", version: 1 });
    render(<DescriptionEditor personId="p1" description={saved} onSaved={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit description" }));
    fireEvent.keyDown(screen.getByLabelText("Short description"), { key: "Escape" });
    expect(screen.queryByLabelText("Short description")).toBeNull();
    expect(screen.getByRole("button", { name: "Edit description" })).toHaveFocus();

    fireEvent.click(screen.getByRole("button", { name: "Edit description" }));
    const field = screen.getByLabelText("Short description");
    fireEvent.change(field, { target: { value: "Scratch." } });
    fireEvent.keyDown(field, { key: "Escape" });
    expect(field).toHaveValue("Scratch.");
    expect(screen.getByText("Press Escape again to discard")).toBeInTheDocument();
    fireEvent.keyDown(field, { key: "Escape" });
    expect(screen.queryByLabelText("Short description")).toBeNull();
    expect(screen.getByText("Saved copy.")).toBeInTheDocument();
    expect(patchPersonDescription).not.toHaveBeenCalled();
  });

  it("saves with Cmd+Enter", async () => {
    const saved = description({ authority: "human", text: "Typed.", version: 1 });
    patchPersonDescription.mockResolvedValue({ descriptions: descriptionsFrom(saved), description: saved, saved: true });
    render(<DescriptionEditor personId="p1" description={description()} onSaved={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Add description" }));
    fireEvent.change(screen.getByLabelText("Short description"), { target: { value: "Typed." } });
    fireEvent.keyDown(screen.getByLabelText("Short description"), { key: "Enter", metaKey: true });
    await waitFor(() =>
      expect(patchPersonDescription).toHaveBeenCalledWith("p1", "general", { text: "Typed.", expectedVersion: 0 }),
    );
  });

  it("shows saved text with its owner and an Edit button, not an open editor", () => {
    const saved = description({ authority: "human", text: "Warm intro.", version: 1 });
    render(<DescriptionEditor personId="p1" description={saved} onSaved={vi.fn()} />);
    expect(screen.getByText("Warm intro.")).toBeInTheDocument();
    expect(screen.getByText("Human-edited")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit description" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Short description")).toBeNull();
  });

  it("keeps a human-protected blank visible with its release control", () => {
    const blank = description({ authority: "human", text: "", version: 4 });
    render(<DescriptionEditor personId="p1" description={blank} onSaved={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Add description" })).toBeInTheDocument();
    expect(screen.getByText("Human-edited")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Let the assistant maintain this" }),
    ).toBeInTheDocument();
  });

  it("names the detailed slot's buttons apart from the short one", () => {
    render(
      <DescriptionEditor
        personId="p1"
        description={description({ slot: "detailed" })}
        onSaved={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Add detailed description" }));
    expect(screen.getByLabelText("Detailed description")).toHaveFocus();
  });

  it("opens straight into the editor when the host already chose to edit", () => {
    render(
      <DescriptionEditor
        personId="p1"
        description={description()}
        onSaved={vi.fn()}
        startEditing
      />,
    );
    expect(screen.getByLabelText("Short description")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add description" })).toBeNull();
  });
});
