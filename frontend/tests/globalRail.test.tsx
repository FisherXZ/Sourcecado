import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GlobalRail } from "../src/app/GlobalRail";
import type { AppRoute } from "../src/app/route";

const route: AppRoute = { kind: "skills" };

const noOperator = null;

function renderRail(open: boolean, memoryReviewCount = 0) {
  return render(
    <GlobalRail
      open={open}
      collapsed={false}
      operator={noOperator}
      route={route}
      sessions={[]}
      scheduledApprovalCount={0}
      memoryReviewCount={memoryReviewCount}
      heldEffectCount={0}
      onToggleCollapsed={vi.fn()}
      onNewChat={vi.fn()}
      onOpenSession={vi.fn()}
      onPin={vi.fn()}
      onRename={vi.fn().mockResolvedValue(undefined)}
      onSearch={vi.fn()}
      onClose={vi.fn()}
    />,
  );
}

function stubMatchMedia(narrow: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query.includes("max-width") && narrow,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
}

describe("GlobalRail off-screen focus containment", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("marks the closed edge sheet inert at narrow widths, matching CSS's 1179px breakpoint", () => {
    stubMatchMedia(true);
    renderRail(false);

    expect(document.getElementById("app-rail")).toHaveAttribute("inert");
  });

  it("clears inert once the sheet opens at narrow widths", () => {
    stubMatchMedia(true);
    const { rerender } = renderRail(false);
    expect(document.getElementById("app-rail")).toHaveAttribute("inert");

    rerender(
      <GlobalRail
        open
        collapsed={false}
        operator={noOperator}
        route={route}
        sessions={[]}
        scheduledApprovalCount={0}
        memoryReviewCount={0}
        heldEffectCount={0}
        onToggleCollapsed={vi.fn()}
        onNewChat={vi.fn()}
        onOpenSession={vi.fn()}
        onPin={vi.fn()}
        onRename={vi.fn().mockResolvedValue(undefined)}
        onSearch={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(document.getElementById("app-rail")).not.toHaveAttribute("inert");
  });

  it("never inerts the always-visible static sidebar at wide widths, even while `open` is false", () => {
    stubMatchMedia(false);
    renderRail(false);

    expect(document.getElementById("app-rail")).not.toHaveAttribute("inert");
  });

  it("defaults to focusable when matchMedia is unavailable in the environment", () => {
    // No stub: mirrors jsdom's default (no window.matchMedia at all). The
    // rail must not silently disable itself when it cannot tell the
    // viewport width.
    renderRail(false);

    expect(document.getElementById("app-rail")).not.toHaveAttribute("inert");
  });
});

describe("GlobalRail saved-memory backlog", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows how many saved memories are waiting for review", () => {
    renderRail(false, 12);

    const badge = screen.getByLabelText("Saved memory: 12 waiting for review");
    expect(badge).toHaveTextContent("12");
    expect(screen.getByRole("link", { name: "Memory" })).toHaveAttribute("href", "#/memory");
  });

  it("shows no badge once the backlog is drained", () => {
    renderRail(false, 0);

    expect(screen.queryByLabelText(/waiting for review/)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Memory" })).toBeInTheDocument();
  });
});

function renderCollapsible(props: { collapsed: boolean; open?: boolean }) {
  const onToggleCollapsed = vi.fn();
  render(
    <GlobalRail
      open={props.open ?? false}
      collapsed={props.collapsed}
      operator={{ email: "dana@codeology.club", persona: "Research Analyst" }}
      route={route}
      sessions={[
        { session_id: "alpha", title: "Alpha", n_msgs: 1, pinned: false, opened_at: "1", updated_at: "1" },
      ]}
      scheduledApprovalCount={0}
      memoryReviewCount={0}
      heldEffectCount={0}
      onToggleCollapsed={onToggleCollapsed}
      onNewChat={vi.fn()}
      onOpenSession={vi.fn()}
      onPin={vi.fn()}
      onRename={vi.fn().mockResolvedValue(undefined)}
      onSearch={vi.fn()}
      onClose={vi.fn()}
    />,
  );
  return { onToggleCollapsed };
}

describe("GlobalRail collapse", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("collapses to an icon column that names every control with a tooltip", () => {
    stubMatchMedia(false);
    renderCollapsible({ collapsed: true });

    const rail = document.getElementById("app-rail")!;
    expect(rail).toHaveClass("is-collapsed");
    const nav = within(rail).getByRole("navigation", { name: "Sourcecado" });
    for (const name of ["Contacts", "Scheduled", "Connections", "Skills", "Memory", "Inbox", "Settings"]) {
      expect(within(nav).getByRole("link", { name })).toHaveAttribute("data-tooltip", name);
    }
    expect(within(nav).getByRole("button", { name: "New chat" })).toHaveAttribute("data-tooltip", "New chat");
    expect(within(nav).getByRole("button", { name: "Expand sidebar" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("keeps the expanded rail free of tooltips and offers the collapse control", () => {
    stubMatchMedia(false);
    const { onToggleCollapsed } = renderCollapsible({ collapsed: false });

    const rail = document.getElementById("app-rail")!;
    expect(rail).not.toHaveClass("is-collapsed");
    expect(rail.querySelector("[data-tooltip]")).toBeNull();
    const toggle = within(rail).getByRole("button", { name: "Collapse sidebar" });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(toggle);
    expect(onToggleCollapsed).toHaveBeenCalledTimes(1);
  });

  it("ignores the collapsed choice in the narrow edge sheet and hides the toggle", () => {
    stubMatchMedia(true);
    renderCollapsible({ collapsed: true, open: true });

    const rail = document.getElementById("app-rail")!;
    expect(rail).not.toHaveClass("is-collapsed");
    expect(within(rail).queryByRole("button", { name: /sidebar/ })).not.toBeInTheDocument();
    expect(within(rail).getByRole("link", { name: "Contacts" })).not.toHaveAttribute("data-tooltip");
  });

  it("keeps Tab inside the open edge sheet", () => {
    stubMatchMedia(true);
    renderCollapsible({ collapsed: false, open: true });

    const rail = document.getElementById("app-rail")!;
    const close = within(rail).getByRole("button", { name: "Close navigation" });
    const settings = within(rail).getByRole("link", { name: "Settings" });
    settings.focus();
    fireEvent.keyDown(settings, { key: "Tab" });
    expect(close).toHaveFocus();
    fireEvent.keyDown(close, { key: "Tab", shiftKey: true });
    expect(settings).toHaveFocus();
  });
});

describe("GlobalRail footer", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("names the operator by their connected account and shows the persona", () => {
    stubMatchMedia(false);
    renderCollapsible({ collapsed: false });

    const footer = screen.getByRole("group", { name: "Signed-in operator" });
    expect(footer).toHaveTextContent("dana@codeology.club");
    expect(footer).toHaveTextContent("Research Analyst");
    expect(footer).not.toHaveTextContent("Sourcing Director");
  });

  it("shows no persona line until settings load", () => {
    stubMatchMedia(false);
    renderRail(false);

    const footer = screen.getByRole("group", { name: "Signed-in operator" });
    expect(footer).toHaveTextContent("Operator");
    expect(footer).not.toHaveTextContent("Sourcing Director");
    expect(footer.querySelector(".operator-persona")).toBeNull();
  });
});

describe("GlobalRail count tooltips", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("puts the exact count in the icon column tooltip and the description, never a dot-only signal", () => {
    stubMatchMedia(false);
    render(
      <GlobalRail
        open={false}
        collapsed
        operator={null}
        route={route}
        sessions={[]}
        scheduledApprovalCount={0}
        memoryReviewCount={1003}
        heldEffectCount={0}
        onToggleCollapsed={vi.fn()}
        onNewChat={vi.fn()}
        onOpenSession={vi.fn()}
        onPin={vi.fn()}
        onRename={vi.fn().mockResolvedValue(undefined)}
        onSearch={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    const memory = screen.getByRole("link", { name: "Memory" });
    expect(memory).toHaveAttribute("data-tooltip", "Memory (1003)");
    expect(memory).toHaveAccessibleDescription("Saved memory: 1003 waiting for review");
    expect(screen.getByRole("link", { name: "Skills" })).toHaveAttribute("data-tooltip", "Skills");
  });
});
