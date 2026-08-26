// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Toggle } from "@/components/ui";

describe("Toggle", () => {
  it("fires onToggle when clicked", () => {
    let count = 0;
    render(<Toggle on={false} onToggle={() => (count += 1)} label="Auto-enrich" />);
    fireEvent.click(screen.getByRole("switch"));
    expect(count).toBe(1);
  });

  it("reflects the on state via aria-checked", () => {
    render(<Toggle on={true} label="Auto-enrich" />);
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "true");
  });
});
