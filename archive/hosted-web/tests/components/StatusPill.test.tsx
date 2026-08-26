// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { StatusPill } from "@/components/ui";

describe("StatusPill", () => {
  it("maps the 'go' tone to the accent-tint token", () => {
    render(<StatusPill tone="go">Sourced</StatusPill>);
    const pill = screen.getByText("Sourced");
    expect(pill.className).toContain("bg-accent-tint");
    expect(pill.className).toContain("text-accent-deep");
  });

  it("maps the 'error' tone to the negative tokens", () => {
    render(<StatusPill tone="error">Failed</StatusPill>);
    expect(screen.getByText("Failed").className).toContain("bg-neg-bg");
  });
});
