import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import {
  ScheduleApiError,
  createScheduleJob,
  getSchedule,
  runScheduleJob,
  updateScheduleJob,
  type Schedule,
  type ScheduleCadence,
  type ScheduleJob,
  type ScheduleRun,
  type ScheduleRunStatus,
  type ScheduleTemplate,
} from "../api";
import { Icon } from "../icons";

type PageState =
  | { status: "loading" }
  | { status: "loaded"; schedule: Schedule }
  | { status: "failed" };

type Notice = { tone: "warn" | "error"; text: string };

type Draft = {
  templateId: string;
  cadence: string;
  name: string;
  prompt: string;
};

/** How often the page re-reads the schedule while a run is in progress. */
export const RUN_POLL_MS = 2000;

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

const RUN_STATUS_TONES: Record<ScheduleRunStatus, string> = {
  running: "running",
  success: "ok",
  failed: "error",
  waiting_approval: "warn",
  partial: "warn",
  interrupted: "neutral",
  unknown: "warn",
};

function cadenceLabel(cadences: ScheduleCadence[], id: string): string {
  return cadences.find((cadence) => cadence.id === id)?.label || id;
}

function draftFromTemplate(template: ScheduleTemplate): Draft {
  return {
    templateId: template.id,
    cadence: template.cadences[0] || "",
    name: template.name,
    prompt: template.defaultPrompt,
  };
}

function formatTimestamp(stamp: string): string {
  const date = new Date(stamp);
  if (Number.isNaN(date.getTime())) return stamp;
  return new Intl.DateTimeFormat(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

function formatDuration(durationMs: number, status: ScheduleRunStatus): string {
  if (status === "running") return "";
  // Legacy rows written before duration tracking existed always recorded 0;
  // showing "0ms" reads as an instant run instead of "never measured".
  if (durationMs === 0) return "";
  if (durationMs < 1000) return `${durationMs}ms`;
  if (durationMs < 60_000) return `${(durationMs / 1000).toFixed(1)}s`;
  return `${Math.round(durationMs / 60_000)}m`;
}

function threadHref(run: ScheduleRun): string {
  return `#/chat/${encodeURIComponent(run.sessionId)}`;
}

function Pill({ tone, children }: { tone: string; children: string }) {
  return <span className={`scheduled-pill tone-${tone}`}>{children}</span>;
}

function RunPill({ run }: { run: ScheduleRun }) {
  return <Pill tone={RUN_STATUS_TONES[run.status]}>{RUN_STATUS_LABELS[run.status]}</Pill>;
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
    <div className="scheduled-loading" aria-busy="true">
      <p className="sr-only" role="status">
        Loading scheduled tasks…
      </p>
      <div className="scheduled-skeleton" aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
    </div>
  );
}

function TaskEditor({
  mode,
  draft,
  templates,
  cadences,
  errors,
  saving,
  onChange,
  onCancel,
  onSubmit,
}: {
  mode: "create" | "edit";
  draft: Draft;
  templates: ScheduleTemplate[];
  cadences: ScheduleCadence[];
  errors: Record<string, string>;
  saving: boolean;
  onChange: (draft: Draft) => void;
  onCancel: () => void;
  onSubmit: (event: FormEvent) => void;
}) {
  const label = mode === "create" ? "New scheduled task" : "Edit scheduled task";
  const template = templates.find((item) => item.id === draft.templateId);
  const cadenceIds = mode === "create" && template ? template.cadences : cadences.map((c) => c.id);
  return (
    <form className="scheduled-editor" aria-label={label} onSubmit={onSubmit} noValidate>
      {mode === "create" && templates.length > 1 && (
        <label className="scheduled-field">
          <span>Template</span>
          <select
            value={draft.templateId}
            disabled={saving}
            onChange={(event) => {
              const next = templates.find((item) => item.id === event.target.value);
              if (next) onChange(draftFromTemplate(next));
            }}
          >
            {templates.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
      )}
      <label className="scheduled-field">
        <span>Name</span>
        <input
          value={draft.name}
          maxLength={80}
          disabled={saving}
          aria-invalid={Boolean(errors.name) || undefined}
          aria-describedby={errors.name ? "scheduled-name-error" : undefined}
          onChange={(event) => onChange({ ...draft, name: event.target.value })}
        />
      </label>
      {errors.name && (
        <p id="scheduled-name-error" className="scheduled-field-error" role="alert">
          {errors.name}
        </p>
      )}
      <label className="scheduled-field">
        <span>Instructions</span>
        <textarea
          rows={4}
          value={draft.prompt}
          disabled={saving}
          aria-invalid={Boolean(errors.prompt) || undefined}
          aria-describedby={errors.prompt ? "scheduled-prompt-error" : undefined}
          onChange={(event) => onChange({ ...draft, prompt: event.target.value })}
        />
      </label>
      {errors.prompt && (
        <p id="scheduled-prompt-error" className="scheduled-field-error" role="alert">
          {errors.prompt}
        </p>
      )}
      <label className="scheduled-field">
        <span>Repeats</span>
        <select
          value={draft.cadence}
          disabled={saving}
          aria-invalid={Boolean(errors.cadence) || undefined}
          aria-describedby={errors.cadence ? "scheduled-cadence-error" : undefined}
          onChange={(event) => onChange({ ...draft, cadence: event.target.value })}
        >
          {cadenceIds.map((id) => (
            <option key={id} value={id}>
              {cadenceLabel(cadences, id)}
            </option>
          ))}
        </select>
      </label>
      {errors.cadence && (
        <p id="scheduled-cadence-error" className="scheduled-field-error" role="alert">
          {errors.cadence}
        </p>
      )}
      {errors.form && (
        <p className="scheduled-field-error" role="alert">
          {errors.form}
        </p>
      )}
      <div className="scheduled-editor-actions">
        <button type="button" className="scheduled-button" onClick={onCancel} disabled={saving}>
          <Icon name="close" />
          Cancel
        </button>
        <button type="submit" className="scheduled-button is-primary" disabled={saving}>
          <Icon name="check" />
          {saving ? "Saving…" : mode === "create" ? "Create task" : "Save"}
        </button>
      </div>
    </form>
  );
}

function validate(draft: Draft): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!draft.name.trim()) errors.name = "Enter a name.";
  if (!draft.prompt.trim()) errors.prompt = "Enter instructions.";
  if (!draft.cadence) errors.cadence = "Choose how often it repeats.";
  return errors;
}

function saveErrors(error: unknown, fallback: string): Record<string, string> {
  if (error instanceof ScheduleApiError && error.code === "invalid_routine") {
    return Object.keys(error.fieldErrors).length ? error.fieldErrors : { form: fallback };
  }
  return { form: fallback };
}

function TaskTable({
  jobs,
  runs,
  cadences,
}: {
  jobs: ScheduleJob[];
  runs: ScheduleRun[];
  cadences: ScheduleCadence[];
}) {
  const latest = useMemo(() => {
    const byJob = new Map<number, ScheduleRun>();
    for (const run of runs) {
      const current = byJob.get(run.jobId);
      if (!current || run.id > current.id) byJob.set(run.jobId, run);
    }
    return byJob;
  }, [runs]);
  return (
    <div className="scheduled-list">
      <table className="scheduled-table">
        <thead>
          <tr>
            <th scope="col">Task</th>
            <th scope="col">
              <span className="scheduled-th">
                <Icon name="repeat" />
                Repeats
              </span>
            </th>
            <th scope="col">
              <span className="scheduled-th">
                <Icon name="clock" />
                Next run
              </span>
            </th>
            <th scope="col">
              <span className="scheduled-th">
                <Icon name="refresh" />
                Last run
              </span>
            </th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => {
            const last = latest.get(job.id);
            const href = `#/scheduled/${job.id}`;
            return (
              <tr
                key={job.id}
                className="scheduled-row"
                onClick={(event) => {
                  if ((event.target as HTMLElement).closest("a")) return;
                  window.location.hash = href;
                }}
              >
                <td>
                  <a href={href} aria-label={`Open ${job.name}`}>
                    {job.name}
                  </a>
                </td>
                <td>
                  <span className="scheduled-cell">
                    <Icon name="repeat" />
                    {cadenceLabel(cadences, job.cadence)}
                  </span>
                </td>
                <td>
                  {job.enabled && job.nextRunAt ? (
                    <span className="scheduled-cell">
                      <Icon name="clock" />
                      <time dateTime={job.nextRunAt}>{formatTimestamp(job.nextRunAt)}</time>
                    </span>
                  ) : (
                    <Pill tone="neutral">Paused</Pill>
                  )}
                </td>
                <td>
                  {last ? (
                    <span className="scheduled-last-run">
                      <RunPill run={last} />
                      <time dateTime={last.startedAt}>{formatTimestamp(last.startedAt)}</time>
                    </span>
                  ) : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="scheduled-footer">
        {jobs.length} {jobs.length === 1 ? "task" : "tasks"}
      </p>
    </div>
  );
}

function RunReceipt({ run }: { run: ScheduleRun }) {
  const duration = formatDuration(run.durationMs, run.status);
  return (
    <article
      className={`scheduled-receipt receipt-${run.status}`}
      aria-label={`${RUN_STATUS_LABELS[run.status]} run ${run.id}`}
    >
      <header>
        <RunPill run={run} />
        <time dateTime={run.startedAt}>{formatTimestamp(run.startedAt)}</time>
        {duration && <span className="scheduled-duration">{duration}</span>}
        <a className="scheduled-link" href={threadHref(run)}>
          <Icon name="chat" />
          Open thread
        </a>
      </header>
      {run.status === "running" ? (
        <p className="scheduled-receipt-line">
          <Icon name="clock" />
          <span>In progress</span>
        </p>
      ) : (
        <ScheduleMarkdown>{run.summary || "No summary was recorded."}</ScheduleMarkdown>
      )}
      {run.status === "waiting_approval" && (
        <p className="scheduled-receipt-line">
          <Icon name="approve" />
          <span>
            {run.waitingApprovalCount} approval{run.waitingApprovalCount === 1 ? "" : "s"} waiting
          </span>
        </p>
      )}
      {run.artifacts.length > 0 && (
        <ul className="scheduled-artifacts" aria-label="Run artifacts">
          {run.artifacts.map((artifact) => (
            <li key={artifact.id}>
              <Icon name="open" />
              {artifact.externalUrl ? (
                <a href={artifact.externalUrl} target="_blank" rel="noopener noreferrer">
                  {artifact.title}
                </a>
              ) : (
                <span>{artifact.title}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}

function RunHistory({ runs }: { runs: ScheduleRun[] }) {
  const [selectedId, setSelectedId] = useState<number | null>(runs[0]?.id ?? null);
  // A new run (Run now, or a tick while open) becomes the selected one.
  useEffect(() => {
    setSelectedId(runs[0]?.id ?? null);
  }, [runs[0]?.id]);
  const selected = runs.find((run) => run.id === selectedId) || runs[0] || null;
  return (
    <section className="scheduled-history" aria-labelledby="scheduled-history-heading">
      <h2 id="scheduled-history-heading">
        History
        <span className="scheduled-count">{runs.length}</span>
      </h2>
      {runs.length === 0 ? (
        <p className="scheduled-history-empty">
          <Icon name="clock" />
          <span>No runs yet</span>
        </p>
      ) : (
        <div className="scheduled-history-grid">
          <ul className="scheduled-runs" aria-label="Runs">
            {runs.map((run) => {
              const duration = formatDuration(run.durationMs, run.status);
              return (
                <li key={run.id} aria-current={selected?.id === run.id || undefined}>
                  <button
                    type="button"
                    aria-pressed={selected?.id === run.id}
                    onClick={() => setSelectedId(run.id)}
                  >
                    <time dateTime={run.startedAt}>{formatTimestamp(run.startedAt)}</time>
                    <RunPill run={run} />
                    <span className="scheduled-duration">{duration}</span>
                  </button>
                  <a
                    className="scheduled-run-thread"
                    href={threadHref(run)}
                    aria-label={`Open thread for the ${formatTimestamp(run.startedAt)} run`}
                    title="Open thread"
                  >
                    <Icon name="chat" />
                  </a>
                </li>
              );
            })}
          </ul>
          {selected && <RunReceipt run={selected} />}
        </div>
      )}
    </section>
  );
}

function TaskDetail({
  job,
  runs,
  cadences,
  notice,
  busy,
  onRun,
  onToggle,
  onSave,
}: {
  job: ScheduleJob;
  runs: ScheduleRun[];
  cadences: ScheduleCadence[];
  notice: Notice | null;
  busy: boolean;
  onRun: () => void;
  onToggle: () => void;
  onSave: (draft: Draft) => Promise<Record<string, string> | null>;
}) {
  const [draft, setDraft] = useState<Draft | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const running = runs.some((run) => run.status === "running");

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!draft || saving) return;
    const local = validate(draft);
    if (Object.keys(local).length) {
      setErrors(local);
      return;
    }
    setSaving(true);
    const failed = await onSave(draft);
    setSaving(false);
    if (failed) {
      setErrors(failed);
      return;
    }
    setDraft(null);
    setErrors({});
  }

  return (
    <>
      <a className="scheduled-back" href="#/scheduled" aria-label="Back to Scheduled tasks">
        <Icon name="back" />
        Scheduled
      </a>
      <header className="scheduled-detail-header">
        <div className="scheduled-heading">
          <h1>{job.name}</h1>
          {running ? (
            <Pill tone="running">Running</Pill>
          ) : job.enabled ? (
            <Pill tone="ok">Active</Pill>
          ) : (
            <Pill tone="neutral">Paused</Pill>
          )}
        </div>
        <div className="scheduled-actions">
          <button
            type="button"
            className="scheduled-button is-primary"
            aria-label={running ? `Running ${job.name}` : `Run ${job.name} now`}
            aria-busy={running || undefined}
            disabled={running}
            onClick={onRun}
          >
            <Icon name="play" />
            {running ? "Running…" : "Run now"}
          </button>
          <button
            type="button"
            className="scheduled-button"
            aria-label={job.enabled ? `Pause ${job.name}` : `Resume ${job.name}`}
            disabled={busy}
            onClick={onToggle}
          >
            <Icon name={job.enabled ? "pause" : "play"} />
            {job.enabled ? "Pause" : "Resume"}
          </button>
          <button
            type="button"
            className="scheduled-button"
            aria-label={`Edit ${job.name}`}
            disabled={Boolean(draft)}
            onClick={() => {
              setErrors({});
              setDraft({
                templateId: job.templateId,
                cadence: job.cadence,
                name: job.name,
                prompt: job.prompt,
              });
            }}
          >
            <Icon name="edit" />
            Edit
          </button>
        </div>
      </header>

      {notice && (
        <p
          className={`scheduled-notice tone-${notice.tone}`}
          role={notice.tone === "error" ? "alert" : "status"}
        >
          <Icon name="warn" />
          <span>{notice.text}</span>
        </p>
      )}

      {draft ? (
        <TaskEditor
          mode="edit"
          draft={draft}
          templates={[]}
          cadences={cadences}
          errors={errors}
          saving={saving}
          onChange={setDraft}
          onCancel={() => {
            setDraft(null);
            setErrors({});
          }}
          onSubmit={(event) => void submit(event)}
        />
      ) : (
        <div className="scheduled-summary">
          <ul className="scheduled-attrs">
            <li>
              <Icon name="repeat" />
              <span>{cadenceLabel(cadences, job.cadence)}</span>
            </li>
            <li>
              <Icon name="clock" />
              {job.enabled && job.nextRunAt ? (
                <span>
                  Next run{" "}
                  <time dateTime={job.nextRunAt}>{formatTimestamp(job.nextRunAt)}</time>
                </span>
              ) : (
                <span>Paused</span>
              )}
            </li>
          </ul>
          <section aria-labelledby="scheduled-instructions-heading">
            <h2 id="scheduled-instructions-heading">Instructions</h2>
            <p className="scheduled-prompt">{job.prompt}</p>
          </section>
        </div>
      )}

      <RunHistory runs={runs} />
    </>
  );
}

export function ScheduledPage({ jobId }: { jobId?: number } = {}) {
  const [state, setState] = useState<PageState>({ status: "loading" });
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [createErrors, setCreateErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [notices, setNotices] = useState<Record<number, Notice>>({});
  const [toggling, setToggling] = useState<number | null>(null);
  const [searchQuery, setSearchQuery] = useState("");

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

  const anyRunning =
    state.status === "loaded" && state.schedule.runs.some((run) => run.status === "running");

  // A run finishes in the background; re-read until none is running.
  useEffect(() => {
    if (!anyRunning) return;
    let active = true;
    const timer = window.setInterval(() => {
      getSchedule().then(
        (schedule) => {
          if (!active) return;
          setState({ status: "loaded", schedule });
          if (!schedule.runs.some((run) => run.status === "running")) {
            window.dispatchEvent(new CustomEvent("sourcecado:inbox-changed"));
            window.dispatchEvent(new CustomEvent("sourcecado:schedule-changed"));
          }
        },
        () => {},
      );
    }, RUN_POLL_MS);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [anyRunning]);

  const replaceJob = useCallback((job: ScheduleJob) => {
    setState((current) =>
      current.status === "loaded"
        ? {
            status: "loaded",
            schedule: {
              ...current.schedule,
              jobs: current.schedule.jobs.map((item) => (item.id === job.id ? job : item)),
            },
          }
        : current,
    );
    window.dispatchEvent(new CustomEvent("sourcecado:schedule-changed"));
  }, []);

  function setNotice(id: number, notice: Notice | null) {
    setNotices((current) => {
      const next = { ...current };
      if (notice) next[id] = notice;
      else delete next[id];
      return next;
    });
  }

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
        // Active tasks by next run, then paused tasks by name.
        if (left.enabled !== right.enabled) return left.enabled ? -1 : 1;
        if (left.nextRunAt && right.nextRunAt && left.nextRunAt !== right.nextRunAt) {
          return left.nextRunAt.localeCompare(right.nextRunAt);
        }
        return left.name.localeCompare(right.name);
      });
  }, [searchQuery, state]);

  const selectedJob =
    state.status === "loaded" && jobId !== undefined
      ? state.schedule.jobs.find((job) => job.id === jobId)
      : undefined;
  const selectedRuns =
    state.status === "loaded" && selectedJob
      ? state.schedule.runs
          .filter((run) => run.jobId === selectedJob.id)
          .sort((left, right) => right.id - left.id)
      : [];

  function openCreate() {
    if (state.status !== "loaded") return;
    const template = state.schedule.templates[0];
    if (!template) return;
    setDraft(draftFromTemplate(template));
    setCreateErrors({});
  }

  async function submitCreate(event: FormEvent) {
    event.preventDefault();
    if (state.status !== "loaded" || !draft || saving) return;
    const errors = validate(draft);
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
              schedule: { ...current.schedule, jobs: [...current.schedule.jobs, created] },
            }
          : current,
      );
      setDraft(null);
      window.dispatchEvent(new CustomEvent("sourcecado:schedule-changed"));
      window.location.hash = `#/scheduled/${created.id}`;
    } catch (error) {
      setCreateErrors(saveErrors(error, "This task couldn’t be created. Try again."));
    } finally {
      setSaving(false);
    }
  }

  async function saveEdit(job: ScheduleJob, edit: Draft): Promise<Record<string, string> | null> {
    try {
      replaceJob(
        await updateScheduleJob(job.id, {
          name: edit.name.trim(),
          prompt: edit.prompt.trim(),
          cadence: edit.cadence,
        }),
      );
      return null;
    } catch (error) {
      return saveErrors(error, "This task couldn’t be saved. Try again.");
    }
  }

  async function toggle(job: ScheduleJob) {
    if (toggling !== null) return;
    setToggling(job.id);
    setNotice(job.id, null);
    try {
      replaceJob(await updateScheduleJob(job.id, { enabled: !job.enabled }));
    } catch {
      setNotice(job.id, {
        tone: "error",
        text: job.enabled ? "Couldn’t pause this task. Try again." : "Couldn’t resume this task. Try again.",
      });
    } finally {
      setToggling(null);
    }
  }

  async function runNow(job: ScheduleJob) {
    setNotice(job.id, null);
    try {
      const { run } = await runScheduleJob(job.id);
      setState((current) =>
        current.status === "loaded"
          ? {
              status: "loaded",
              schedule: {
                ...current.schedule,
                runs: [...current.schedule.runs.filter((item) => item.id !== run.id), run],
              },
            }
          : current,
      );
    } catch (error) {
      setNotice(
        job.id,
        error instanceof ScheduleApiError && error.code === "already_running"
          ? { tone: "warn", text: "Already running. Wait for the current run to finish." }
          : { tone: "error", text: "Couldn’t start the run. Try again." },
      );
    }
  }

  return (
    <main className={`route-page scheduled-page${jobId !== undefined ? " is-detail" : ""}`}>
      {jobId === undefined && (
        <header className="scheduled-titlebar">
          <div className="scheduled-heading">
            <h1>Scheduled</h1>
            {state.status === "loaded" && (
              <span className="scheduled-count">
                {state.schedule.jobs.length}
                <span className="sr-only">
                  {state.schedule.jobs.length === 1 ? " task" : " tasks"}
                </span>
              </span>
            )}
          </div>
          <div className="scheduled-actions">
            {state.status === "loaded" && state.schedule.jobs.length > 0 && (
              <label className="scheduled-search">
                <Icon name="search" />
                <input
                  type="search"
                  aria-label="Search scheduled tasks"
                  placeholder="Search"
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.target.value)}
                />
              </label>
            )}
            {/* With no tasks, the empty state carries the one New task button. */}
            {state.status === "loaded" && state.schedule.jobs.length > 0 && (
              <button
                type="button"
                className="scheduled-button is-primary"
                onClick={openCreate}
                disabled={!state.schedule.templates.length || Boolean(draft)}
              >
                <Icon name="add" />
                New task
              </button>
            )}
          </div>
        </header>
      )}

      {state.status === "loading" && <LoadingSchedule />}
      {state.status === "failed" && (
        <section className="route-error" role="alert">
          {jobId === undefined ? (
            <h2>Scheduled tasks couldn’t be loaded</h2>
          ) : (
            <h1>Scheduled tasks couldn’t be loaded</h1>
          )}
          <button
            type="button"
            className="scheduled-button"
            aria-label="Retry loading scheduled tasks"
            onClick={() => setLoadAttempt((attempt) => attempt + 1)}
          >
            <Icon name="refresh" />
            Retry
          </button>
        </section>
      )}
      {state.status === "loaded" && jobId !== undefined && selectedJob && (
        <TaskDetail
          key={selectedJob.id}
          job={selectedJob}
          runs={selectedRuns}
          cadences={state.schedule.cadences}
          notice={notices[selectedJob.id] || null}
          busy={toggling === selectedJob.id}
          onRun={() => void runNow(selectedJob)}
          onToggle={() => void toggle(selectedJob)}
          onSave={(edit) => saveEdit(selectedJob, edit)}
        />
      )}
      {state.status === "loaded" && jobId !== undefined && !selectedJob && (
        <section className="route-error" role="alert">
          <h1>Scheduled task not found</h1>
          <a href="#/scheduled">
            <Icon name="back" />
            Back to Scheduled tasks
          </a>
        </section>
      )}
      {state.status === "loaded" && jobId === undefined && (
        <>
          {draft && (
            <TaskEditor
              mode="create"
              draft={draft}
              templates={state.schedule.templates}
              cadences={state.schedule.cadences}
              errors={createErrors}
              saving={saving}
              onChange={setDraft}
              onCancel={() => {
                setDraft(null);
                setCreateErrors({});
              }}
              onSubmit={(event) => void submitCreate(event)}
            />
          )}
          {state.schedule.jobs.length === 0 && !draft && (
            <section className="scheduled-empty">
              <Icon name="clock" />
              <h2>No scheduled tasks</h2>
              <button
                type="button"
                className="scheduled-button is-primary"
                onClick={openCreate}
                disabled={!state.schedule.templates.length}
              >
                <Icon name="add" />
                New task
              </button>
            </section>
          )}
          {visibleJobs.length > 0 && (
            <TaskTable
              jobs={visibleJobs}
              runs={state.schedule.runs}
              cadences={state.schedule.cadences}
            />
          )}
          {state.schedule.jobs.length > 0 && visibleJobs.length === 0 && (
            <section className="scheduled-empty">
              <Icon name="search" />
              <h2>No tasks match “{searchQuery.trim()}”</h2>
              <button type="button" className="scheduled-button" onClick={() => setSearchQuery("")}>
                <Icon name="close" />
                Clear search
              </button>
            </section>
          )}
        </>
      )}
    </main>
  );
}
