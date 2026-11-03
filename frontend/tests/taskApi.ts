import { vi } from "vitest";

/** Task API stand-ins for suites that mock ../src/api but do not test tasks. */
export function taskApiMock() {
  class TaskSaveError extends Error {
    status = 0;
    code: string | null = null;
    current = null;
  }
  return {
    getPersonEnrichment: vi.fn(async (personId: string) => ({
      person: {person_id:personId,name:"Saved person",title:null,company:null,version:1,identity_revision:0},
      known:{email:null,phone:null,linkedin_url:null},match:null,credits_estimated:null,session_id:null,
      status:"not_enriched",latest:null,pending:null,can_request:false,problem:"apollo_not_configured",attempts:[],
    })),
    getCrmProposals: vi.fn(async () => ({ proposals: [], truncated: false })),
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
