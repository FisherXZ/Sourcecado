import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { getAfterSendReminder, saveAfterSendReminder } from "../src/api";

beforeEach(() => {
  window.__CLUB_HTTP__ = "http://backend.test";
  window.__CLUB_API_TOKEN__ = "reminder-test-token";
});
afterEach(() => vi.unstubAllGlobals());

it("loads reminder context for the exact person and confirmed message", async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ timezone: "America/Los_Angeles", operation_id: "saved-op", task: null }) });
  vi.stubGlobal("fetch", fetchMock);
  await getAfterSendReminder("person one", "message one");
  expect(fetchMock.mock.calls[0][0]).toBe("http://backend.test/v1/people/person%20one/sent-messages/message%20one/reminder");
  expect(fetchMock.mock.calls[0][1].headers.get("X-Club-Token")).toBe("reminder-test-token");
});

it("saves only the chosen reminder fields and stable operation, with no send request", async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ task: { task_id: "task_saved" } }) });
  vi.stubGlobal("fetch", fetchMock);
  await saveAfterSendReminder("person one", "message one", {
    title: "Ask about the deck", dueDate: "2026-11-06", operationId: "saved-op", timezone: "America/Los_Angeles",
  });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(fetchMock).toHaveBeenCalledWith("http://backend.test/v1/people/person%20one/sent-messages/message%20one/reminder", expect.objectContaining({
    method: "POST", body: JSON.stringify({ title: "Ask about the deck", due_date: "2026-11-06", operation_id: "saved-op", timezone: "America/Los_Angeles" }),
  }));
});
