import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MessageFileChips } from "../src/chat/AttachmentChips";
import { describeAttachment, parseCsv, resetAttachments } from "../src/chat/attachments";
import { Inspector, InspectorProvider } from "../src/chat/Inspector";
import { splitContext, withContext } from "../src/chat/messageContext";
import type { AttachmentInfo } from "../src/api";

const api = vi.hoisted(() => ({
  getAttachment: vi.fn(),
  getAttachmentText: vi.fn(),
  getAttachmentImage: vi.fn(),
}));

vi.mock("../src/api", () => ({
  getAttachment: api.getAttachment,
  getAttachmentText: api.getAttachmentText,
  getAttachmentImage: api.getAttachmentImage,
}));

const ID = (digit: string) => `att_${digit.repeat(32)}`;

function info(overrides: Partial<AttachmentInfo> & { id: string }): AttachmentInfo {
  return {
    name: "brief.pdf",
    size: 148 * 1024,
    kind: "pdf",
    mime: "application/pdf",
    chars: 120,
    total_chars: 120,
    truncated: false,
    pages: 2,
    encoding: null,
    width: null,
    height: null,
    excluded: null,
    ...overrides,
  };
}

const FILES: Record<string, AttachmentInfo> = {
  [ID("1")]: info({ id: ID("1") }),
  [ID("2")]: info({
    id: ID("2"),
    name: "shot.png",
    kind: "image",
    mime: "image/png",
    size: 2048,
    pages: null,
    chars: 0,
    total_chars: null,
    width: 640,
    height: 400,
  }),
  [ID("3")]: info({
    id: ID("3"),
    name: "people.csv",
    kind: "csv",
    mime: "text/plain",
    size: 154,
    pages: null,
  }),
  [ID("4")]: info({
    id: ID("4"),
    name: "notes.md",
    kind: "markdown",
    mime: "text/plain",
    size: 900,
    pages: null,
    chars: 30_000,
    total_chars: 81_204,
    truncated: true,
  }),
};

const TEXTS: Record<string, { text: string; truncated: boolean; total_chars: number | null }> = {
  [ID("1")]: {
    text: "Quarterly sourcing brief\nTarget: staff engineers in Berkeley.",
    truncated: false,
    total_chars: 58,
  },
  [ID("3")]: {
    text:
      'company,contact,role\nAcme Robotics,Maya Chen,Founder\nLinear,"Alvarez, Jon","Head of ""Eng"""\n',
    truncated: false,
    total_chars: 90,
  },
  [ID("4")]: {
    text: "# Target\nFind early engineers.",
    truncated: true,
    total_chars: 81_204,
  },
};

function renderChips(ids: string[]) {
  return render(
    <InspectorProvider threadId="thread-1">
      <p>
        <button type="button">outside</button>
      </p>
      <MessageFileChips ids={ids} />
      <Inspector />
    </InspectorProvider>,
  );
}

const panel = () => screen.getByRole("complementary", { name: "Inspector" });

describe("Attached files on a sent message", () => {
  beforeEach(() => {
    resetAttachments();
    api.getAttachment.mockImplementation(async (id: string) => FILES[id] ?? null);
    api.getAttachmentText.mockImplementation(async (id: string) => TEXTS[id] ?? null);
    api.getAttachmentImage.mockResolvedValue("data:image/png;base64,iVBORw0KGgo=");
  });

  it("shows each file as a chip with its kind icon, name, type and size", async () => {
    renderChips([ID("1"), ID("2"), ID("3")]);

    const list = await screen.findByRole("list", { name: "Attached files" });
    const chips = await within(list).findAllByRole("button");
    expect(chips.map((chip) => chip.getAttribute("aria-label"))).toEqual([
      "Open brief.pdf",
      "Open shot.png",
      "Open people.csv",
    ]);
    expect(chips[0]).toHaveTextContent("PDF · 2 pages · 148 KB");
    expect(chips[1]).toHaveTextContent("Image · 640 by 400 pixels · 2 KB");
    expect(chips[2]).toHaveTextContent("CSV · 154 B");
    for (const chip of chips) expect(chip.querySelector("svg")).not.toBeNull();
  });

  it("opens a PDF as readable text in the inspector and returns focus on close", async () => {
    renderChips([ID("1")]);
    const chip = await screen.findByRole("button", { name: "Open brief.pdf" });
    chip.focus();
    fireEvent.click(chip);

    const inspector = panel();
    expect(within(inspector).getByRole("heading", { level: 2 })).toHaveTextContent("brief.pdf");
    expect(inspector).toHaveTextContent("Attached file");
    const body = await within(inspector).findByLabelText("Text of brief.pdf");
    expect(body).toHaveTextContent("Target: staff engineers in Berkeley.");
    expect(inspector).toHaveTextContent("Text read from the PDF");
    expect(inspector).not.toHaveTextContent("The model reads the same part");

    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("complementary")).not.toBeInTheDocument());
    await waitFor(() => expect(chip).toHaveFocus());
  });

  it("says when the text is cut, with the numbers", async () => {
    renderChips([ID("4")]);
    fireEvent.click(await screen.findByRole("button", { name: "Open notes.md" }));

    const body = await within(panel()).findByLabelText("Text of notes.md");
    expect(body).toHaveTextContent("Find early engineers.");
    expect(panel()).toHaveTextContent(
      "Showing the first 30 of 81,204 characters. The model reads the same part.",
    );
  });

  it("shows a CSV as a table, with quoted commas and quotes kept", async () => {
    renderChips([ID("3")]);
    fireEvent.click(await screen.findByRole("button", { name: "Open people.csv" }));

    const region = await within(panel()).findByRole("region", { name: "Rows of people.csv" });
    const table = within(region).getByRole("table");
    expect(
      within(table)
        .getAllByRole("columnheader")
        .map((cell) => cell.textContent),
    ).toEqual(["company", "contact", "role"]);
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((row) => within(row).getAllByRole("cell").map((cell) => cell.textContent))).toEqual([
      ["Acme Robotics", "Maya Chen", "Founder"],
      ["Linear", "Alvarez, Jon", 'Head of "Eng"'],
    ]);
    expect(panel()).toHaveTextContent("2 rows under the header row.");
  });

  it("shows an image with zoom controls and keys", async () => {
    renderChips([ID("2")]);
    fireEvent.click(await screen.findByRole("button", { name: "Open shot.png" }));

    const picture = await within(panel()).findByAltText("shot.png");
    expect(picture).toHaveAttribute("src", "data:image/png;base64,iVBORw0KGgo=");
    const zoomIn = within(panel()).getByRole("button", { name: "Zoom in" });
    const zoomOut = within(panel()).getByRole("button", { name: "Zoom out" });
    const fit = within(panel()).getByRole("button", { name: "Fit to panel" });
    const level = () => within(panel()).getByRole("status");
    expect(level()).toHaveTextContent("100%");
    expect(fit).toBeDisabled();

    fireEvent.click(zoomIn);
    expect(level()).toHaveTextContent("150%");
    expect(picture.style.width).toBe("150%");
    expect(fit).not.toBeDisabled();
    fireEvent.click(zoomOut);
    fireEvent.click(zoomOut);
    expect(level()).toHaveTextContent("75%");

    const frame = within(panel()).getByLabelText(/Picture shot\.png/);
    fireEvent.keyDown(frame, { key: "+" });
    fireEvent.keyDown(frame, { key: "+" });
    expect(level()).toHaveTextContent("150%");
    fireEvent.keyDown(frame, { key: "0" });
    expect(level()).toHaveTextContent("100%");
    for (let press = 0; press < 8; press += 1) fireEvent.click(zoomIn);
    expect(level()).toHaveTextContent("400%");
    expect(zoomIn).toBeDisabled();
  });

  it("shows File unavailable for a file whose bytes are gone, and never a blank chip", async () => {
    renderChips([ID("9")]);

    const list = await screen.findByRole("list", { name: "Attached files" });
    await within(list).findByText("File unavailable");
    expect(within(list).queryByRole("button")).not.toBeInTheDocument();
    expect(list).toHaveTextContent("It is no longer on this computer.");
  });

  it("says File unavailable in the preview when the text is gone after the chip loaded", async () => {
    api.getAttachmentText.mockResolvedValue(null);
    renderChips([ID("1")]);
    fireEvent.click(await screen.findByRole("button", { name: "Open brief.pdf" }));

    expect(await within(panel()).findByRole("alert")).toHaveTextContent("File unavailable");
  });

  it("lets the director try again when the file details did not load", async () => {
    api.getAttachment.mockRejectedValueOnce(new Error("attachment 500"));
    renderChips([ID("1")]);

    const retry = await screen.findByRole("button", { name: /File details did not load/ });
    fireEvent.click(retry);

    expect(await screen.findByRole("button", { name: "Open brief.pdf" })).toBeInTheDocument();
  });

  it("reads each file's record once", async () => {
    renderChips([ID("1"), ID("1")]);
    await screen.findAllByRole("button", { name: "Open brief.pdf" });
    expect(api.getAttachment).toHaveBeenCalledTimes(1);
  });
});

describe("describing a file", () => {
  it("names the encoding of a converted text file, and says nothing for UTF-8", () => {
    const base = info({ id: ID("9"), kind: "csv", pages: null, size: 285 });

    expect(describeAttachment({ ...base, encoding: "windows-1252" })).toBe(
      "CSV · Windows-1252 text · 285 B",
    );
    expect(describeAttachment({ ...base, encoding: "utf-16" })).toBe("CSV · UTF-16 text · 285 B");
    expect(describeAttachment({ ...base, encoding: "utf-8" })).toBe("CSV · 285 B");
    expect(describeAttachment({ ...base, encoding: null })).toBe("CSV · 285 B");
  });
});

describe("the reference lines a message carries", () => {
  it("round-trips people and files and keeps the text apart", () => {
    const text = withContext(
      "Summarize these.",
      [{ kind: "person", id: "per_1" }],
      [ID("1"), ID("2")],
    );

    expect(text).toBe(
      "Summarize these.\n\n[Attached by the director]\n" +
        "Person: person_id per_1. Read the person file with board_get.\n" +
        `File: file_id ${ID("1")}.\nFile: file_id ${ID("2")}.`,
    );
    expect(splitContext(text)).toEqual({
      body: "Summarize these.",
      chips: [{ kind: "person", id: "per_1" }],
      files: [ID("1"), ID("2")],
    });
  });

  it("never puts a file name in the message", () => {
    expect(withContext("Read it", [], [ID("1")])).not.toMatch(/\.pdf/);
  });

  it("treats a block with one odd line as plain text", () => {
    const text = `Hi\n\n[Attached by the director]\nFile: file_id ${ID("1")}.\nFile: file_id ../../x.`;
    expect(splitContext(text)).toEqual({ body: text, chips: [], files: [] });
  });
});

describe("parseCsv", () => {
  it("reads quotes, doubled quotes and line breaks inside quotes", () => {
    expect(parseCsv('a,b\n"x, y","say ""hi"""\n"line\nbreak",z\n')).toEqual([
      ["a", "b"],
      ["x, y", 'say "hi"'],
      ["line\nbreak", "z"],
    ]);
  });

  it("skips empty lines and reads a last row with no line break", () => {
    expect(parseCsv("a,b\r\n\r\nc,d")).toEqual([
      ["a", "b"],
      ["c", "d"],
    ]);
  });
});
