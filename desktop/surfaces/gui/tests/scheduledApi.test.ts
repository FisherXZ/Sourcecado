import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ScheduleApiError,
  createScheduleJob,
  getSchedule,
  runScheduleJob,
} from "../src/api";

const fetchMock = vi.fn();

function response(body: unknown, options: { ok?: boolean; status?: number } = {}): Response {
  return {
    ok: options.ok ?? true,
    status: options.status ?? 200,
    json: vi.fn().mockResolvedValue(body),
  } as unknown as Response;
}

describe("schedule API boundary", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    window.__CLUB_HTTP__ = "http://sidecar.test";
    window.__CLUB_API_TOKEN__ = "review-token";
  });

  it("normalizes durable jobs, receipts, templates, and drops unknown payload fields", async () => {
    fetchMock.mockResolvedValue(
      response({
        jobs: [
          {
            id: 4,
            name: "Weekly priority review",
            template_id: "weekly_sourcing_review",
            cadence: "weekly_monday_0900",
            cron: "0 9 * * 1",
            prompt: "Review priority sourcing work.",
            created_at: "2026-08-25T10:00:00Z",
            next_run_at: "2026-08-31T09:00:00-07:00",
            secret: "job-secret-never-render",
          },
        ],
        runs: [
          {
            id: 9,
            job_id: 4,
            status: "waiting_approval",
            result: "Draft review is waiting.",
            summary: "One approval needs review.",
            created_at: "2026-08-25T10:01:00Z",
            started_at: "2026-08-25T10:01:00Z",
            finished_at: "2026-08-25T10:01:02Z",
            duration_ms: 2100,
            session_id: "sched-4",
            waiting_approval_count: 1,
            artifacts: [
              {
                id: "shortlist-1",
                artifact_type: "shortlist",
                title: "Priority shortlist",
                external_url: "https://example.test/shortlist",
                raw_payload: "artifact-secret-never-render",
              },
              {
                id: "unsafe-artifact",
                artifact_type: "link",
                title: "Unsafe artifact link",
                external_url: "javascript:alert('unsafe')",
              },
            ],
            token: "run-secret-never-render",
          },
        ],
        templates: [
          {
            id: "weekly_sourcing_review",
            name: "Weekly sourcing review",
            description: "Review priorities and next sourcing work.",
            cadences: ["weekly_monday_0900"],
            default_prompt: "Review priority sourcing work.",
          },
        ],
      }),
    );

    const schedule = await getSchedule();

    expect(schedule.jobs[0]).toEqual({
      id: 4,
      name: "Weekly priority review",
      templateId: "weekly_sourcing_review",
      cadence: "weekly_monday_0900",
      cron: "0 9 * * 1",
      prompt: "Review priority sourcing work.",
      createdAt: "2026-08-25T10:00:00Z",
      nextRunAt: "2026-08-31T09:00:00-07:00",
    });
    expect(schedule.runs[0]).toEqual({
      id: 9,
      jobId: 4,
      status: "waiting_approval",
      result: "Draft review is waiting.",
      summary: "One approval needs review.",
      createdAt: "2026-08-25T10:01:00Z",
      startedAt: "2026-08-25T10:01:00Z",
      finishedAt: "2026-08-25T10:01:02Z",
      durationMs: 2100,
      sessionId: "sched-4",
      waitingApprovalCount: 1,
      artifacts: [
        {
          id: "shortlist-1",
          artifactType: "shortlist",
          title: "Priority shortlist",
          externalUrl: "https://example.test/shortlist",
        },
        {
          id: "unsafe-artifact",
          artifactType: "link",
          title: "Unsafe artifact link",
          externalUrl: null,
        },
      ],
    });
    expect(schedule.templates[0].defaultPrompt).toBe("Review priority sourcing work.");
    expect(JSON.stringify(schedule)).not.toContain("secret-never-render");
  });

  it("marks a genuinely unrecognized run status as unknown instead of coercing it to failed", async () => {
    fetchMock.mockResolvedValue(
      response({
        jobs: [],
        runs: [
          {
            id: 11,
            job_id: 4,
            status: "some_future_status_this_client_has_never_seen",
            result: "",
            summary: "",
            created_at: "2026-08-25T10:01:00Z",
            started_at: "2026-08-25T10:01:00Z",
            finished_at: "2026-08-25T10:01:02Z",
            duration_ms: 500,
            session_id: "sched-4",
            waiting_approval_count: 0,
            artifacts: [],
          },
        ],
        templates: [],
      }),
    );

    const schedule = await getSchedule();

    expect(schedule.runs[0].status).toBe("unknown");
  });

  it("creates a routine through the schedule endpoint", async () => {
    fetchMock.mockResolvedValue(
      response({
        job: {
          id: 2,
          name: "Weekly review",
          template_id: "weekly_sourcing_review",
          cadence: "weekly_monday_0900",
          cron: "0 9 * * 1",
          prompt: "Review priorities.",
          created_at: "2026-08-25T10:00:00Z",
          next_run_at: "2026-08-31T09:00:00-07:00",
        },
      }),
    );

    const job = await createScheduleJob({
      templateId: "weekly_sourcing_review",
      cadence: "weekly_monday_0900",
      name: "Weekly review",
      prompt: "Review priorities.",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://sidecar.test/v1/schedule",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          template_id: "weekly_sourcing_review",
          cadence: "weekly_monday_0900",
          name: "Weekly review",
          prompt: "Review priorities.",
        }),
      }),
    );
    expect(job.id).toBe(2);
    expect(job.nextRunAt).toBe("2026-08-31T09:00:00-07:00");
  });

  it("returns safe field validation without exposing server details", async () => {
    fetchMock.mockResolvedValue(
      response(
        {
          error: "invalid_routine",
          fields: { prompt: "Enter instructions up to 2,000 characters." },
          detail: "token=server-secret /private/state",
        },
        { ok: false, status: 400 },
      ),
    );

    const error = await createScheduleJob({
      templateId: "weekly_sourcing_review",
      cadence: "weekly_monday_0900",
      name: "Weekly review",
      prompt: "",
    }).catch((reason) => reason);

    expect(error).toBeInstanceOf(ScheduleApiError);
    expect(error.code).toBe("invalid_routine");
    expect(error.fieldErrors).toEqual({
      prompt: "Enter instructions up to 2,000 characters.",
    });
    expect(String(error)).not.toContain("server-secret");
    expect(String(error)).not.toContain("/private/state");
  });

  it("classifies an already-running response for contextual UI recovery", async () => {
    fetchMock.mockResolvedValue(
      response(
        {
          error: "already_running",
          message: "This routine is already running. Wait for its current receipt.",
        },
        { ok: false, status: 409 },
      ),
    );

    const error = await runScheduleJob(4).catch((reason) => reason);

    expect(error).toBeInstanceOf(ScheduleApiError);
    expect(error.code).toBe("already_running");
    expect(error.message).toBe("This routine is already running. Wait for its current receipt.");
  });
});
