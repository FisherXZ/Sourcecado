import { vi } from "vitest";

/** Task API stand-ins for suites that mock ../src/api but do not test tasks. */
export function taskApiMock() {
  class TaskSaveError extends Error {
    status = 0;
    code: string | null = null;
    current = null;
  }
  return {
    listTasks: vi.fn(async () => ({
      tasks: [],
      truncated: false,
      timezone: "America/Los_Angeles",
      today: "2026-10-12",
    })),
    createTask: vi.fn(),
    updateTask: vi.fn(),
    newOperationId: vi.fn(() => "ui-test-operation"),
    TaskSaveError,
  };
}
