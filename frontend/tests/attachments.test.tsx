import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ChatPage } from "../src/routes/ChatPage";
import { resetAttachments } from "../src/chat/attachments";
import { resetPersonNames } from "../src/chat/personNames";
import type { AttachmentInfo, AttachmentLimits } from "../src/api";

const api = vi.hoisted(() => {
  class AttachmentError extends Error {
    constructor(
      readonly code: string,
      message: string,
    ) {
      super(message);
    }
  }
  return {
    AttachmentError,
    getSessions: vi.fn(),
    getPersona: vi.fn(),
    getSession: vi.fn(),
    getCurrentRunTelemetry: vi.fn(),
    getSkills: vi.fn(),
    getBoard: vi.fn(),
    openChat: vi.fn(),
    getAttachmentLimits: vi.fn(),
    uploadAttachment: vi.fn(),
    getAttachment: vi.fn(),
    getAttachmentText: vi.fn(),
    getAttachmentImage: vi.fn(),
  };
});
const chatSend = vi.fn();
const chatQueue = vi.fn();
let onChatEvent: ((event: Record<string, unknown>) => void) | undefined;

vi.mock("../src/api", () => ({
  AttachmentError: api.AttachmentError,
  getSessions: api.getSessions,
  getPersona: api.getPersona,
  getSession: api.getSession,
  getCurrentRunTelemetry: api.getCurrentRunTelemetry,
  getSkills: api.getSkills,
  getBoard: api.getBoard,
  hasToken: () => true,
  openChat: api.openChat,
  getAttachmentLimits: api.getAttachmentLimits,
  uploadAttachment: api.uploadAttachment,
  getAttachment: api.getAttachment,
  getAttachmentText: api.getAttachmentText,
  getAttachmentImage: api.getAttachmentImage,
}));

const LIMITS: AttachmentLimits = {
  max_files: 3,
  max_bytes: 10 * 1024 * 1024,
  max_text_chars: 30_000,
  max_context_chars: 90_000,
  max_image_bytes: 5 * 1024 * 1024,
  max_image_side: 8000,
  images: false,
  accept: ".pdf,.png,.jpg,.jpeg,.webp,.txt,.md,.markdown,.csv",
  types: ["PDF", "PNG", "JPEG", "WebP", "Text", "Markdown", "CSV"],
};

const ID = (digit: string) => `att_${digit.repeat(32)}`;

function stored(overrides: Partial<AttachmentInfo> & { id: string }): AttachmentInfo {
  return {
    name: "brief.pdf",
    size: 148 * 1024,
    kind: "pdf",
    mime: "application/pdf",
    chars: 90,
    total_chars: 90,
    truncated: false,
    pages: 2,
    encoding: null,
    width: null,
    height: null,
    excluded: null,
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

const pdf = (name = "brief.pdf", size = 148 * 1024) =>
  Object.defineProperty(new File(["%PDF-1.4"], name, { type: "application/pdf" }), "size", {
    value: size,
  });

async function openChat(conversation: Record<string, unknown> = {}) {
  api.getSession.mockResolvedValue({
    id: "thread-alpha",
    title: "Alpha",
    messages: [],
    events: [],
    ...conversation,
  });
  render(<ChatPage sessionId="thread-alpha" />);
  await screen.findByRole("heading", { level: 1, name: "Alpha" });
  await act(async () => {});
  return screen.getByRole("textbox", { name: "Message Sourcecado" });
}

const picker = () => screen.getByLabelText("Choose files to attach") as HTMLInputElement;
const chosen = (...files: File[]) =>
  fireEvent.change(picker(), { target: { files } });
const composer = (box: HTMLElement) => box.closest(".sourcecado-composer") as HTMLElement;
const fileList = (box: HTMLElement) =>
  within(composer(box)).queryByRole("list", { name: "Attached files" });
const send = () => fireEvent.click(screen.getByRole("button", { name: "Send message" }));

describe("Attaching files in the composer", () => {
  beforeEach(() => {
    window.localStorage.clear();
    chatSend.mockReset();
    chatQueue.mockReset();
    onChatEvent = undefined;
    resetAttachments();
    resetPersonNames();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: vi.fn(),
    });
    api.getSessions.mockResolvedValue({ sessions: [], open_id: "thread-alpha" });
    api.getPersona.mockResolvedValue({ id: "sourcing", name: "Sourcing Operator", tools: [] });
    api.getCurrentRunTelemetry.mockResolvedValue({
      version: 1,
      session_id: "thread-alpha",
      current_run: null,
    });
    api.getSkills.mockResolvedValue({ skills: [] });
    api.getBoard.mockResolvedValue({ backlog: [], open: [], in_conversation: [], done: [] });
    api.getAttachmentLimits.mockResolvedValue(LIMITS);
    api.uploadAttachment.mockImplementation(async (file: File) =>
      stored({ id: ID("1"), name: file.name, size: file.size }),
    );
    api.getAttachment.mockResolvedValue(null);
    api.getAttachmentText.mockResolvedValue(null);
    api.openChat.mockImplementation((callback) => {
      onChatEvent = callback;
      return {
        send: chatSend,
        approve: vi.fn(),
        cancel: vi.fn(),
        queue: chatQueue,
        recover: vi.fn(),
        close: vi.fn(),
      };
    });
  });

  describe("choosing a file", () => {
    it("shows the limits the backend gave on the menu row", async () => {
      await openChat();
      fireEvent.click(screen.getByRole("button", { name: "Add context" }));

      const item = await screen.findByRole("menuitem", { name: /Attach a file/ });
      expect(item).not.toHaveAttribute("aria-disabled");
      await waitFor(() =>
        expect(item).toHaveTextContent(
          "Up to 3 files, 10.0 MB each. The model reads 30,000 characters of text per file and 90,000 in all. No images with this model.",
        ),
      );
      expect(item).not.toHaveTextContent("Not available yet");
    });

    it("opens the file picker from the menu and limits it to the types the backend lists", async () => {
      await openChat();
      const click = vi.spyOn(picker(), "click");
      fireEvent.click(screen.getByRole("button", { name: "Add context" }));
      const item = await screen.findByRole("menuitem", { name: /Attach a file/ });
      await waitFor(() => expect(item).toHaveTextContent("Up to 3 files"));

      fireEvent.click(item);

      expect(click).toHaveBeenCalledOnce();
      expect(picker()).toHaveAttribute("accept", LIMITS.accept);
      expect(picker()).toHaveAttribute("multiple");
      expect(screen.queryByRole("menu", { name: "Add context" })).not.toBeInTheDocument();
    });

    it("shows a chip with icon, name, size and state, from processing to ready", async () => {
      const box = await openChat();
      const upload = deferred<AttachmentInfo>();
      api.uploadAttachment.mockReturnValueOnce(upload.promise);

      chosen(pdf());

      const list = await within(composer(box)).findByRole("list", { name: "Attached files" });
      const chip = within(list).getByRole("listitem");
      expect(chip).toHaveTextContent("brief.pdf");
      expect(chip).toHaveTextContent("148 KB · Processing…");
      expect(chip.querySelector("svg")).not.toBeNull();
      expect(api.uploadAttachment).toHaveBeenCalledWith(
        expect.objectContaining({ name: "brief.pdf" }),
        "brief.pdf",
        expect.any(AbortSignal),
      );

      await act(async () => upload.resolve(stored({ id: ID("1") })));
      expect(chip).toHaveTextContent("148 KB · Ready");
    });

    it("holds the message back even before the limits have loaded", async () => {
      const box = await openChat();
      const limits = deferred<AttachmentLimits>();
      api.getAttachmentLimits.mockReturnValueOnce(limits.promise);
      fireEvent.change(box, { target: { value: "Summarize this" } });

      chosen(pdf());
      send();
      fireEvent.keyDown(box, { key: "Enter" });

      expect(await within(composer(box)).findByText(/Processing/)).toBeInTheDocument();
      expect(chatSend).not.toHaveBeenCalled();
      await act(async () => limits.resolve(LIMITS));
      await within(composer(box)).findByText(/Ready/);
    });

    it("removes a file with its icon button and keeps focus in the box", async () => {
      const box = await openChat();
      chosen(pdf());
      await within(composer(box)).findByText(/Ready/);

      fireEvent.click(screen.getByRole("button", { name: "Remove brief.pdf" }));

      expect(fileList(box)).toBeNull();
      expect(box).toHaveFocus();
    });

    it("stops the upload of a file that is removed while it processes", async () => {
      const box = await openChat();
      const upload = deferred<AttachmentInfo>();
      api.uploadAttachment.mockReturnValueOnce(upload.promise);
      chosen(pdf());
      await within(composer(box)).findByText(/Processing/);
      const signal = api.uploadAttachment.mock.calls[0]![2] as AbortSignal;

      fireEvent.click(screen.getByRole("button", { name: "Remove brief.pdf" }));

      expect(signal.aborted).toBe(true);
      expect(fileList(box)).toBeNull();
    });

    it("attaches by drag and drop, and shows a drop target while files are over the box", async () => {
      const box = await openChat();
      const file = pdf("dropped.pdf");

      fireEvent.dragEnter(composer(box), { dataTransfer: { types: ["Files"], files: [file] } });
      expect(screen.getByText("Drop files to attach")).toBeInTheDocument();
      fireEvent.dragLeave(composer(box), { dataTransfer: { types: ["Files"], files: [file] } });
      expect(screen.queryByText("Drop files to attach")).not.toBeInTheDocument();

      fireEvent.dragEnter(composer(box), { dataTransfer: { types: ["Files"], files: [file] } });
      fireEvent.drop(composer(box), { dataTransfer: { types: ["Files"], files: [file] } });

      expect(await within(composer(box)).findByText(/dropped\.pdf/)).toBeInTheDocument();
      expect(screen.queryByText("Drop files to attach")).not.toBeInTheDocument();
      expect(api.uploadAttachment).toHaveBeenCalledOnce();
    });

    it("ignores a drag that holds no file", async () => {
      const box = await openChat();

      fireEvent.dragEnter(composer(box), { dataTransfer: { types: ["text/plain"], files: [] } });
      fireEvent.drop(composer(box), { dataTransfer: { types: ["text/plain"], files: [] } });

      expect(screen.queryByText("Drop files to attach")).not.toBeInTheDocument();
      expect(api.uploadAttachment).not.toHaveBeenCalled();
    });

    it("attaches a pasted picture and names it", async () => {
      const box = await openChat();
      const picture = new File(["png"], "image.png", { type: "image/png" });

      const handled = fireEvent.paste(box, {
        clipboardData: { files: [picture], types: ["Files"], getData: () => "" },
      });

      expect(handled).toBe(false); // the paste was taken over
      expect(await within(composer(box)).findByText(/Pasted image\.png/)).toBeInTheDocument();
      expect(api.uploadAttachment).toHaveBeenCalledWith(
        expect.objectContaining({ type: "image/png" }),
        "Pasted image.png",
        expect.any(AbortSignal),
      );
    });

    it("leaves pasted text as text, even with a picture of it beside it", async () => {
      const box = await openChat();
      const picture = new File(["png"], "image.png", { type: "image/png" });

      const onlyText = fireEvent.paste(box, {
        clipboardData: { files: [], types: ["text/plain"], getData: () => "hello" },
      });
      const textAndPicture = fireEvent.paste(box, {
        clipboardData: { files: [picture], types: ["Files", "text/plain"], getData: () => "A1\tB1" },
      });

      expect(onlyText).toBe(true);
      expect(textAndPicture).toBe(true);
      expect(api.uploadAttachment).not.toHaveBeenCalled();
      expect(fileList(box)).toBeNull();
    });
  });

  describe("limits", () => {
    it("stops at the count the backend allows and says how many were left out", async () => {
      const box = await openChat();

      chosen(pdf("a.pdf"), pdf("b.pdf"), pdf("c.pdf"), pdf("d.pdf"), pdf("e.pdf"));

      const list = await within(composer(box)).findByRole("list", { name: "Attached files" });
      await waitFor(() => expect(within(list).getAllByRole("listitem")).toHaveLength(3));
      expect(api.uploadAttachment).toHaveBeenCalledTimes(3);
      expect(screen.getByText("A message can carry up to 3 files. 2 not attached.")).toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: "Add context" }));
      const item = await screen.findByRole("menuitem", { name: /Attach a file/ });
      expect(item).toHaveAttribute("aria-disabled", "true");
      expect(item).toHaveTextContent("Up to 3 files per message. Remove one to add another.");
    });

    it("refuses a file over the size limit without uploading it", async () => {
      const box = await openChat();

      chosen(pdf("huge.pdf", 12 * 1024 * 1024));

      const chip = await within(composer(box)).findByRole("listitem");
      expect(chip).toHaveTextContent("huge.pdf");
      expect(within(chip).getByRole("alert")).toHaveTextContent(
        "This file is 12.0 MB. The limit is 10.0 MB.",
      );
      expect(api.uploadAttachment).not.toHaveBeenCalled();
    });
  });

  describe("failures keep the message from going", () => {
    it.each([
      ["an unsupported type", "tool.exe", "Unsupported file type. Attach a PDF, PNG, JPEG, WebP, text, Markdown or CSV file."],
      ["a corrupt PDF", "broken.pdf", "This PDF could not be read. It may be damaged."],
      ["an image for a model that cannot read images", "shot.png", "The current model cannot read images. Remove this image or switch to a model that can."],
      ["a file over the limit the server checks", "big.txt", "This file is over the 10.0 MB limit."],
    ])("%s shows the backend's words on its chip", async (_label, name, message) => {
      const box = await openChat();
      api.uploadAttachment.mockRejectedValueOnce(new api.AttachmentError("refused", message));

      chosen(pdf(name));

      const chip = await within(composer(box)).findByRole("listitem");
      expect(await within(chip).findByRole("alert")).toHaveTextContent(message);
      expect(chip).toHaveTextContent(name);
      expect(chip.querySelector("[data-state='failed']")).not.toBeNull();
    });

    it("cannot be sent while a file processes, and says why", async () => {
      const box = await openChat();
      const upload = deferred<AttachmentInfo>();
      api.uploadAttachment.mockReturnValueOnce(upload.promise);
      chosen(pdf());
      fireEvent.change(box, { target: { value: "Summarize this" } });

      send();
      fireEvent.keyDown(box, { key: "Enter" });

      expect(chatSend).not.toHaveBeenCalled();
      expect(screen.getByRole("button", { name: "Send message" })).toHaveAttribute(
        "aria-disabled",
        "true",
      );
      expect(screen.getByText("Wait for the file to finish processing.")).toBeInTheDocument();

      await act(async () => upload.resolve(stored({ id: ID("1") })));
      expect(screen.queryByText("Wait for the file to finish processing.")).not.toBeInTheDocument();
      send();
      await waitFor(() => expect(chatSend).toHaveBeenCalledOnce());
    });

    it("cannot be sent with a failed file until the file is removed", async () => {
      const box = await openChat();
      api.uploadAttachment.mockRejectedValueOnce(
        new api.AttachmentError("unsupported_type", "Unsupported file type. Attach a PDF."),
      );
      chosen(pdf("tool.exe"));
      await within(composer(box)).findByRole("alert");
      fireEvent.change(box, { target: { value: "Summarize this" } });

      send();
      fireEvent.keyDown(box, { key: "Enter" });

      expect(chatSend).not.toHaveBeenCalled();
      expect(screen.getByText("Remove the file that failed before you send.")).toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: "Remove tool.exe" }));
      send();
      await waitFor(() => expect(chatSend).toHaveBeenCalledOnce());
      expect(chatSend.mock.calls[0][0]).toBe("Summarize this");
    });

    it("holds the message back when one of two files failed, though the other is ready", async () => {
      const box = await openChat();
      api.uploadAttachment
        .mockResolvedValueOnce(stored({ id: ID("1") }))
        .mockRejectedValueOnce(new api.AttachmentError("corrupt_pdf", "This PDF could not be read."));
      chosen(pdf("good.pdf"), pdf("bad.pdf"));
      await within(composer(box)).findByRole("alert");
      fireEvent.change(box, { target: { value: "Read both" } });

      send();

      expect(chatSend).not.toHaveBeenCalled();
    });

    it("says it when the network fails, and the file can be removed", async () => {
      const box = await openChat();
      api.uploadAttachment.mockRejectedValueOnce(new TypeError("fetch failed"));

      chosen(pdf());

      expect(await within(composer(box)).findByRole("alert")).toHaveTextContent(
        "This file could not be attached.",
      );
    });
  });

  describe("sending", () => {
    it("sends the ids of ready files after the text, and no name", async () => {
      const box = await openChat();
      api.uploadAttachment
        .mockResolvedValueOnce(stored({ id: ID("1"), name: "brief.pdf" }))
        .mockResolvedValueOnce(stored({ id: ID("2"), name: "notes.md", kind: "markdown", pages: null }));
      chosen(pdf("brief.pdf"), pdf("notes.md"));
      await waitFor(() => expect(within(composer(box)).getAllByText(/Ready/)).toHaveLength(2));
      fireEvent.change(box, { target: { value: "Summarize both" } });

      send();

      await waitFor(() => expect(chatSend).toHaveBeenCalledOnce());
      expect(chatSend.mock.calls[0][0]).toBe(
        `Summarize both\n\n[Attached by the director]\nFile: file_id ${ID("1")}.\nFile: file_id ${ID("2")}.`,
      );
      expect(fileList(box)).toBeNull();
    });

    it("shows the sent message with chips, not the lines, and opens the preview", async () => {
      const box = await openChat();
      api.getAttachmentText.mockResolvedValue({
        text: "Target: staff engineers in Berkeley.",
        truncated: false,
        total_chars: 36,
      });
      chosen(pdf("brief.pdf"));
      await within(composer(box)).findByText(/Ready/);
      fireEvent.change(box, { target: { value: "Summarize this" } });
      send();
      await waitFor(() => expect(chatSend).toHaveBeenCalledOnce());

      const log = screen.getByRole("log", { name: "Conversation" });
      expect(await within(log).findByText("Summarize this")).toBeInTheDocument();
      expect(log).not.toHaveTextContent("file_id");
      const chip = await within(log).findByRole("button", { name: "Open brief.pdf" });
      fireEvent.click(chip);

      const inspector = screen.getByRole("complementary", { name: "Inspector" });
      expect(await within(inspector).findByLabelText("Text of brief.pdf")).toHaveTextContent(
        "Target: staff engineers in Berkeley.",
      );
      expect(api.getAttachment).not.toHaveBeenCalled(); // the upload already told us
    });

    it("queues the ids with the message while a run is active", async () => {
      const box = await openChat();
      chosen(pdf());
      await within(composer(box)).findByText(/Ready/);
      act(() => {
        onChatEvent?.({
          version: 2,
          type: "turn_start",
          session_id: "thread-alpha",
          run_id: "run-1",
          event_id: "run-1-start",
          message_id: "run-1-assistant",
          part_id: "run-1-part",
          state: "running",
        });
      });
      fireEvent.change(box, { target: { value: "Then summarize this" } });
      fireEvent.keyDown(box, { key: "Enter" });

      await waitFor(() => expect(chatQueue).toHaveBeenCalledOnce());
      expect(chatQueue.mock.calls[0][0].text).toBe(
        `Then summarize this\n\n[Attached by the director]\nFile: file_id ${ID("1")}.`,
      );
    });
  });

  describe("images", () => {
    it("refuses an image over the image limit without uploading it", async () => {
      const box = await openChat();
      const picture = new File(["x"], "big.png", { type: "image/png" });
      Object.defineProperty(picture, "size", { value: 6 * 1024 * 1024 });

      chosen(picture);

      const chip = await within(composer(box)).findByRole("listitem");
      expect(await within(chip).findByRole("alert")).toHaveTextContent(
        "This image is 6.0 MB. Models accept images up to 5.0 MB.",
      );
      expect(api.uploadAttachment).not.toHaveBeenCalled();
    });

    it("shows the words for a broken image that the backend gave", async () => {
      const box = await openChat();
      api.uploadAttachment.mockRejectedValueOnce(
        new api.AttachmentError("corrupt_image", "This image could not be read. It may be damaged."),
      );

      chosen(new File(["x"], "broken.jpg", { type: "image/jpeg" }));

      expect(await within(composer(box)).findByRole("alert")).toHaveTextContent(
        "This image could not be read. It may be damaged.",
      );
    });
  });

  describe("editing a message that carried files", () => {
    const sentFile = (id: string) => `Summarize this\n\n[Attached by the director]\nFile: file_id ${id}.`;
    const edit = async () =>
      fireEvent.click(await screen.findByRole("button", { name: "Edit message" }));

    it("puts the files back as chips and not as reference lines, and resends them", async () => {
      api.getAttachment.mockResolvedValue(stored({ id: ID("5"), name: "kept.pdf" }));
      const box = await openChat({ messages: [{ role: "user", content: sentFile(ID("5")) }] });

      await edit();

      await waitFor(() => expect(box).toHaveValue("Summarize this"));
      const chip = await within(composer(box)).findByRole("listitem");
      expect(chip).toHaveTextContent("kept.pdf");
      expect(await within(chip).findByText(/Ready/)).toBeInTheDocument();
      expect(box).not.toHaveValue(expect.stringContaining("file_id"));

      // Any change to the text keeps the file.
      fireEvent.change(box, { target: { value: "Summarize this in two lines" } });
      send();
      await waitFor(() => expect(chatSend).toHaveBeenCalledOnce());
      expect(chatSend.mock.calls[0][0]).toBe(
        `Summarize this in two lines\n\n[Attached by the director]\nFile: file_id ${ID("5")}.`,
      );
    });

    it("shows a file that is gone as failed and holds the send until it is removed", async () => {
      api.getAttachment.mockResolvedValue(null);
      const box = await openChat({ messages: [{ role: "user", content: sentFile(ID("6")) }] });

      await edit();

      const chip = await within(composer(box)).findByRole("listitem");
      expect(await within(chip).findByRole("alert")).toHaveTextContent(
        "File unavailable. It is no longer on this computer.",
      );
      send();
      expect(chatSend).not.toHaveBeenCalled();
      expect(screen.getByText("Remove the file that failed before you send.")).toBeInTheDocument();
      fireEvent.click(within(chip).getByRole("button", { name: /^Remove/ }));
      send();
      await waitFor(() => expect(chatSend).toHaveBeenCalledOnce());
      expect(chatSend.mock.calls[0][0]).toBe("Summarize this");
    });

    it("shows an image a provider refused as failed, so it is not sent again by accident", async () => {
      api.getAttachment.mockResolvedValue(
        stored({ id: ID("7"), name: "refused.png", kind: "image", excluded: "provider_rejected" }),
      );
      const box = await openChat({ messages: [{ role: "user", content: sentFile(ID("7")) }] });

      await edit();

      const chip = await within(composer(box)).findByRole("listitem");
      expect(await within(chip).findByRole("alert")).toHaveTextContent(
        "The model provider rejected this image earlier. Remove it.",
      );
      expect(chip).toHaveTextContent("refused.png");
      send();
      expect(chatSend).not.toHaveBeenCalled();
    });

    it("does not add a second chip for a file that is already attached", async () => {
      api.getAttachment.mockResolvedValue(stored({ id: ID("1"), name: "brief.pdf" }));
      const box = await openChat({ messages: [{ role: "user", content: sentFile(ID("1")) }] });
      chosen(pdf("brief.pdf"));
      await within(composer(box)).findByText(/Ready/);

      await edit();

      await waitFor(() => expect(within(composer(box)).getAllByRole("listitem")).toHaveLength(1));
    });

    it("holds the send when edits bring back more files than a message can carry", async () => {
      const box = await openChat({
        messages: [
          {
            role: "user",
            content: `Look\n\n[Attached by the director]\n${["1", "2", "3", "4"]
              .map((digit) => `File: file_id ${ID(digit)}.`)
              .join("\n")}`,
          },
        ],
      });
      api.getAttachment.mockImplementation(async (id: string) => stored({ id, name: `${id.slice(4, 6)}.pdf` }));
      // The limits are read when the menu opens, as they are for any attach.
      fireEvent.click(screen.getByRole("button", { name: "Add context" }));
      await screen.findByRole("menuitem", { name: /Attach a file/ });
      await waitFor(() => expect(api.getAttachmentLimits).toHaveBeenCalled());
      fireEvent.keyDown(screen.getByRole("menu", { name: "Add context" }), { key: "Escape" });

      await edit();

      await waitFor(() => expect(within(composer(box)).getAllByRole("listitem")).toHaveLength(4));
      expect(await screen.findByText("Remove 1 file. A message can carry up to 3.")).toBeInTheDocument();
      send();
      expect(chatSend).not.toHaveBeenCalled();
    });
  });

  describe("files the model does not see", () => {
    const sentFile = (id: string) => `Look\n\n[Attached by the director]\nFile: file_id ${id}.`;

    it.each([
      ["left_out", "Left out of the model's view. The request is at its text limit."],
      ["provider_rejected", "The model provider rejected an image in this thread. It is not sent."],
    ] as const)("says so on the chip when %s, and the file still opens", async (excluded, line) => {
      api.getAttachment.mockResolvedValue(stored({ id: ID("8"), name: "old.pdf", excluded }));
      api.getAttachmentText.mockResolvedValue({ text: "Old text.", truncated: false, total_chars: 9 });
      await openChat({ messages: [{ role: "user", content: sentFile(ID("8")) }] });

      const log = screen.getByRole("log", { name: "Conversation" });
      const chip = await within(log).findByRole("button", { name: "Open old.pdf" });

      expect(chip).toHaveAttribute("data-state", "excluded");
      expect(chip).toHaveTextContent(line);
      fireEvent.click(chip);
      expect(await screen.findByLabelText("Text of old.pdf")).toHaveTextContent("Old text.");
    });
  });

  describe("after a reload", () => {
    const sentFile = (id: string) => `Look at this\n\n[Attached by the director]\nFile: file_id ${id}.`;

    it("shows the chip from the kept record and opens the preview", async () => {
      api.getAttachment.mockResolvedValue(stored({ id: ID("5"), name: "kept.pdf" }));
      api.getAttachmentText.mockResolvedValue({ text: "Kept text.", truncated: false, total_chars: 10 });
      await openChat({ messages: [{ role: "user", content: sentFile(ID("5")) }] });

      const log = screen.getByRole("log", { name: "Conversation" });
      fireEvent.click(await within(log).findByRole("button", { name: "Open kept.pdf" }));

      expect(await screen.findByLabelText("Text of kept.pdf")).toHaveTextContent("Kept text.");
    });

    it("shows File unavailable for a file whose bytes are gone", async () => {
      api.getAttachment.mockResolvedValue(null);
      await openChat({ messages: [{ role: "user", content: sentFile(ID("6")) }] });

      const log = screen.getByRole("log", { name: "Conversation" });
      expect(await within(log).findByText("File unavailable")).toBeInTheDocument();
      expect(within(log).queryByRole("button", { name: /Open/ })).not.toBeInTheDocument();
      expect(log).not.toHaveTextContent("file_id");
    });
  });
});
