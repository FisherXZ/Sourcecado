import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SkillsPage } from "../src/routes/SkillsPage";

const api = vi.hoisted(() => ({
  getSkills: vi.fn(),
}));

vi.mock("../src/api", () => ({
  getSkills: api.getSkills,
}));

describe("SkillsPage", () => {
  beforeEach(() => {
    api.getSkills.mockReset();
  });

  it("announces that the catalog is loading", () => {
    api.getSkills.mockReturnValue(new Promise(() => {}));

    render(<SkillsPage />);

    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("status")).toHaveTextContent("Loading skills");
  });

  it("renders human skill names and descriptions without manifest internals", async () => {
    api.getSkills.mockResolvedValue({
      skills: [
        {
          name: "weekly-sourcing",
          description: "Weekly check-in playbook for who to work next and why-now.",
          path: "/Users/operator/private/weekly-sourcing/SKILL.md",
          instructions: "RAW_MANIFEST_SECRET",
        },
        {
          name: "outreach-brief",
          description: "Builds a review-ready outreach brief.",
          path: "/tmp/outreach-brief/SKILL.md",
          instructions: "DO_NOT_RENDER_THIS_BODY",
        },
      ],
    });

    const { container } = render(<SkillsPage />);

    expect(await screen.findByRole("heading", { level: 2, name: "Weekly sourcing" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Outreach brief" })).toBeInTheDocument();
    expect(screen.getByText("Builds a review-ready outreach brief.")).toBeInTheDocument();
    expect(container).not.toHaveTextContent("/Users/operator/private");
    expect(container).not.toHaveTextContent("RAW_MANIFEST_SECRET");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("explains when no skills are available", async () => {
    api.getSkills.mockResolvedValue({ skills: [] });

    render(<SkillsPage />);

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("No skills available"));
    expect(screen.getByText("Installed skills will appear here when they’re ready to use.")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Available skills" })).not.toBeInTheDocument();
  });

  it("shows a contextual failure without leaking details and retries the catalog", async () => {
    api.getSkills
      .mockRejectedValueOnce(new Error("skills 500 sk-live-secret /Users/private/SKILL.md"))
      .mockResolvedValueOnce({
        skills: [{ name: "weekly-sourcing", description: "Plan the weekly sourcing loop." }],
      });

    const { container } = render(<SkillsPage />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The skills catalog couldn’t be loaded");
    expect(container).not.toHaveTextContent("sk-live-secret");
    expect(container).not.toHaveTextContent("/Users/private");

    fireEvent.click(within(alert).getByRole("button", { name: "Retry loading skills" }));

    expect(await screen.findByRole("heading", { level: 2, name: "Weekly sourcing" })).toBeInTheDocument();
    expect(api.getSkills).toHaveBeenCalledTimes(2);
  });
});
