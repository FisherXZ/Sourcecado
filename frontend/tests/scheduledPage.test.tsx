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
  updateScheduleJob: vi.fn(),
}));

vi.mock("../src/api", async () => {
  const actual = await vi.importActual<typeof import("../src/api")>("../src/api");
  return {
    ...actual,
    createScheduleJob: api.createScheduleJob,
    getSchedule: api.getSchedule,
    runScheduleJob: api.runScheduleJob,
    updateScheduleJob: api.updateScheduleJob,
  };
});

const cadences = [
  { id: "weekly_monday_0900", label: "Every Monday at 9:00 AM" },
  { id: "weekdays_0900", label: "Every weekday at 9:00 AM" },
  { id: "daily_0900", label: "Every day at 9:00 AM" },
];

const template = {
  id: "weekly_sourcing_review",
  name: "Weekly sourcing review",
  description: "Review priorities and next sourcing work.",
  cadences: cadences.map((cadence) => cadence.id),
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
  enabled: true,
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
  return { jobs: [], runs: [], templates: [template], cadences, ...overrides };
}

async function openDetail(data: Schedule) {
  api.getSchedule.mockResolvedValue(data);
  window.location.hash = `#/scheduled/${job.id}`;
  const view = render(<ScheduledPage jobId={job.id} />);
  await screen.findByRole("heading", { level: 1, name: job.name });
  return view;
}

describe("ScheduledPage", () => {
  beforeEach(() => {
    api.createScheduleJob.mockReset();
    api.getSchedule.mockReset();
    api.runScheduleJob.mockReset();
    api.updateScheduleJob.mockReset();
    window.location.hash = "#/scheduled";
  });

  it("shows skeleton rows while loading", () => {
    api.getSchedule.mockReturnValue(new Promise(() => {}));

    const { container } = render(<ScheduledPage />);

    expect(screen.getByRole("heading", { level: 1, name: "Scheduled" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Loading scheduled tasks");
    expect(container.querySelectorAll(".scheduled-skeleton span")).toHaveLength(3);
  });

  it("lists tasks in a table with cadence, next run and last run, without receipts", async () => {
    api.getSchedule.mockResolvedValue(
      schedule({
        jobs: [job],
        runs: [run({ summary: "This receipt belongs on the task page only." })],
      }),
    );

    render(<ScheduledPage />);

    const link = await screen.findByRole("link", { name: "Open Weekly priority review" });
    expect(link).toHaveAttribute("href", "#/scheduled/4");
    const row = link.closest("tr")!;
    expect(within(row).getByText("Every Monday at 9:00 AM")).toBeInTheDocument();
    expect(row.querySelector(`time[datetime="${job.nextRunAt}"]`)).not.toBeNull();
    expect(within(row).getByText("Completed")).toBeInTheDocument();
    expect(screen.getByText("1 task")).toBeInTheDocument();
    expect(screen.queryByText("This receipt belongs on the task page only.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New task" })).toBeEnabled();
  });

  it("shows a paused task as Paused instead of a next run", async () => {
    api.getSchedule.mockResolvedValue(
      schedule({ jobs: [{ ...job, enabled: false, nextRunAt: null }] }),
    );

    render(<ScheduledPage />);

    const row = (await screen.findByRole("link", { name: "Open Weekly priority review" })).closest("tr")!;
    expect(within(row).getByText("Paused")).toBeInTheDocument();
  });

  it("searches task names and instructions, then clears an empty result", async () => {
    const daily: ScheduleJob = {
      ...job,
      id: 5,
      name: "Daily reply check",
      prompt: "List replies.",
      cadence: "daily_0900",
    };
    api.getSchedule.mockResolvedValue(schedule({ jobs: [job, daily] }));

    render(<ScheduledPage />);

    const search = await screen.findByRole("searchbox", { name: "Search scheduled tasks" });
    fireEvent.change(search, { target: { value: "replies" } });
    expect(screen.getByRole("link", { name: "Open Daily reply check" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open Weekly priority review" })).not.toBeInTheDocument();

    fireEvent.change(search, { target: { value: "nothing" } });
    expect(screen.getByRole("heading", { name: "No tasks match “nothing”" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear search" }));
    expect(screen.getByRole("link", { name: "Open Weekly priority review" })).toBeInTheDocument();
  });

  it("opens a prefilled form from the empty state", async () => {
    api.getSchedule.mockResolvedValue(schedule());

    render(<ScheduledPage />);

    expect(await screen.findByRole("heading", { name: "No scheduled tasks" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "New task" })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "New task" }));

    const form = screen.getByRole("form", { name: "New scheduled task" });
    expect(within(form).getByRole("textbox", { name: "Name" })).toHaveValue(template.name);
    expect(within(form).getByRole("textbox", { name: "Instructions" })).toHaveValue(
      template.defaultPrompt,
    );
    expect(within(form).getByRole("combobox", { name: "Repeats" })).toHaveValue("weekly_monday_0900");
    expect(within(form).getAllByRole("option").map((option) => option.textContent)).toEqual(
      cadences.map((cadence) => cadence.label),
    );
    // One template: no template picker.
    expect(within(form).queryByRole("combobox", { name: "Template" })).not.toBeInTheDocument();
  });

  it("validates the form before calling the API", async () => {
    api.getSchedule.mockResolvedValue(schedule());
    render(<ScheduledPage />);
    fireEvent.click(await screen.findByRole("button", { name: "New task" }));

    const form = screen.getByRole("form", { name: "New scheduled task" });
    fireEvent.change(within(form).getByRole("textbox", { name: "Name" }), { target: { value: " " } });
    fireEvent.change(within(form).getByRole("textbox", { name: "Instructions" }), {
      target: { value: "" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Create task" }));

    expect(within(form).getByRole("textbox", { name: "Name" })).toHaveAttribute("aria-invalid", "true");
    expect(within(form).getByText("Enter a name.")).toBeInTheDocument();
    expect(within(form).getByText("Enter instructions.")).toBeInTheDocument();
    expect(api.createScheduleJob).not.toHaveBeenCalled();
  });

  it("creates a task with the chosen cadence and opens it", async () => {
    api.getSchedule.mockResolvedValue(schedule());
    api.createScheduleJob.mockResolvedValue({ ...job, id: 7, name: "Daily reply check", cadence: "daily_0900" });
    render(<ScheduledPage />);
    fireEvent.click(await screen.findByRole("button", { name: "New task" }));

    const form = screen.getByRole("form", { name: "New scheduled task" });
    fireEvent.change(within(form).getByRole("textbox", { name: "Name" }), {
      target: { value: "  Daily reply check " },
    });
    fireEvent.change(within(form).getByRole("combobox", { name: "Repeats" }), {
      target: { value: "daily_0900" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Create task" }));

    await waitFor(() => expect(window.location.hash).toBe("#/scheduled/7"));
    expect(api.createScheduleJob).toHaveBeenCalledWith({
      templateId: "weekly_sourcing_review",
      cadence: "daily_0900",
      name: "Daily reply check",
      prompt: template.defaultPrompt,
    });
  });

  it("shows server field validation beside the input without leaking details", async () => {
    api.getSchedule.mockResolvedValue(schedule());
    api.createScheduleJob.mockRejectedValue(
      new ScheduleApiError("invalid_routine", "Review the highlighted routine fields.", {
        prompt: "Enter instructions up to 2,000 characters.",
      }),
    );
    const { container } = render(<ScheduledPage />);
    fireEvent.click(await screen.findByRole("button", { name: "New task" }));
    fireEvent.click(screen.getByRole("button", { name: "Create task" }));

    expect(await screen.findByText("Enter instructions up to 2,000 characters.")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Instructions" })).toHaveAttribute("aria-invalid", "true");
    expect(container).not.toHaveTextContent("Review the highlighted routine fields.");
  });

  it("opens a task page with icon lines, instructions and the three actions", async () => {
    await openDetail(schedule({ jobs: [job] }));

    expect(screen.getByRole("link", { name: "Back to Scheduled tasks" })).toHaveAttribute(
      "href",
      "#/scheduled",
    );
    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(screen.getByText("Every Monday at 9:00 AM")).toBeInTheDocument();
    expect(screen.getByRole("time")).toHaveAttribute("datetime", job.nextRunAt);
    expect(screen.getByRole("heading", { level: 2, name: "Instructions" })).toBeInTheDocument();
    expect(screen.getByText(job.prompt)).toBeInTheDocument();
    expect(screen.getByText("No runs yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run Weekly priority review now" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Pause Weekly priority review" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Edit Weekly priority review" })).toBeEnabled();
  });

  it("renders safe GFM and lets the director pick any run from the history", async () => {
    const runs = Array.from({ length: 12 }, (_, index) =>
      run({
        id: index + 1,
        startedAt: `2026-08-${String(index + 1).padStart(2, "0")}T16:00:00Z`,
        summary:
          index === 11
            ? "**Top priorities**\n\n- [Maya](https://example.test/maya) needs a response\n\n<script>unsafe()</script>"
            : `Receipt **${index + 1}**`,
      }),
    );
    const { container } = await openDetail(schedule({ jobs: [job], runs }));

    const list = screen.getByRole("list", { name: "Runs" });
    const buttons = within(list).getAllByRole("button");
    expect(buttons).toHaveLength(12);
    expect(buttons[0]).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Top priorities", { selector: "strong" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Maya" })).toHaveAttribute("href", "https://example.test/maya");
    expect(container.querySelector("script")).toBeNull();
    expect(container).not.toHaveTextContent("unsafe()");
    expect(within(list).getAllByRole("link")[0]).toHaveAttribute("href", "#/chat/sched-4");

    fireEvent.click(buttons[1]);

    expect(buttons[1]).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("11", { selector: "strong" })).toBeInTheDocument();
  });

  it("names every run status honestly, including interrupted and unknown", async () => {
    const runs = [
      run({ id: 6, status: "unknown" }),
      run({ id: 5, status: "interrupted" }),
      run({ id: 4, status: "partial" }),
      run({ id: 3, status: "waiting_approval", waitingApprovalCount: 2 }),
      run({ id: 2, status: "failed", summary: "The routine failed before it could finish." }),
      run({ id: 1, status: "running", durationMs: 0, finishedAt: null }),
    ];
    api.getSchedule.mockResolvedValue(schedule({ jobs: [job], runs }));
    window.location.hash = "#/scheduled/4";
    render(<ScheduledPage jobId={job.id} />);

    const list = await screen.findByRole("list", { name: "Runs" });
    for (const label of ["Needs review", "Interrupted", "Partial", "Waiting for approval", "Failed", "Running"]) {
      expect(within(list).getByText(label)).toBeInTheDocument();
    }
    fireEvent.click(within(list).getByRole("button", { name: /Waiting for approval/ }));
    expect(screen.getByText("2 approvals waiting")).toBeInTheDocument();
    fireEvent.click(within(list).getByRole("button", { name: /Running/ }));
    expect(screen.getByText("In progress")).toBeInTheDocument();
  });

  it("shows artifact links and the thread link on the selected run", async () => {
    await openDetail(
      schedule({
        jobs: [job],
        runs: [
          run({
            artifacts: [
              {
                id: "shortlist",
                artifactType: "shortlist",
                title: "Priority shortlist",
                externalUrl: "https://example.test/shortlist",
              },
            ],
          }),
        ],
      }),
    );

    const receipt = screen.getByRole("article", { name: "Completed run 9" });
    expect(within(receipt).getByRole("link", { name: "Priority shortlist" })).toHaveAttribute(
      "href",
      "https://example.test/shortlist",
    );
    expect(within(receipt).getByRole("link", { name: "Open thread" })).toHaveAttribute(
      "href",
      "#/chat/sched-4",
    );
    expect(within(receipt).getByText("2.1s")).toBeInTheDocument();
  });

  it("starts Run now in the background and polls until the run finishes", async () => {
    const running = run({ id: 10, status: "running", durationMs: 0, finishedAt: null, summary: "" });
    const finished = run({ id: 10, summary: "Fresh manual result." });
    api.runScheduleJob.mockResolvedValue({ run: running });
    await openDetail(schedule({ jobs: [job] }));
    api.getSchedule.mockResolvedValue(schedule({ jobs: [job], runs: [finished] }));

    fireEvent.click(screen.getByRole("button", { name: "Run Weekly priority review now" }));

    expect(await screen.findByRole("button", { name: "Running Weekly priority review" })).toBeDisabled();
    expect(screen.getByText("In progress")).toBeInTheDocument();
    expect(await screen.findByText("Fresh manual result.", {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run Weekly priority review now" })).toBeEnabled();
    expect(window.location.hash).toBe("#/scheduled/4");
  });

  it("explains an already-running conflict and a failed start without leaking details", async () => {
    const { container } = await openDetail(schedule({ jobs: [job] }));
    api.runScheduleJob.mockRejectedValueOnce(
      new ScheduleApiError("already_running", "This routine is already running."),
    );
    fireEvent.click(screen.getByRole("button", { name: "Run Weekly priority review now" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Already running.");

    api.runScheduleJob.mockRejectedValueOnce(new Error("provider-secret /private/state"));
    fireEvent.click(screen.getByRole("button", { name: "Run Weekly priority review now" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn’t start the run. Try again.");
    expect(container).not.toHaveTextContent("provider-secret");
  });

  it("edits name, instructions and cadence in place", async () => {
    await openDetail(schedule({ jobs: [job] }));
    api.updateScheduleJob.mockResolvedValue({
      ...job,
      name: "Daily reply check",
      prompt: "List replies.",
      cadence: "daily_0900",
    });

    fireEvent.click(screen.getByRole("button", { name: "Edit Weekly priority review" }));
    const form = screen.getByRole("form", { name: "Edit scheduled task" });
    fireEvent.change(within(form).getByRole("textbox", { name: "Name" }), {
      target: { value: "Daily reply check" },
    });
    fireEvent.change(within(form).getByRole("textbox", { name: "Instructions" }), {
      target: { value: "List replies." },
    });
    fireEvent.change(within(form).getByRole("combobox", { name: "Repeats" }), {
      target: { value: "daily_0900" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Daily reply check" })).toBeInTheDocument();
    expect(api.updateScheduleJob).toHaveBeenCalledWith(4, {
      name: "Daily reply check",
      prompt: "List replies.",
      cadence: "daily_0900",
    });
    expect(screen.queryByRole("form", { name: "Edit scheduled task" })).not.toBeInTheDocument();
    expect(screen.getByText("Every day at 9:00 AM")).toBeInTheDocument();
    expect(screen.getByText("List replies.")).toBeInTheDocument();
  });

  it("keeps the editor open with the server's field error when a save is refused", async () => {
    await openDetail(schedule({ jobs: [job] }));
    api.updateScheduleJob.mockRejectedValue(
      new ScheduleApiError("invalid_routine", "Review the highlighted routine fields.", {
        name: "Enter a name up to 80 characters.",
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Edit Weekly priority review" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Enter a name up to 80 characters.")).toBeInTheDocument();
    expect(screen.getByRole("form", { name: "Edit scheduled task" })).toBeInTheDocument();
  });

  it("pauses and resumes a task", async () => {
    await openDetail(schedule({ jobs: [job] }));
    api.updateScheduleJob.mockResolvedValueOnce({ ...job, enabled: false, nextRunAt: null });

    fireEvent.click(screen.getByRole("button", { name: "Pause Weekly priority review" }));

    const resume = await screen.findByRole("button", { name: "Resume Weekly priority review" });
    expect(api.updateScheduleJob).toHaveBeenLastCalledWith(4, { enabled: false });
    expect(screen.getAllByText("Paused")).toHaveLength(2);
    expect(screen.queryByRole("time")).not.toBeInTheDocument();

    api.updateScheduleJob.mockResolvedValueOnce(job);
    fireEvent.click(resume);

    expect(await screen.findByRole("button", { name: "Pause Weekly priority review" })).toBeInTheDocument();
    expect(api.updateScheduleJob).toHaveBeenLastCalledWith(4, { enabled: true });
    expect(screen.getByText("Active")).toBeInTheDocument();
  });

  it("recovers from a failed page load and shows a missing task", async () => {
    api.getSchedule.mockRejectedValueOnce(new Error("token=schedule-secret /private/state"));
    const { container } = render(<ScheduledPage jobId={99} />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Scheduled tasks couldn’t be loaded");
    expect(container).not.toHaveTextContent("schedule-secret");
    api.getSchedule.mockResolvedValueOnce(schedule({ jobs: [job] }));
    fireEvent.click(within(alert).getByRole("button", { name: "Retry loading scheduled tasks" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Scheduled task not found" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to Scheduled tasks" })).toHaveAttribute("href", "#/scheduled");
  });
});
