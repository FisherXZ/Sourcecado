import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GlobalRail } from "../src/app/GlobalRail";
import type { AppRoute } from "../src/app/route";

const route: AppRoute = { kind: "skills" };

function renderRail(open: boolean, memoryReviewCount = 0) {
  return render(
    <GlobalRail
      open={open}
      route={route}
      sessions={[]}
      scheduledApprovalCount={0}
      memoryReviewCount={memoryReviewCount}
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
        route={route}
        sessions={[]}
        scheduledApprovalCount={0}
        memoryReviewCount={0}
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
