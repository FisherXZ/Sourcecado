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

    expect(screen.getByRole("heading", { level: 1, name: "Scheduled tasks" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Loading automations");
    expect(container.querySelectorAll(".schedule-job-skeleton")).toHaveLength(2);
    expect(container.querySelectorAll(".schedule-receipt-skeleton")).toHaveLength(3);
  });

  it("opens a durable task detail with labeled instructions and honest lifecycle stubs", async () => {
    api.getSchedule.mockResolvedValue(schedule({ jobs: [job] }));

    render(<ScheduledPage jobId={job.id} />);

    expect(
      await screen.findByRole("heading", { level: 1, name: "Weekly priority review" }),
    ).toBeInTheDocument();
    const breadcrumb = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(breadcrumb).getByRole("link", { name: "Scheduled tasks" })).toHaveAttribute(
      "href",
      "#/scheduled",
    );
    expect(screen.getByRole("heading", { level: 2, name: "Instructions" })).toBeInTheDocument();
    expect(screen.getByText(job.prompt)).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Repeats" })).toBeInTheDocument();
    expect(screen.getByText("Every Monday at 9:00 AM")).toBeInTheDocument();
    expect(screen.getByText(/Next run/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Approvals" })).toBeInTheDocument();
    expect(screen.getByText(/No automatic approvals/)).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Active" })).toBeChecked();
    expect(screen.getByRole("switch", { name: "Active" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Edit task" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Delete task" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Run Weekly priority review now" })).toBeEnabled();
    expect(screen.getByText(/Existing receipts and the scheduled thread remain available/)).toBeInTheDocument();
  });

  it("renders safe GFM in a bounded selectable run history", async () => {
    const receipts = Array.from({ length: 12 }, (_, index) =>
      run({
        id: index + 1,
        startedAt: `2026-08-${String(index + 1).padStart(2, "0")}T09:00:00Z`,
        summary:
          index === 11
            ? "**Top priorities**\n\n- [Maya](https://example.test/maya) needs a response\n- Jordan is due\n\n<script>unsafe()</script>"
            : `Receipt **${index + 1}**`,
      }),
    );
    api.getSchedule.mockResolvedValue(schedule({ jobs: [job], runs: receipts }));

    const { container } = render(<ScheduledPage jobId={job.id} />);

    const history = await screen.findByRole("complementary", { name: "History" });
    const historyButtons = within(history).getAllByRole("button");
    expect(historyButtons).toHaveLength(10);
    expect(historyButtons[0]).toHaveAttribute("aria-pressed", "true");
    expect(historyButtons[0]).toHaveTextContent(/\bat\b/);
    expect(screen.getByText("Top priorities", { selector: "strong" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Maya" })).toHaveAttribute(
      "href",
      "https://example.test/maya",
    );
    expect(container.querySelector("script")).toBeNull();
    expect(container).not.toHaveTextContent("unsafe()");

    fireEvent.click(historyButtons[1]);

    expect(historyButtons[1]).toHaveAttribute("aria-pressed", "true");
    const selectedReceipt = screen.getByRole("region", { name: "Selected run receipt" });
    expect(within(selectedReceipt).getByText("Completed · 2.1s")).toBeInTheDocument();
    expect(within(selectedReceipt).getByText("11", { selector: "strong" })).toBeInTheDocument();
  });

  it("keeps saved tasks compact and navigates each one to its durable detail route", async () => {
    api.getSchedule.mockResolvedValue(
      schedule({
        jobs: [job],
        runs: [run({ summary: "This receipt belongs on the detail page only." })],
      }),
    );

    render(<ScheduledPage />);

    expect(
      await screen.findByRole("heading", { level: 1, name: "Scheduled tasks" }),
    ).toBeInTheDocument();
    const task = screen.getByRole("link", { name: /Weekly priority review/ });
    expect(task).toHaveAttribute("href", "#/scheduled/4");
    expect(within(task).getByText("Every Monday at 9:00 AM")).toBeInTheDocument();
    expect(within(task).getByText("Active")).toBeInTheDocument();
    expect(within(task).queryByText(/Next/)).not.toBeInTheDocument();
    expect(screen.queryByText("This receipt belongs on the detail page only.")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run Weekly priority review now" })).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Use Weekly sourcing review template" }),
    ).toBeInTheDocument();
  });

  it("searches saved tasks without mixing templates into the result", async () => {
    const dailyJob: ScheduleJob = {
      ...job,
      id: 5,
      name: "Daily reply triage",
      prompt: "Review new replies that need action.",
    };
    api.getSchedule.mockResolvedValue(schedule({ jobs: [job, dailyJob] }));

    render(<ScheduledPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Search scheduled tasks" }));
    fireEvent.change(screen.getByRole("searchbox", { name: "Search scheduled tasks" }), {
      target: { value: "weekly" },
    });

    expect(screen.getByRole("link", { name: /Weekly priority review/ })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Daily reply triage/ })).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Use Weekly sourcing review template" }),
    ).toBeInTheDocument();
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
    const routine = await screen.findByRole("link", { name: /Weekly priority review/ });
    expect(within(routine).getByText("Every Monday at 9:00 AM")).toBeInTheDocument();
    expect(routine).toHaveAttribute("href", "#/scheduled/4");
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

    render(<ScheduledPage jobId={job.id} />);

    const history = await screen.findByRole("complementary", { name: "History" });
    expect(within(history).getByText("Running")).toBeInTheDocument();
    expect(within(history).getByText("Completed")).toBeInTheDocument();
    expect(within(history).getByText("Failed")).toBeInTheDocument();
    expect(within(history).getByText("Waiting for approval")).toBeInTheDocument();
    expect(within(history).getByText("Partial")).toBeInTheDocument();
    fireEvent.click(within(history).getByRole("button", { name: /Waiting for approval/ }));
    expect(screen.getByText("This run is paused—not complete.")).toBeInTheDocument();
    expect(screen.getByText("1 approval waiting in Inbox")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open scheduled thread" })).toHaveAttribute(
      "href",
      "#/chat/sched-4",
    );
    fireEvent.click(within(history).getByRole("button", { name: /Running/ }));
    const selectedReceipt = screen.getByRole("region", { name: "Selected run receipt" });
    expect(within(selectedReceipt).getByText(/In progress/)).toBeInTheDocument();
    expect(within(selectedReceipt).queryByText(/Legacy run/)).not.toBeInTheDocument();
  });

  it("renders a genuinely unknown status as needs-review, not failed", async () => {
    api.getSchedule.mockResolvedValue(
      schedule({
        jobs: [job],
        runs: [run({ status: "unknown", summary: "Status could not be recognized." })],
      }),
    );

    render(<ScheduledPage jobId={job.id} />);

    const receipts = await screen.findByRole("list", { name: "Run receipts" });
    expect(within(receipts).getByText(/Needs review/)).toBeInTheDocument();
    expect(within(receipts).queryByText("Failed")).not.toBeInTheDocument();
  });

  it("shows legacy zero-duration runs as 'Legacy run' instead of 0ms", async () => {
    api.getSchedule.mockResolvedValue(
      schedule({
        jobs: [job],
        runs: [run({ durationMs: 0, summary: "Found three priority contacts." })],
      }),
    );

    render(<ScheduledPage jobId={job.id} />);

    expect(await screen.findByText(/Legacy run/)).toBeInTheDocument();
    expect(screen.queryByText("0ms")).not.toBeInTheDocument();
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

    render(<ScheduledPage jobId={job.id} />);

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
    api.getSchedule.mockResolvedValue(
      schedule({ jobs: [job], runs: [run({ id: 9, summary: "Previous result." })] }),
    );
    api.runScheduleJob.mockReturnValue(
      new Promise<{ run: ScheduleRun }>((resolve) => {
        resolveRun = resolve;
      }),
    );

    window.location.hash = "#/scheduled/4";
    render(<ScheduledPage jobId={job.id} />);
    await screen.findByRole("heading", { level: 1, name: "Weekly priority review" });
    fireEvent.click(screen.getByRole("button", { name: "Run Weekly priority review now" }));

    expect(screen.getByRole("button", { name: "Running Weekly priority review" })).toBeDisabled();
    expect(screen.getByRole("time")).toHaveAttribute("datetime", job.nextRunAt);
    expect(screen.getByRole("status")).toHaveTextContent("Running now");

    resolveRun({ run: run({ id: 10, summary: "Fresh manual result." }) });
    const selectedReceipt = await screen.findByRole("region", { name: "Selected run receipt" });
    expect(within(selectedReceipt).getByText(/Completed/)).toBeInTheDocument();
    expect(within(selectedReceipt).getByText("Fresh manual result.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run Weekly priority review now" })).toBeEnabled();
    expect(window.location.hash).toBe("#/scheduled/4");
  });

  it("keeps an already-running conflict contextual and retryable", async () => {
    api.getSchedule.mockResolvedValue(schedule({ jobs: [job] }));
    api.runScheduleJob.mockRejectedValue(
      new ScheduleApiError(
        "already_running",
        "This routine is already running. Wait for its current receipt.",
      ),
    );

    render(<ScheduledPage jobId={job.id} />);
    await screen.findByRole("heading", { level: 1, name: "Weekly priority review" });
    fireEvent.click(screen.getByRole("button", { name: "Run Weekly priority review now" }));

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("Already running"),
    );
    expect(screen.getByRole("status")).toHaveTextContent("Wait for its current receipt");
    expect(screen.getByRole("button", { name: "Run Weekly priority review now" })).toBeEnabled();
    expect(screen.getByRole("time")).toHaveAttribute("datetime", job.nextRunAt);
  });

  it("shows safe run and page-load recovery", async () => {
    api.getSchedule.mockRejectedValueOnce(new Error("token=schedule-secret /private/state"));
    const { container } = render(<ScheduledPage jobId={job.id} />);

    const pageAlert = await screen.findByRole("alert");
    expect(pageAlert).toHaveTextContent("Automations couldn’t be loaded");
    expect(container).not.toHaveTextContent("schedule-secret");
    api.getSchedule.mockResolvedValueOnce(schedule({ jobs: [job] }));
    fireEvent.click(within(pageAlert).getByRole("button", { name: "Retry loading automations" }));
    await screen.findByRole("heading", { level: 1, name: "Weekly priority review" });

    api.runScheduleJob.mockRejectedValueOnce(new Error("provider-secret /private/state"));
    fireEvent.click(screen.getByRole("button", { name: "Run Weekly priority review now" }));
    const runAlert = await screen.findByRole("alert");
    expect(runAlert).toHaveTextContent("This run couldn’t be started");
    expect(runAlert).toHaveTextContent(/try again/i);
    expect(container).not.toHaveTextContent("provider-secret");
  });
});
