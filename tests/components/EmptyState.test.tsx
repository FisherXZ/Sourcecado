// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { EmptyState, Button } from "@/components/ui";

describe("EmptyState", () => {
  it("renders title, description, and an optional action", () => {
    render(
      <EmptyState
        title="No contacts yet"
        description="Run a routine to pull candidates."
        action={<Button>Run routine</Button>}
      />,
    );
    expect(screen.getByText("No contacts yet")).toBeInTheDocument();
    expect(screen.getByText("Run a routine to pull candidates.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run routine" })).toBeInTheDocument();
  });
});
