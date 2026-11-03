import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { SkillDraft, SkillInfo } from "../src/api";
import { ChatPage } from "../src/routes/ChatPage";
import { SkillsPage } from "../src/routes/SkillsPage";

const api = vi.hoisted(() => ({
  getSkills: vi.fn(),
  createSkill: vi.fn(),
  updateSkill: vi.fn(),
  deleteSkill: vi.fn(),
  getSessions: vi.fn(),
  getPersona: vi.fn(),
  getSession: vi.fn(),
  getCurrentRunTelemetry: vi.fn(),
  getBoard: vi.fn(),
  openChat: vi.fn(),
  getAttachmentLimits: vi.fn(),
}));

vi.mock("../src/api", () => ({
  ...api,
  hasToken: () => true,
}));

function skill(overrides: Partial<SkillInfo>): SkillInfo {
  return {
    name: "weekly-sourcing",
    purpose: "Builds a compact shortlist from active Person Files.",
    useWhen: "The director asks who to work next.",
    source: "builtin",
    status: "ready",
    instructions: "1. Start from the Target.\n2. Treat each **Person** as the unit of work.",
    builtinDefault: true,
    ...overrides,
  };
}

const BUILTIN = skill({});
const OUTREACH = skill({
  name: "outreach-brief",
  purpose: "Prepare a source-backed outreach brief.",
  useWhen: "",
  source: "workspace",
  instructions: "- Read the Person File.",
  builtinDefault: false,
});

/** A catalog the mocked API reads and writes, like the backend's skills folder. */
let catalog: SkillInfo[] = [];

function saved(draft: SkillDraft): SkillInfo {
  return skill({ ...draft, source: "workspace", builtinDefault: draft.name === BUILTIN.name });
}

beforeEach(() => {
  catalog = [BUILTIN, OUTREACH];
  for (const mock of Object.values(api)) mock.mockReset();
  api.getSkills.mockImplementation(async () => ({ skills: [...catalog] }));
  api.createSkill.mockImplementation(async (draft: SkillDraft) => {
    catalog = [...catalog, saved(draft)];
    return saved(draft);
  });
  api.updateSkill.mockImplementation(async (draft: SkillDraft) => {
    catalog = catalog.map((row) => (row.name === draft.name ? saved(draft) : row));
    return saved(draft);
  });
  api.deleteSkill.mockImplementation(async (name: string) => {
    catalog = catalog.filter((row) => row.name !== name);
  });
});

async function openPage() {
  render(<SkillsPage />);
  return screen.findByRole("list", { name: "Skills" });
}

function field(name: string) {
  return screen.getByRole("textbox", { name });
}

describe("SkillsPage", () => {
  it("shows a loading state with one page title", () => {
    api.getSkills.mockReturnValue(new Promise(() => {}));
    render(<SkillsPage />);

    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("status", { name: "Loading skills" })).toBeInTheDocument();
  });

  it("lists skills and shows the selected one with its command and no narrating copy", async () => {
    const list = await openPage();

    expect(within(list).getAllByRole("button")).toHaveLength(2);
    expect(screen.getByText("2")).toHaveClass("sm-count");
    const detail = screen.getByRole("article");
    expect(within(detail).getByRole("heading", { level: 2 })).toHaveTextContent("Weekly sourcing");
    expect(within(detail).getByText("/weekly-sourcing")).toBeInTheDocument();
    expect(within(detail).getByText("Built in")).toBeInTheDocument();
    expect(within(detail).getByText("The director asks who to work next.")).toBeInTheDocument();
    expect(within(detail).getByText("Person").tagName).toBe("STRONG");
    expect(within(detail).queryByRole("button", { name: /Delete/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/Instructions that extend/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Use when/)).not.toBeInTheDocument();
    for (const button of screen.getAllByRole("button")) {
      expect(button.querySelector("svg")).not.toBeNull();
    }
  });

  it("searches and clears a search with no match", async () => {
    await openPage();
    const search = screen.getByRole("searchbox", { name: "Search skills" });

    fireEvent.change(search, { target: { value: "outreach" } });
    expect(within(screen.getByRole("list", { name: "Skills" })).getAllByRole("button")).toHaveLength(1);
    fireEvent.change(search, { target: { value: "nothing" } });
    expect(screen.getByText("No match")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(search).toHaveValue("");
  });

  it("creates a skill, turning the typed name into its command", async () => {
    await openPage();
    fireEvent.click(screen.getByRole("button", { name: "New skill" }));

    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    fireEvent.change(field("Skill name"), { target: { value: "Intro Email!" } });
    expect(field("Skill name")).toHaveValue("intro-email");
    fireEvent.change(field("Description"), { target: { value: "Draft a warm first email." } });
    fireEvent.change(field("Use when"), { target: { value: "The director asks for a first email." } });
    fireEvent.change(field("Instructions"), { target: { value: "Open with the shared link." } });
    fireEvent.click(save);

    const detail = await screen.findByRole("article");
    expect(api.createSkill).toHaveBeenCalledWith({
      name: "intro-email",
      purpose: "Draft a warm first email.",
      useWhen: "The director asks for a first email.",
      instructions: "Open with the shared link.",
    });
    expect(within(detail).getByText("/intro-email")).toBeInTheDocument();
    expect(within(detail).getByText("Open with the shared link.")).toBeInTheDocument();
    expect(screen.getByText("3")).toHaveClass("sm-count");
  });

  it("keeps every field when a save is refused and shows the reason", async () => {
    api.createSkill.mockRejectedValueOnce(new Error("A skill named outreach-brief already exists."));
    await openPage();
    fireEvent.click(screen.getByRole("button", { name: "New skill" }));
    fireEvent.change(field("Skill name"), { target: { value: "outreach-brief" } });
    fireEvent.change(field("Description"), { target: { value: "Again." } });
    fireEvent.change(field("Instructions"), { target: { value: "Step one." } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "A skill named outreach-brief already exists.",
    );
    expect(field("Skill name")).toHaveValue("outreach-brief");
    expect(field("Instructions")).toHaveValue("Step one.");
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  });

  it("edits a skill in place and shows the saved instructions", async () => {
    await openPage();
    fireEvent.click(screen.getByRole("button", { name: /Outreach brief/ }));
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));

    expect(screen.getByRole("form", { name: "Edit /outreach-brief" })).toBeInTheDocument();
    expect(field("Description")).toHaveValue("Prepare a source-backed outreach brief.");
    fireEvent.change(field("Instructions"), { target: { value: "- Lead with the launch." } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    const detail = await screen.findByRole("article");
    expect(api.updateSkill).toHaveBeenCalledWith(
      expect.objectContaining({ name: "outreach-brief", instructions: "- Lead with the launch." }),
    );
    expect(within(detail).getByText("Lead with the launch.")).toBeInTheDocument();
  });

  it("asks before deleting and can be cancelled", async () => {
    await openPage();
    fireEvent.click(screen.getByRole("button", { name: /Outreach brief/ }));

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(screen.getByText("Delete /outreach-brief?")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(api.deleteSkill).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(api.deleteSkill).toHaveBeenCalledWith("outreach-brief"));
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /Outreach brief/ })).not.toBeInTheDocument(),
    );
  });

  it("marks an edited built-in skill and offers Reset instead of Delete", async () => {
    catalog = [skill({ source: "workspace", builtinDefault: true })];
    await openPage();

    expect(screen.getByText("Edited")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Reset" }));
    expect(screen.getByText("Reset /weekly-sourcing?")).toBeInTheDocument();
  });

  it("shows a failed load and retries", async () => {
    api.getSkills.mockRejectedValueOnce(new Error("skills 500"));
    render(<SkillsPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Skills didn’t load.");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("list", { name: "Skills" })).toBeInTheDocument();
  });

  it("starts an empty library from one New skill button", async () => {
    catalog = [];
    render(<SkillsPage />);

    expect(await screen.findByText("No skills yet")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "New skill" })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "New skill" }));
    expect(field("Skill name")).toHaveFocus();
  });
});

describe("a saved skill in chat", () => {
  it("appears in the composer's slash menu", async () => {
    api.getSessions.mockResolvedValue({ sessions: [], open_id: "thread-alpha" });
    api.getPersona.mockResolvedValue({ id: "sourcing", name: "Sourcing Operator", tools: [] });
    api.getCurrentRunTelemetry.mockResolvedValue({
      version: 1,
      session_id: "thread-alpha",
      current_run: null,
    });
    api.getBoard.mockResolvedValue({ backlog: [], open: [], in_conversation: [], done: [] });
    api.getAttachmentLimits.mockResolvedValue({
      max_files: 5,
      max_bytes: 1024,
      max_text_chars: 1000,
      images: true,
      accept: ".pdf",
      types: ["PDF"],
    });
    api.openChat.mockReturnValue({
      send: vi.fn(),
      approve: vi.fn(),
      cancel: vi.fn(),
      queue: vi.fn(),
      recover: vi.fn(),
      close: vi.fn(),
    });
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Alpha",
      messages: [],
      events: [],
    });
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: vi.fn(),
    });

    const page = render(<SkillsPage />);
    await screen.findByRole("list", { name: "Skills" });
    fireEvent.click(screen.getByRole("button", { name: "New skill" }));
    fireEvent.change(field("Skill name"), { target: { value: "intro-email" } });
    fireEvent.change(field("Description"), { target: { value: "Draft a warm first email." } });
    fireEvent.change(field("Instructions"), { target: { value: "Open with the shared link." } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByRole("article");
    page.unmount();

    render(<ChatPage sessionId="thread-alpha" />);
    await screen.findByRole("heading", { level: 1, name: "Alpha" });
    await act(async () => {});
    const box = screen.getByRole("textbox", { name: "Message Sourcecado" });
    fireEvent.change(box, { target: { value: "/intro" } });

    const menu = await screen.findByRole("listbox", { name: "Skills" });
    const option = within(menu).getByRole("option");
    expect(option).toHaveTextContent("/intro-email");
    expect(option).toHaveTextContent("Draft a warm first email.");
    fireEvent.keyDown(box, { key: "Enter" });
    expect(box).toHaveValue("/intro-email ");
  });
});
