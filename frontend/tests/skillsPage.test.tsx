import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SkillsPage } from "../src/routes/SkillsPage";

const api = vi.hoisted(() => ({
  getSkills: vi.fn(),
}));

vi.mock("../src/api", () => ({
  getSkills: api.getSkills,
}));

const skills = [
  {
    name: "weekly-sourcing",
    purpose: "Builds a compact shortlist from active Person Files, current sequence state, source-backed why-now evidence, and known knowledge gaps.",
    useWhen: "The director asks who to work next or requests a weekly sourcing check-in.",
    source: "builtin" as const,
    status: "ready" as const,
    instructions: "1. Start from the director-authored Target.\n2. Treat each **Person** as the unit of work.",
  },
  {
    name: "outreach-brief",
    purpose: "Prepare a source-backed outreach brief for review.",
    useWhen: "The director asks to prepare tailored outreach for one person.",
    source: "workspace" as const,
    status: "ready" as const,
    instructions: "- Read the Person File.\n- Keep sending human-approved.",
  },
];

describe("SkillsPage", () => {
  beforeEach(() => {
    api.getSkills.mockReset();
  });

  it("renders stable catalog and detail skeletons while loading", () => {
    api.getSkills.mockReturnValue(new Promise(() => {}));

    const { container } = render(<SkillsPage />);

    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("status")).toHaveTextContent("Loading skills");
    expect(container.querySelectorAll(".skill-row-skeleton")).toHaveLength(3);
    expect(container.querySelector(".skill-detail-skeleton")).toBeInTheDocument();
  });

  it("renders a compact catalog and an inspectable selected skill", async () => {
    api.getSkills.mockResolvedValue({ skills });

    const { container } = render(<SkillsPage />);

    expect(await screen.findByRole("searchbox", { name: "Search skills" })).toBeInTheDocument();
    expect(screen.getByText("Available · 2")).toBeInTheDocument();
    const catalog = screen.getByRole("list", { name: "Available skills" });
    const weeklyRow = within(catalog).getByRole("button", { name: /Weekly sourcing/ });
    const outreachRow = within(catalog).getByRole("button", { name: /Outreach brief/ });
    expect(weeklyRow).toHaveAttribute("aria-pressed", "true");
    expect(within(weeklyRow).getByText("Ready")).toBeInTheDocument();
    expect(within(weeklyRow).getByText("Built in")).toBeInTheDocument();
    expect(outreachRow).toHaveAttribute("aria-pressed", "false");
    expect(within(outreachRow).getByText("Ready")).toBeInTheDocument();
    expect(within(outreachRow).getByText("Workspace")).toBeInTheDocument();

    const detail = screen.getByRole("region", { name: "Weekly sourcing" });
    expect(within(detail).getByText("Ready")).toBeInTheDocument();
    expect(within(detail).getByText("Built into Sourcecado")).toBeInTheDocument();
    expect(within(detail).getByRole("heading", { name: "Use when" })).toBeInTheDocument();
    expect(within(detail).getByText(skills[0].useWhen)).toBeInTheDocument();
    expect(within(detail).getByRole("heading", { name: "What it does" })).toBeInTheDocument();
    expect(within(detail).getByText(skills[0].purpose)).toBeInTheDocument();
    expect(within(detail).getByRole("heading", { name: "Instructions" })).toBeInTheDocument();
    expect(within(detail).getByText("Person").tagName).toBe("STRONG");
    expect(container).not.toHaveTextContent("SKILL.md");
  });

  it("searches names, purpose, and activation guidance and clears no results", async () => {
    api.getSkills.mockResolvedValue({ skills });

    render(<SkillsPage />);
    const search = await screen.findByRole("searchbox", { name: "Search skills" });

    fireEvent.change(search, { target: { value: "tailored outreach" } });
    expect(screen.queryByRole("button", { name: /Weekly sourcing/ })).not.toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "Available skills" })).getByRole("button", { name: /Outreach brief/ })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Outreach brief" })).toBeInTheDocument();

    fireEvent.change(search, { target: { value: "does not exist" } });
    expect(screen.getByRole("status")).toHaveTextContent("No skills match");
    fireEvent.click(screen.getByRole("button", { name: "Clear skill search" }));
    expect(search).toHaveValue("");
    expect(within(screen.getByRole("list", { name: "Available skills" })).getByRole("button", { name: /Weekly sourcing/ })).toBeInTheDocument();
  });

  it("selects skill detail through keyboard activation", async () => {
    api.getSkills.mockResolvedValue({ skills });

    render(<SkillsPage />);
    const outreach = await screen.findByRole("button", { name: /Outreach brief/ });
    outreach.focus();
    fireEvent.keyDown(outreach, { key: "Enter" });

    expect(outreach).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("region", { name: "Outreach brief" })).toBeInTheDocument();
  });

  it("shows disabled management affordances without fake behavior or teaser copy", async () => {
    api.getSkills.mockResolvedValue({ skills });

    const { container } = render(<SkillsPage />);
    const detail = await screen.findByRole("region", { name: "Weekly sourcing" });
    const edit = within(detail).getByRole("button", { name: "Edit Weekly sourcing" });
    const enabled = within(detail).getByRole("switch", { name: "Disable Weekly sourcing" });

    expect(edit).toBeDisabled();
    expect(enabled).toBeDisabled();
    expect(enabled).toHaveAttribute("aria-checked", "true");
    fireEvent.click(edit);
    fireEvent.click(enabled);
    expect(api.getSkills).toHaveBeenCalledTimes(1);
    expect(container).not.toHaveTextContent(/coming soon/i);
  });

  it("explains when no skills are available", async () => {
    api.getSkills.mockResolvedValue({ skills: [] });

    render(<SkillsPage />);

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("No skills available"));
    expect(screen.getByText("Installed skills will appear here when they’re ready to use.")).toBeInTheDocument();
    expect(screen.queryByRole("searchbox", { name: "Search skills" })).not.toBeInTheDocument();
  });

  it("shows a contextual failure without leaking details and retries the catalog", async () => {
    api.getSkills
      .mockRejectedValueOnce(new Error("skills 500 sk-live-secret /Users/private/SKILL.md"))
      .mockResolvedValueOnce({ skills: [skills[0]] });

    const { container } = render(<SkillsPage />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The skills catalog couldn’t be loaded");
    expect(container).not.toHaveTextContent("sk-live-secret");
    expect(container).not.toHaveTextContent("/Users/private");

    fireEvent.click(within(alert).getByRole("button", { name: "Retry loading skills" }));

    expect(await screen.findByRole("region", { name: "Weekly sourcing" })).toBeInTheDocument();
    expect(api.getSkills).toHaveBeenCalledTimes(2);
  });
});
