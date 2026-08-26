// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { Button } from "@/components/ui";

describe("Button", () => {
  it("renders a primary button with the accent token by default", () => {
    render(<Button>Create draft</Button>);
    const btn = screen.getByRole("button", { name: "Create draft" });
    expect(btn).toBeInTheDocument();
    expect(btn.className).toContain("bg-accent");
  });

  it("renders a ghost variant with a border token, not the accent fill", () => {
    render(<Button variant="ghost">Cancel</Button>);
    const btn = screen.getByRole("button", { name: "Cancel" });
    expect(btn.className).toContain("border-border");
    expect(btn.className).not.toContain("bg-accent");
  });
});
