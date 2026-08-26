import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  Inspector,
  InspectorProvider,
  useInspector,
} from "../src/chat/Inspector";

function Harness() {
  const { select } = useInspector();
  return (
    <>
      <button
        type="button"
        onClick={(event) =>
          select(
            { kind: "source", id: "loading", title: "Loading source", status: "loading" },
            event.currentTarget,
          )
        }
      >
        Loading target
      </button>
      <button
        type="button"
        onClick={(event) =>
          select(
            {
              kind: "artifact",
              id: "error",
              title: "Failed artifact",
              status: "error",
              errorSummary: "The artifact couldn’t be loaded.",
              externalUrl: "javascript:alert(1)",
              retry: vi.fn(),
            },
            event.currentTarget,
          )
        }
      >
        Failed target
      </button>
      <button
        type="button"
        onClick={(event) =>
          select(
            {
              kind: "source",
              id: "cached",
              title: "Cached evidence",
              status: "success",
              provider: "Legacy source",
              externalUrl: "https://example.com/evidence",
              stale: true,
              truncated: true,
            },
            event.currentTarget,
          )
        }
      >
        Cached target
      </button>
      <Inspector />
    </>
  );
}

describe("Inspector", () => {
  it("renders loading error stale truncated and unsafe-link states without stale detail", () => {
    render(
      <InspectorProvider threadId="thread-alpha">
        <Harness />
      </InspectorProvider>,
    );

    expect(screen.queryByRole("complementary", { name: "Inspector" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Loading target" }));
    expect(screen.getByLabelText("Loading detail")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Failed target" }));
    const inspector = screen.getByRole("complementary", { name: "Inspector" });
    expect(within(inspector).getByRole("alert")).toHaveTextContent(
      "artifact couldn’t be loaded",
    );
    expect(within(inspector).queryByRole("link", { name: "Open externally" })).not.toBeInTheDocument();
    expect(within(inspector).getByText("External URL unavailable")).toBeInTheDocument();
    expect(within(inspector).queryByText("Loading source")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Cached target" }));
    expect(within(inspector).getByRole("heading", { level: 2 })).toHaveTextContent(
      "Cached evidence",
    );
    expect(within(inspector).getByText("Cached stale")).toBeInTheDocument();
    expect(within(inspector).getByText("Truncated")).toBeInTheDocument();
    expect(within(inspector).getByRole("link", { name: "Open externally" })).toHaveAttribute(
      "target",
      "_blank",
    );
    expect(within(inspector).getByRole("link", { name: "Open externally" })).toHaveAttribute(
      "rel",
      "noopener noreferrer",
    );
  });

  it("Escape closes selection and returns focus, while a thread switch clears it", async () => {
    const view = render(
      <InspectorProvider threadId="thread-alpha">
        <Harness />
      </InspectorProvider>,
    );
    const trigger = screen.getByRole("button", { name: "Cached target" });
    fireEvent.click(trigger);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("complementary", { name: "Inspector" })).not.toBeInTheDocument();
    await waitFor(() => expect(trigger).toHaveFocus());

    fireEvent.click(trigger);
    view.rerender(
      <InspectorProvider threadId="thread-beta">
        <Harness />
      </InspectorProvider>,
    );
    await waitFor(() =>
      expect(screen.queryByRole("complementary", { name: "Inspector" })).not.toBeInTheDocument(),
    );
  });
});
