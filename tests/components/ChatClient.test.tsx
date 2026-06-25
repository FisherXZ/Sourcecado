// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ChatClient } from "@/app/chat/ChatClient";

describe("ChatClient", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the question input and run button", () => {
    render(<ChatClient />);
    expect(screen.getByLabelText("Question")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /run/i })).toBeInTheDocument();
  });

  it("shows an accessible alert when the request fails", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      json: async () => ({ error: "DB connection failed" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ChatClient />);
    fireEvent.change(screen.getByLabelText("Question"), { target: { value: "will fail" } });
    fireEvent.click(screen.getByRole("button", { name: /run/i }));

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("alert")).toHaveTextContent("DB connection failed");
  });

  it("posts the question and shows the run result with a trace link", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      json: async () => ({ runId: 42, status: "succeeded", answer: "Echoed hello", steps: 2 }),
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ChatClient />);
    fireEvent.change(screen.getByLabelText("Question"), { target: { value: "echo hello" } });
    fireEvent.click(screen.getByRole("button", { name: /run/i }));

    await waitFor(() => expect(screen.getByText(/Run #42/)).toBeInTheDocument());
    expect(screen.getByText("Echoed hello")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /view trace/i })).toHaveAttribute("href", "/runs/42");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/agent",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
