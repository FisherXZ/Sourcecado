import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ScheduleApiError,
  type Schedule,
  type ScheduleJob,
  type ScheduleRun,
} from "../src/api";
import { ScheduledPage } from "../src/routes/ScheduledPage";

const api = vi.hoisted(() => ({
  createScheduleJob: vi.fn(),
  getSchedule: vi.fn(),
  runScheduleJob: vi.fn(),
}));

vi.mock("../src/api", async () => {
  const actual = await vi.importActual<typeof import("../src/api")>("../src/api");
  return {
    ...actual,
    createScheduleJob: api.createScheduleJob,
    getSchedule: api.getSchedule,
    runScheduleJob: api.runScheduleJob,
  };
});

const template = {
  id: "weekly_sourcing_review",
  name: "Weekly sourcing review",
  description: "Review priorities and next sourcing work.",
  cadences: ["weekly_monday_0900"],
  defaultPrompt: "Review the highest-priority sourcing work for this week.",
};

const job: ScheduleJob = {
  id: 4,
  name: "Weekly priority review",
  templateId: "weekly_sourcing_review",
  cadence: "weekly_monday_0900",
  cron: "0 9 * * 1",
  prompt: "Review the highest-priority sourcing work for this week.",
  createdAt: "2026-08-25T10:00:00Z",
  nextRunAt: "2026-08-31T09:00:00-07:00",
};

function run(overrides: Partial<ScheduleRun> = {}): ScheduleRun {
  return {
    id: 9,
    jobId: 4,
    status: "success",
    result: "Priority review finished.",
    summary: "Three priority contacts are ready for review.",
    createdAt: "2026-08-25T10:01:00Z",
    startedAt: "2026-08-25T10:01:00Z",
    finishedAt: "2026-08-25T10:01:02Z",
    durationMs: 2100,
    sessionId: "sched-4",
    waitingApprovalCount: 0,
    artifacts: [],
    ...overrides,
  };
}

function schedule(overrides: Partial<Schedule> = {}): Schedule {
  return { jobs: [], runs: [], templates: [template], ...overrides };
}

describe("ScheduledPage", () => {
  beforeEach(() => {
    api.createScheduleJob.mockReset();
    api.getSchedule.mockReset();
    api.runScheduleJob.mockReset();
    window.location.hash = "#/scheduled";
  });

  it("renders stable job and receipt skeletons while loading", () => {
    api.getSchedule.mockReturnValue(new Promise(() => {}));

    const { container } = render(<ScheduledPage />);

    expect(screen.getByRole("heading", { level: 1, name: "Scheduled" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Loading automations");
    expect(container.querySelectorAll(".schedule-job-skeleton")).toHaveLength(2);
    expect(container.querySelectorAll(".schedule-receipt-skeleton")).toHaveLength(3);
  });

  it("offers a template-backed create action from the empty state", async () => {
    api.getSchedule.mockResolvedValue(schedule());

    render(<ScheduledPage />);

    expect(await screen.findByRole("heading", { name: "No automations yet" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create automation" }));

    const form = screen.getByRole("form", { name: "Create automation" });
    expect(within(form).getByRole("combobox", { name: "Template" })).toHaveValue(
      "weekly_sourcing_review",
    );
    expect(within(form).getByRole("combobox", { name: "Cadence" })).toHaveValue(
      "weekly_monday_0900",
    );
    expect(within(form).getByRole("textbox", { name: "Automation name" })).toHaveValue(
      "Weekly sourcing review",
    );
    expect(within(form).getByRole("textbox", { name: "Instructions" })).toHaveValue(
      template.defaultPrompt,
    );
  });

  it("validates the creation form accessibly before calling the API", async () => {
    api.getSchedule.mockResolvedValue(schedule());

    render(<ScheduledPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Create automation" }));
    const form = screen.getByRole("form", { name: "Create automation" });
    fireEvent.change(within(form).getByRole("textbox", { name: "Automation name" }), {
      target: { value: "" },
    });
    fireEvent.change(within(form).getByRole("textbox", { name: "Instructions" }), {
      target: { value: "" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Save automation" }));

    expect(within(form).getByText("Enter an automation name.")).toHaveAttribute("role", "alert");
    expect(within(form).getByText("Enter instructions for this automation.")).toHaveAttribute(
      "role",
      "alert",
    );
    expect(api.createScheduleJob).not.toHaveBeenCalled();
  });

  it("creates an automation end to end and shows its durable next run", async () => {
    api.getSchedule.mockResolvedValue(schedule());
    api.createScheduleJob.mockResolvedValue(job);

    render(<ScheduledPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Create automation" }));
    fireEvent.click(screen.getByRole("button", { name: "Save automation" }));

    await waitFor(() =>
      expect(api.createScheduleJob).toHaveBeenCalledWith({
        templateId: "weekly_sourcing_review",
        cadence: "weekly_monday_0900",
        name: "Weekly sourcing review",
        prompt: template.defaultPrompt,
      }),
    );
    const routine = await screen.findByRole("article", { name: "Weekly priority review" });
    expect(within(routine).getAllByText("Every Monday at 9:00 AM")).not.toHaveLength(0);
    expect(within(routine).getByText("Next run")).toBeInTheDocument();
    expect(within(routine).getByRole("time")).toHaveAttribute("datetime", job.nextRunAt);
    expect(window.location.hash).toBe("#/scheduled");
  });

  it("maps server field validation beside the creation input without leaking details", async () => {
    api.getSchedule.mockResolvedValue(schedule());
    api.createScheduleJob.mockRejectedValue(
      new ScheduleApiError("invalid_routine", "Review the highlighted routine fields.", {
        prompt: "Enter instructions up to 2,000 characters.",
      }),
    );

    const { container } = render(<ScheduledPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Create automation" }));
    fireEvent.click(screen.getByRole("button", { name: "Save automation" }));

    expect(await screen.findByText("Enter instructions up to 2,000 characters.")).toHaveAttribute(
      "role",
      "alert",
    );
    expect(container).not.toHaveTextContent("token=");
  });

  it("renders durable running, success, failed, waiting, and partial receipts", async () => {
    api.getSchedule.mockResolvedValue(
      schedule({
        jobs: [job],
        runs: [
          run({ id: 1, status: "running", finishedAt: null, summary: "Routine is running." }),
          run({ id: 2, status: "success" }),
          run({ id: 3, status: "failed", summary: "Routine failed safely." }),
          run({
            id: 4,
            status: "waiting_approval",
            summary: "A Gmail draft needs review.",
            waitingApprovalCount: 1,
          }),
          run({ id: 5, status: "partial", summary: "Two of three sources completed." }),
        ],
      }),
    );

    render(<ScheduledPage />);

    const routine = await screen.findByRole("article", { name: "Weekly priority review" });
    const receipts = within(routine).getByRole("list", { name: "Run receipts" });
    expect(within(receipts).getByText("Running")).toBeInTheDocument();
    expect(within(receipts).getByText("Completed")).toBeInTheDocument();
    expect(within(receipts).getByText("Failed")).toBeInTheDocument();
    expect(within(receipts).getByText("Waiting for approval")).toBeInTheDocument();
    expect(within(receipts).getByText("Partial")).toBeInTheDocument();
    expect(within(receipts).getByText("This run is paused—not complete.")).toBeInTheDocument();
    expect(within(receipts).getByText("1 approval waiting in Inbox")).toBeInTheDocument();
  });

  it("renders a genuinely unknown status as needs-review, not failed", async () => {
    api.getSchedule.mockResolvedValue(
      schedule({
        jobs: [job],
        runs: [run({ status: "unknown", summary: "Status could not be recognized." })],
      }),
    );

    render(<ScheduledPage />);

    const routine = await screen.findByRole("article", { name: "Weekly priority review" });
    const receipts = within(routine).getByRole("list", { name: "Run receipts" });
    expect(within(receipts).getByText("Needs review")).toBeInTheDocument();
    expect(within(receipts).queryByText("Failed")).not.toBeInTheDocument();
  });

  it("shows legacy zero-duration runs as 'Legacy run' instead of 0ms", async () => {
    api.getSchedule.mockResolvedValue(
      schedule({
        jobs: [job],
        runs: [run({ durationMs: 0, summary: "Found three priority contacts." })],
      }),
    );

    render(<ScheduledPage />);

    const routine = await screen.findByRole("article", { name: "Weekly priority review" });
    expect(within(routine).getByText("Legacy run")).toBeInTheDocument();
    expect(within(routine).queryByText("0ms")).not.toBeInTheDocument();
  });

  it("shows artifact metadata and opens the isolated scheduled thread", async () => {
    api.getSchedule.mockResolvedValue(
      schedule({
        jobs: [job],
        runs: [
          run({
            artifacts: [
              {
                id: "shortlist-1",
                artifactType: "shortlist",
                title: "Priority shortlist",
                externalUrl: "https://example.test/shortlist",
              },
            ],
          }),
        ],
      }),
    );

    render(<ScheduledPage />);

    const receipt = await screen.findByRole("listitem", { name: /Completed run/ });
    expect(within(receipt).getByRole("link", { name: "Priority shortlist" })).toHaveAttribute(
      "href",
      "https://example.test/shortlist",
    );
    expect(within(receipt).getByRole("link", { name: "Open scheduled thread" })).toHaveAttribute(
      "href",
      "#/chat/sched-4",
    );
  });

  it("keeps the next scheduled time visible while Run now is in progress", async () => {
    let resolveRun!: (value: { run: ScheduleRun }) => void;
    api.getSchedule.mockResolvedValue(schedule({ jobs: [job] }));
    api.runScheduleJob.mockReturnValue(
      new Promise<{ run: ScheduleRun }>((resolve) => {
        resolveRun = resolve;
      }),
    );

    render(<ScheduledPage />);
    const routine = await screen.findByRole("article", { name: "Weekly priority review" });
    fireEvent.click(within(routine).getByRole("button", { name: "Run Weekly priority review now" }));

    expect(within(routine).getByRole("button", { name: "Running Weekly priority review" })).toBeDisabled();
    expect(within(routine).getByRole("time")).toHaveAttribute("datetime", job.nextRunAt);
    expect(within(routine).getByRole("status")).toHaveTextContent("Running now");

    resolveRun({ run: run({ id: 10 }) });
    expect(await within(routine).findByText("Completed")).toBeInTheDocument();
    expect(within(routine).getByRole("button", { name: "Run Weekly priority review now" })).toBeEnabled();
    expect(window.location.hash).toBe("#/scheduled");
  });

  it("keeps an already-running conflict contextual and retryable", async () => {
    api.getSchedule.mockResolvedValue(schedule({ jobs: [job] }));
    api.runScheduleJob.mockRejectedValue(
      new ScheduleApiError(
        "already_running",
        "This routine is already running. Wait for its current receipt.",
      ),
    );

    render(<ScheduledPage />);
    const routine = await screen.findByRole("article", { name: "Weekly priority review" });
    fireEvent.click(within(routine).getByRole("button", { name: "Run Weekly priority review now" }));

    await waitFor(() =>
      expect(within(routine).getByRole("status")).toHaveTextContent("Already running"),
    );
    expect(within(routine).getByRole("status")).toHaveTextContent("Wait for its current receipt");
    expect(within(routine).getByRole("button", { name: "Run Weekly priority review now" })).toBeEnabled();
    expect(within(routine).getByRole("time")).toHaveAttribute("datetime", job.nextRunAt);
  });

  it("shows safe run and page-load recovery", async () => {
    api.getSchedule.mockRejectedValueOnce(new Error("token=schedule-secret /private/state"));
    const { container } = render(<ScheduledPage />);

    const pageAlert = await screen.findByRole("alert");
    expect(pageAlert).toHaveTextContent("Automations couldn’t be loaded");
    expect(container).not.toHaveTextContent("schedule-secret");
    api.getSchedule.mockResolvedValueOnce(schedule({ jobs: [job] }));
    fireEvent.click(within(pageAlert).getByRole("button", { name: "Retry loading automations" }));
    const routine = await screen.findByRole("article", { name: "Weekly priority review" });

    api.runScheduleJob.mockRejectedValueOnce(new Error("provider-secret /private/state"));
    fireEvent.click(within(routine).getByRole("button", { name: "Run Weekly priority review now" }));
    const runAlert = await within(routine).findByRole("alert");
    expect(runAlert).toHaveTextContent("This run couldn’t be started");
    expect(runAlert).toHaveTextContent(/try again/i);
    expect(container).not.toHaveTextContent("provider-secret");
  });
});
