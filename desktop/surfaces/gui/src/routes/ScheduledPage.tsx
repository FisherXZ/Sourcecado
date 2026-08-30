import { useEffect, useMemo, useState, type FormEvent } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import {
  ScheduleApiError,
  createScheduleJob,
  getSchedule,
  runScheduleJob,
  type Schedule,
  type ScheduleJob,
  type ScheduleRun,
  type ScheduleRunStatus,
  type ScheduleTemplate,
} from "../api";

type PageState =
  | { status: "loading" }
  | { status: "loaded"; schedule: Schedule }
  | { status: "failed" };

type RunFeedback =
  | { kind: "running" }
  | { kind: "already_running"; message: string }
  | { kind: "failed" };

type CreateDraft = {
  templateId: string;
  cadence: string;
  name: string;
  prompt: string;
};

const CADENCE_LABELS: Record<string, string> = {
  weekly_monday_0900: "Every Monday at 9:00 AM",
};

const RUN_STATUS_LABELS: Record<ScheduleRunStatus, string> = {
  running: "Running",
  success: "Completed",
  failed: "Failed",
  waiting_approval: "Waiting for approval",
  partial: "Partial",
  // A routine cut short by a restart. Distinct from "Failed" on purpose: the
  // routine did not break, it was interrupted, and telling an operator their
  // automation failed when it was merely cut short sends them debugging a
  // problem that does not exist.
  interrupted: "Interrupted",
  // A status this build does not recognize. Distinct from "Failed" for the
  // same reason as "Interrupted": an indeterminate outcome is not a failure.
  unknown: "Needs review",
};

function draftFromTemplate(template: ScheduleTemplate): CreateDraft {
  return {
    templateId: template.id,
    cadence: template.cadences[0] || "",
    name: template.name,
    prompt: template.defaultPrompt,
  };
}

function formatTimestamp(stamp: string | null): string {
  if (!stamp) return "Not scheduled";
  const date = new Date(stamp);
  if (Number.isNaN(date.getTime())) return stamp;
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

function formatHistoryTimestamp(stamp: string): string {
  const date = new Date(stamp);
  if (Number.isNaN(date.getTime())) return stamp;
  const day = new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
  }).format(date);
  const time = new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
  return `${day} at ${time}`;
}

function formatDuration(durationMs: number, status: ScheduleRunStatus): string {
  if (status === "running") return "In progress";
  // Legacy rows written before duration tracking existed always recorded 0;
  // showing "0ms" reads as an instant run instead of "never measured".
  if (durationMs === 0) return "Legacy run";
  if (durationMs < 1000) return `${durationMs}ms`;
  return `${(durationMs / 1000).toFixed(1)}s`;
}

function ScheduleMarkdown({ children }: { children: string }) {
  return (
    <div className="schedule-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={{
          a: ({ children: linkChildren, ...props }) => (
            <a {...props} target="_blank" rel="noreferrer noopener">
              {linkChildren}
            </a>
          ),
          table: ({ children: tableChildren, ...props }) => (
            <div className="schedule-markdown-table">
              <table {...props}>{tableChildren}</table>
            </div>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}

function LoadingSchedule() {
  return (
    <div className="schedule-loading" aria-busy="true">
      <p className="schedule-visually-hidden" role="status">
        Loading automations…
      </p>
      <div className="schedule-job-skeleton">
        <span />
        <span />
      </div>
      <div className="schedule-job-skeleton">
        <span />
        <span />
      </div>
      <div className="schedule-receipt-skeleton" />
      <div className="schedule-receipt-skeleton" />
      <div className="schedule-receipt-skeleton" />
    </div>
  );
}

function CreateAutomationForm({
  templates,
  draft,
  errors,
  saving,
  onChange,
  onCancel,
  onSubmit,
}: {
  templates: ScheduleTemplate[];
  draft: CreateDraft;
  errors: Record<string, string>;
  saving: boolean;
  onChange: (draft: CreateDraft) => void;
  onCancel: () => void;
  onSubmit: (event: FormEvent) => void;
}) {
  return (
    <form className="schedule-create-form" aria-label="Create automation" onSubmit={onSubmit}>
      <header>
        <div>
          <p className="schedule-eyebrow">Warm Operator routine</p>
          <h2>Create automation</h2>
        </div>
        <button type="button" onClick={onCancel} disabled={saving}>
          Cancel
        </button>
      </header>

      <label>
        <span>Template</span>
        <select
          aria-label="Template"
          value={draft.templateId}
          onChange={(event) => {
            const next = templates.find((item) => item.id === event.target.value);
            if (next) onChange(draftFromTemplate(next));
          }}
          disabled={saving}
        >
          {templates.map((template) => (
            <option key={template.id} value={template.id}>
              {template.name}
            </option>
          ))}
        </select>
      </label>

      <label>
        <span>Cadence</span>
        <select
          aria-label="Cadence"
          value={draft.cadence}
          aria-invalid={Boolean(errors.cadence) || undefined}
          aria-describedby={errors.cadence ? "schedule-cadence-error" : undefined}
          onChange={(event) => onChange({ ...draft, cadence: event.target.value })}
          disabled={saving}
        >
          {(templates.find((item) => item.id === draft.templateId)?.cadences || []).map(
            (cadence) => (
              <option key={cadence} value={cadence}>
                {CADENCE_LABELS[cadence] || cadence}
              </option>
            ),
          )}
        </select>
      </label>
      {errors.cadence && (
        <p id="schedule-cadence-error" className="schedule-field-error" role="alert">
          {errors.cadence}
        </p>
      )}

      <label>
        <span>Automation name</span>
        <input
          aria-label="Automation name"
          value={draft.name}
          aria-invalid={Boolean(errors.name) || undefined}
          aria-describedby={errors.name ? "schedule-name-error" : undefined}
          onChange={(event) => onChange({ ...draft, name: event.target.value })}
          disabled={saving}
        />
      </label>
      {errors.name && (
        <p id="schedule-name-error" className="schedule-field-error" role="alert">
          {errors.name}
        </p>
      )}

      <label>
        <span>Instructions</span>
        <textarea
          aria-label="Instructions"
          rows={5}
          value={draft.prompt}
          aria-invalid={Boolean(errors.prompt) || undefined}
          aria-describedby={errors.prompt ? "schedule-prompt-error" : undefined}
          onChange={(event) => onChange({ ...draft, prompt: event.target.value })}
          disabled={saving}
        />
      </label>
      {errors.prompt && (
        <p id="schedule-prompt-error" className="schedule-field-error" role="alert">
          {errors.prompt}
        </p>
      )}

      <div className="schedule-form-actions">
        <button type="submit" disabled={saving}>
          {saving ? "Saving…" : "Save automation"}
        </button>
      </div>
    </form>
  );
}

function ScheduleReceipt({ receipt, heading }: { receipt: ScheduleRun; heading: string }) {
  const label = RUN_STATUS_LABELS[receipt.status];
  return (
    <li
      className={`schedule-receipt receipt-${receipt.status}`}
      aria-label={`${label} run ${receipt.id}`}
    >
      <header>
        <h2>{heading}</h2>
        <span className="schedule-run-status">
          {label} · {formatDuration(receipt.durationMs, receipt.status)}
        </span>
      </header>
      <ScheduleMarkdown>{receipt.summary || "No summary was recorded."}</ScheduleMarkdown>
      {receipt.status === "waiting_approval" && (
        <div className="schedule-waiting-context">
          <strong>This run is paused—not complete.</strong>
          <span>
            {receipt.waitingApprovalCount} approval
            {receipt.waitingApprovalCount === 1 ? "" : "s"} waiting in Inbox
          </span>
          <span>Choose allow or deny explicitly; this run will not resume automatically.</span>
        </div>
      )}
      {receipt.artifacts.length > 0 && (
        <ul className="schedule-artifacts" aria-label="Run artifacts">
          {receipt.artifacts.map((artifact) => (
            <li key={artifact.id}>
              {artifact.externalUrl ? (
                <a href={artifact.externalUrl} target="_blank" rel="noopener noreferrer">
                  {artifact.title}
                </a>
              ) : (
                <span>{artifact.title}</span>
              )}
              <small>{artifact.artifactType}</small>
            </li>
          ))}
        </ul>
      )}
      <a className="schedule-thread-link" href={`#/chat/${encodeURIComponent(receipt.sessionId)}`}>
        Open scheduled thread
      </a>
    </li>
  );
}

function RunFeedbackMessage({ feedback }: { feedback: RunFeedback | undefined }) {
  if (feedback?.kind === "running") {
    return (
      <p className="schedule-run-feedback" role="status">
        Running now. The next scheduled time remains unchanged.
      </p>
    );
  }
  if (feedback?.kind === "already_running") {
    return (
      <p className="schedule-run-feedback warning" role="status">
        <strong>Already running.</strong> {feedback.message}
      </p>
    );
  }
  if (feedback?.kind === "failed") {
    return (
      <div className="schedule-run-feedback error" role="alert">
        <strong>This run couldn’t be started.</strong>
        <span>Check that Sourcecado is available, then try again.</span>
      </div>
    );
  }
  return null;
}

function ScheduleTaskList({
  jobs,
  templates,
  onCreateFromTemplate,
}: {
  jobs: ScheduleJob[];
  templates: ScheduleTemplate[];
  onCreateFromTemplate: (template: ScheduleTemplate) => void;
}) {
  return (
    <>
      {jobs.length > 0 && (
        <section className="schedule-task-list" aria-label="Saved scheduled tasks">
          {jobs.map((job) => (
            <a
              key={job.id}
              className="schedule-task-card"
              href={`#/scheduled/${job.id}`}
              aria-label={`Open ${job.name}`}
            >
              <strong>{job.name}</strong>
              <p>{job.prompt}</p>
              <span className="schedule-task-meta">
                <span>{CADENCE_LABELS[job.cadence] || job.cadence}</span>
                <span className="schedule-active-pill">Active</span>
              </span>
            </a>
          ))}
        </section>
      )}

      {templates.length > 0 && (
        <section className="schedule-template-section" aria-labelledby="schedule-template-heading">
          <h2 id="schedule-template-heading">Start from a template</h2>
          <div className="schedule-template-grid">
            {templates.map((template) => (
              <button
                key={template.id}
                type="button"
                className="schedule-template-card"
                aria-label={`Use ${template.name} template`}
                onClick={() => onCreateFromTemplate(template)}
              >
                <span className="schedule-template-mark" aria-hidden="true">
                  ✦
                </span>
                <span>
                  <strong>{template.name}</strong>
                  <p>{template.description}</p>
                  <small>
                    {CADENCE_LABELS[template.cadences[0] || ""] ||
                      template.cadences[0] ||
                      "Choose a cadence"}
                  </small>
                </span>
              </button>
            ))}
          </div>
        </section>
      )}
    </>
  );
}

function ScheduleDetail({
  job,
  receipts,
  feedback,
  onRun,
}: {
  job: ScheduleJob;
  receipts: ScheduleRun[];
  feedback: RunFeedback | undefined;
  onRun: () => void;
}) {
  const running = feedback?.kind === "running";
  const boundedReceipts = receipts.slice(0, 10);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(receipts[0]?.id ?? null);
  useEffect(() => {
    setSelectedRunId(receipts[0]?.id ?? null);
  }, [job.id, receipts[0]?.id]);
  const selectedReceipt =
    receipts.find((receipt) => receipt.id === selectedRunId) || receipts[0] || null;
  return (
    <>
      <nav className="schedule-breadcrumb" aria-label="Breadcrumb">
        <a href="#/scheduled">Scheduled tasks</a>
        <span aria-hidden="true">/</span>
        <span>{job.name}</span>
      </nav>
      <header className="schedule-detail-header">
        <div>
          <h1>{job.name}</h1>
          <label className="schedule-active-switch">
            <input type="checkbox" role="switch" aria-label="Active" checked readOnly disabled />
            <span>Active</span>
          </label>
        </div>
        <div className="schedule-detail-actions">
          <button type="button" aria-label="Edit task" disabled>
            <span aria-hidden="true">✎</span>
          </button>
          <button type="button" aria-label="Delete task" disabled>
            <span aria-hidden="true">⌫</span>
          </button>
          <button
            type="button"
            aria-label={running ? `Running ${job.name}` : `Run ${job.name} now`}
            aria-busy={running || undefined}
            disabled={running}
            onClick={onRun}
          >
            {running ? (
              "Running…"
            ) : (
              <>
                <span aria-hidden="true">▷</span> Run now
              </>
            )}
          </button>
        </div>
      </header>

      <RunFeedbackMessage feedback={feedback} />

      <div className="schedule-detail-grid">
        <aside className="schedule-history" aria-labelledby="schedule-history-heading">
          <h2 id="schedule-history-heading">History</h2>
          {boundedReceipts.length === 0 ? (
            <p>No runs yet.</p>
          ) : (
            <ul>
              {boundedReceipts.map((receipt) => (
                <li key={receipt.id}>
                  <button
                    type="button"
                    aria-pressed={selectedReceipt?.id === receipt.id}
                    onClick={() => setSelectedRunId(receipt.id)}
                  >
                    <span>{formatHistoryTimestamp(receipt.startedAt)}</span>
                    <span>{RUN_STATUS_LABELS[receipt.status]}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </aside>

        <article className="schedule-detail-body">
          {selectedReceipt && (
            <section className="schedule-selected-receipt" aria-label="Selected run receipt">
              <ul aria-label="Run receipts">
                <ScheduleReceipt
                  receipt={selectedReceipt}
                  heading={
                    selectedReceipt.id === boundedReceipts[0]?.id
                      ? "Latest receipt"
                      : "Run receipt"
                  }
                />
              </ul>
            </section>
          )}
          <section className="schedule-detail-section">
            <h2>Instructions</h2>
            <p>{job.prompt}</p>
          </section>
          <section className="schedule-detail-section schedule-repeat-row">
            <div>
              <h2>Repeats</h2>
              <p>{CADENCE_LABELS[job.cadence] || job.cadence}</p>
            </div>
            <p>
              Next run{" "}
              <time dateTime={job.nextRunAt || undefined}>{formatTimestamp(job.nextRunAt)}</time>
            </p>
          </section>
          <section className="schedule-detail-section">
            <h2>Approvals</h2>
            <p>
              No automatic approvals. Enrichment and sending still pause in Inbox for an explicit
              decision.
            </p>
          </section>
          <p className="schedule-retention-note">
            Deleting this task stops future runs. Existing receipts and the scheduled thread remain
            available as history.
          </p>
        </article>
      </div>
    </>
  );
}

export function ScheduledPage({ jobId }: { jobId?: number } = {}) {
  const [state, setState] = useState<PageState>({ status: "loading" });
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [showCreate, setShowCreate] = useState(false);
  const [draft, setDraft] = useState<CreateDraft | null>(null);
  const [createErrors, setCreateErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [createFeedback, setCreateFeedback] = useState<string | null>(null);
  const [runFeedback, setRunFeedback] = useState<Record<number, RunFeedback>>({});
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [sortBy, setSortBy] = useState<"next_run" | "name">("next_run");

  useEffect(() => {
    let active = true;
    setState({ status: "loading" });
    getSchedule().then(
      (schedule) => {
        if (active) setState({ status: "loaded", schedule });
      },
      () => {
        if (active) setState({ status: "failed" });
      },
    );
    return () => {
      active = false;
    };
  }, [loadAttempt]);

  const waitingApprovalCount = useMemo(
    () =>
      state.status === "loaded"
        ? state.schedule.runs.reduce(
            (total, receipt) => total + receipt.waitingApprovalCount,
            0,
          )
        : 0,
    [state],
  );

  const visibleJobs = useMemo(() => {
    if (state.status !== "loaded") return [];
    const query = searchQuery.trim().toLocaleLowerCase();
    return state.schedule.jobs
      .filter(
        (job) =>
          !query ||
          job.name.toLocaleLowerCase().includes(query) ||
          job.prompt.toLocaleLowerCase().includes(query),
      )
      .sort((left, right) => {
        if (sortBy === "name") return left.name.localeCompare(right.name);
        if (left.nextRunAt === right.nextRunAt) return left.name.localeCompare(right.name);
        if (left.nextRunAt === null) return 1;
        if (right.nextRunAt === null) return -1;
        return left.nextRunAt.localeCompare(right.nextRunAt);
      });
  }, [searchQuery, sortBy, state]);
  const selectedJob =
    state.status === "loaded" && jobId !== undefined
      ? state.schedule.jobs.find((job) => job.id === jobId)
      : undefined;
  const selectedReceipts =
    state.status === "loaded" && selectedJob
      ? state.schedule.runs
          .filter((receipt) => receipt.jobId === selectedJob.id)
          .sort((left, right) => right.id - left.id)
      : [];

  function openCreate(template?: ScheduleTemplate) {
    if (state.status !== "loaded") return;
    const selectedTemplate = template || state.schedule.templates?.[0];
    if (!selectedTemplate) return;
    setDraft(draftFromTemplate(selectedTemplate));
    setCreateErrors({});
    setCreateFeedback(null);
    setShowCreate(true);
  }

  async function submitCreate(event: FormEvent) {
    event.preventDefault();
    if (state.status !== "loaded" || !draft || saving) return;
    const errors: Record<string, string> = {};
    if (!draft.name.trim()) errors.name = "Enter an automation name.";
    if (!draft.prompt.trim()) errors.prompt = "Enter instructions for this automation.";
    if (!draft.cadence) errors.cadence = "Choose a cadence.";
    if (Object.keys(errors).length) {
      setCreateErrors(errors);
      return;
    }
    setSaving(true);
    setCreateErrors({});
    try {
      const created = await createScheduleJob({
        templateId: draft.templateId,
        cadence: draft.cadence,
        name: draft.name.trim(),
        prompt: draft.prompt.trim(),
      });
      setState((current) =>
        current.status === "loaded"
          ? {
              status: "loaded",
              schedule: {
                ...current.schedule,
                jobs: [...current.schedule.jobs, created],
              },
            }
          : current,
      );
      setShowCreate(false);
      setDraft(null);
      setCreateFeedback(`${created.name} was created.`);
      window.dispatchEvent(new CustomEvent("sourcecado:schedule-changed"));
    } catch (error) {
      if (error instanceof ScheduleApiError && error.code === "invalid_routine") {
        setCreateErrors(error.fieldErrors);
      } else {
        setCreateErrors({ form: "This automation couldn’t be created. Try again." });
      }
    } finally {
      setSaving(false);
    }
  }

  async function runNow(job: ScheduleJob) {
    if (runFeedback[job.id]?.kind === "running") return;
    setRunFeedback((current) => ({ ...current, [job.id]: { kind: "running" } }));
    try {
      const body = await runScheduleJob(job.id);
      setState((current) =>
        current.status === "loaded"
          ? {
              status: "loaded",
              schedule: {
                ...current.schedule,
                runs: [
                  body.run,
                  ...current.schedule.runs.filter((receipt) => receipt.id !== body.run.id),
                ],
              },
            }
          : current,
      );
      setRunFeedback((current) => {
        const next = { ...current };
        delete next[job.id];
        return next;
      });
      window.dispatchEvent(new CustomEvent("sourcecado:inbox-changed"));
      window.dispatchEvent(new CustomEvent("sourcecado:schedule-changed"));
    } catch (error) {
      setRunFeedback((current) => ({
        ...current,
        [job.id]:
          error instanceof ScheduleApiError && error.code === "already_running"
            ? { kind: "already_running", message: error.message }
            : { kind: "failed" },
      }));
    }
  }

  return (
    <main className="route-page scheduled-page">
      {jobId === undefined && (
        <>
          <header className="scheduled-page-header">
            <div>
              <h1>Scheduled tasks</h1>
              <p>Run tasks on a schedule or whenever you need them.</p>
            </div>
            <div className="schedule-list-actions">
              <button
                type="button"
                className="schedule-search-toggle"
                aria-label="Search scheduled tasks"
                aria-expanded={searchOpen}
                aria-controls="schedule-search"
                onClick={() => setSearchOpen((open) => !open)}
              >
                <span aria-hidden="true">⌕</span>
              </button>
              <label>
                <span className="schedule-visually-hidden">Sort scheduled tasks</span>
                <select
                  aria-label="Sort scheduled tasks"
                  value={sortBy}
                  onChange={(event) => setSortBy(event.target.value as "next_run" | "name")}
                >
                  <option value="next_run">Sort by Next run</option>
                  <option value="name">Sort by Name</option>
                </select>
              </label>
              <button
                type="button"
                className="schedule-primary-action"
                onClick={() => openCreate()}
                disabled={state.status !== "loaded" || !state.schedule.templates?.length}
              >
                New task
              </button>
            </div>
          </header>
          {searchOpen && (
            <div className="schedule-search" id="schedule-search">
              <label htmlFor="schedule-search-input">Search scheduled tasks</label>
              <input
                id="schedule-search-input"
                type="search"
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
              />
            </div>
          )}
        </>
      )}

      {state.status === "loading" && <LoadingSchedule />}
      {state.status === "failed" && (
        <section className="route-error" role="alert">
          {jobId === undefined ? (
            <h2>Automations couldn’t be loaded</h2>
          ) : (
            <h1>Automations couldn’t be loaded</h1>
          )}
          <p>Check that Sourcecado is available, then try again.</p>
          <button type="button" onClick={() => setLoadAttempt((attempt) => attempt + 1)}>
            Retry loading automations
          </button>
        </section>
      )}
      {state.status === "loaded" && (
        <>
          {jobId !== undefined && selectedJob && (
            <ScheduleDetail
              job={selectedJob}
              receipts={selectedReceipts}
              feedback={runFeedback[jobId]}
              onRun={() => void runNow(selectedJob)}
            />
          )}
          {jobId !== undefined && !selectedJob && (
            <section className="route-error" role="alert">
              <h1>Scheduled task not found</h1>
              <p>This saved task is no longer available.</p>
              <a href="#/scheduled">Back to Scheduled tasks</a>
            </section>
          )}
          {jobId === undefined && (
            <>
              {waitingApprovalCount > 0 && (
                <aside className="schedule-inbox-summary" role="status">
                  <strong>
                    {waitingApprovalCount} approval
                    {waitingApprovalCount === 1 ? "" : "s"} waiting in Inbox
                  </strong>
                  <span>Open the matching scheduled thread to review the approval in context.</span>
                </aside>
              )}
              {createFeedback && (
                <p className="schedule-create-feedback" role="status">
                  {createFeedback}
                </p>
              )}
              {showCreate && draft && (
                <CreateAutomationForm
                  templates={state.schedule.templates || []}
                  draft={draft}
                  errors={createErrors}
                  saving={saving}
                  onChange={setDraft}
                  onCancel={() => {
                    setShowCreate(false);
                    setDraft(null);
                    setCreateErrors({});
                  }}
                  onSubmit={(event) => void submitCreate(event)}
                />
              )}
              {createErrors.form && (
                <p className="schedule-create-feedback error" role="alert">
                  {createErrors.form}
                </p>
              )}
              {state.schedule.jobs.length === 0 && !showCreate && (
                <section className="schedule-empty">
                  <p className="schedule-eyebrow">Template ready</p>
                  <h2>No automations yet</h2>
                  <p>
                    Start with a weekly sourcing review. You can adjust its name and instructions
                    before saving.
                  </p>
                  <button
                    type="button"
                    onClick={() => openCreate()}
                    disabled={!state.schedule.templates?.length}
                  >
                    Create automation
                  </button>
                </section>
              )}
              {state.schedule.jobs.length > 0 && (
                <ScheduleTaskList
                  jobs={visibleJobs}
                  templates={state.schedule.templates || []}
                  onCreateFromTemplate={openCreate}
                />
              )}
              {state.schedule.jobs.length > 0 && visibleJobs.length === 0 && (
                <p className="schedule-no-results" role="status">
                  No saved tasks match “{searchQuery.trim()}”.
                </p>
              )}
            </>
          )}
        </>
      )}
    </main>
  );
}
