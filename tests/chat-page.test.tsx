import { vi } from "vitest";

const { createSessionMock, getOrCreateLatestSessionMock, loadSessionMessagesMock, redirectMock } = vi.hoisted(() => ({
  createSessionMock: vi.fn(),
  getOrCreateLatestSessionMock: vi.fn(),
  loadSessionMessagesMock: vi.fn(),
  redirectMock: vi.fn(() => {
    throw new Error("NEXT_REDIRECT");
  }),
}));
vi.mock("@/lib/chat/sessions", () => ({
  createSession: createSessionMock,
  getOrCreateLatestSession: getOrCreateLatestSessionMock,
  loadSessionMessages: loadSessionMessagesMock,
}));
vi.mock("@/lib/db", () => ({ getDb: vi.fn().mockReturnValue({}) }));
vi.mock("next/navigation", () => ({ redirect: redirectMock }));

import ChatPage from "@/app/chat/page";
import { ChatClient } from "@/app/chat/ChatClient";

describe("ChatPage", () => {
  beforeEach(() => {
    createSessionMock.mockReset();
    getOrCreateLatestSessionMock.mockReset();
    loadSessionMessagesMock.mockReset();
    redirectMock.mockClear();
  });

  it("resumes the latest session and passes its mapped history into ChatClient", async () => {
    getOrCreateLatestSessionMock.mockResolvedValue({ id: 5 });
    loadSessionMessagesMock.mockResolvedValue([
      { role: "user", content: "hi" },
      { role: "assistant", content: [{ type: "text", text: "hello" }] },
    ]);

    const element = await ChatPage({ searchParams: Promise.resolve({}) });
    expect(getOrCreateLatestSessionMock).toHaveBeenCalled();
    expect(createSessionMock).not.toHaveBeenCalled();

    const clientElement = findElementOfType(element, ChatClient);
    expect(clientElement?.props.initialExchanges).toEqual([{ question: "hi", answer: "hello" }]);
  });

  it("with ?new=1, creates a fresh session and redirects to /chat", async () => {
    createSessionMock.mockResolvedValue({ id: 9 });

    await expect(ChatPage({ searchParams: Promise.resolve({ new: "1" }) })).rejects.toThrow("NEXT_REDIRECT");
    expect(createSessionMock).toHaveBeenCalled();
    expect(getOrCreateLatestSessionMock).not.toHaveBeenCalled();
    expect(redirectMock).toHaveBeenCalledWith("/chat");
  });
});

// React elements expose their tree via .props.children; walk it to find a
// node whose type matches the given component.
function findElementOfType(node: unknown, type: unknown): { props: Record<string, unknown> } | null {
  if (node == null || typeof node !== "object") return null;
  const el = node as { type?: unknown; props?: { children?: unknown } };
  if (el.type === type) return el as { props: Record<string, unknown> };
  const children = el.props?.children;
  if (Array.isArray(children)) {
    for (const child of children) {
      const found = findElementOfType(child, type);
      if (found) return found;
    }
  } else if (children) {
    return findElementOfType(children, type);
  }
  return null;
}
