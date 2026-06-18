// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { AppShell } from "@/components/ui";

describe("AppShell", () => {
  const nav = [
    { label: "Research Chat", href: "/chat" },
    { label: "Run Ledger", href: "/runs" },
  ];

  it("renders the brand, nav items, and the center children", () => {
    render(
      <AppShell nav={nav} activeHref="/chat">
        <div>center content</div>
      </AppShell>,
    );
    expect(screen.getAllByText("Sourcecado").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: "Research Chat" })).toHaveAttribute("href", "/chat");
    expect(screen.getByText("center content")).toBeInTheDocument();
  });

  it("marks the active nav item with the accent-tint token", () => {
    render(
      <AppShell nav={nav} activeHref="/chat">
        <div />
      </AppShell>,
    );
    expect(screen.getByRole("link", { name: "Research Chat" }).className).toContain("bg-accent-tint");
  });

  it("renders the inspector pane only when provided", () => {
    const { rerender } = render(
      <AppShell nav={nav}>
        <div />
      </AppShell>,
    );
    expect(screen.queryByTestId("inspector")).not.toBeInTheDocument();
    rerender(
      <AppShell nav={nav} inspector={<div>panel</div>}>
        <div />
      </AppShell>,
    );
    expect(screen.getByTestId("inspector")).toBeInTheDocument();
  });
});
