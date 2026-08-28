import { useEffect, useMemo, useState, type FormEvent } from "react";

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

function formatDuration(durationMs: number): string {
  // Legacy rows written before duration tracking existed always recorded 0;
  // showing "0ms" reads as an instant run instead of "never measured".
  if (durationMs === 0) return "Legacy run";
  if (durationMs < 1000) return `${durationMs}ms`;
  return `${(durationMs / 1000).toFixed(1)}s`;
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

function ScheduleReceipt({ receipt }: { receipt: ScheduleRun }) {
  const label = RUN_STATUS_LABELS[receipt.status];
  return (
    <li
      className={`schedule-receipt receipt-${receipt.status}`}
      aria-label={`${label} run ${receipt.id}`}
    >
      <header>
        <span className="schedule-run-status">{label}</span>
        <span>{formatDuration(receipt.durationMs)}</span>
      </header>
      <p>{receipt.summary || "No summary was recorded."}</p>
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

function ScheduleJobCard({
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
  return (
    <article className="schedule-job" aria-label={job.name}>
      <header className="schedule-job-header">
        <div>
          <p className="schedule-eyebrow">{CADENCE_LABELS[job.cadence] || job.cadence}</p>
          <h2>{job.name}</h2>
        </div>
        <button
          type="button"
          aria-label={running ? `Running ${job.name}` : `Run ${job.name} now`}
          aria-busy={running || undefined}
          disabled={running}
          onClick={onRun}
        >
          {running ? "Running…" : "Run now"}
        </button>
      </header>

      <p className="schedule-job-prompt">{job.prompt}</p>
      <dl className="schedule-job-facts">
        <div>
          <dt>Next run</dt>
          <dd>
            <time dateTime={job.nextRunAt || undefined}>{formatTimestamp(job.nextRunAt)}</time>
          </dd>
        </div>
        <div>
          <dt>Cadence</dt>
          <dd>{CADENCE_LABELS[job.cadence] || job.cadence}</dd>
        </div>
      </dl>

      {feedback?.kind === "running" && (
        <p className="schedule-run-feedback" role="status">
          Running now. The next scheduled time remains unchanged.
        </p>
      )}
      {feedback?.kind === "already_running" && (
        <p className="schedule-run-feedback warning" role="status">
          <strong>Already running.</strong> {feedback.message}
        </p>
      )}
      {feedback?.kind === "failed" && (
        <div className="schedule-run-feedback error" role="alert">
          <strong>This run couldn’t be started.</strong>
          <span>Check that Sourcecado is available, then try again.</span>
        </div>
      )}

      <section className="schedule-receipts" aria-labelledby={`schedule-runs-${job.id}`}>
        <h3 id={`schedule-runs-${job.id}`}>Run receipts</h3>
        {receipts.length === 0 ? (
          <p>No runs yet. Run it now or wait for the next scheduled time.</p>
        ) : (
          <ul aria-label="Run receipts">
            {receipts.map((receipt) => (
              <ScheduleReceipt key={receipt.id} receipt={receipt} />
            ))}
          </ul>
        )}
      </section>
    </article>
  );
}

export function ScheduledPage() {
  const [state, setState] = useState<PageState>({ status: "loading" });
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [showCreate, setShowCreate] = useState(false);
  const [draft, setDraft] = useState<CreateDraft | null>(null);
  const [createErrors, setCreateErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [createFeedback, setCreateFeedback] = useState<string | null>(null);
  const [runFeedback, setRunFeedback] = useState<Record<number, RunFeedback>>({});

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

  function openCreate() {
    if (state.status !== "loaded") return;
    const firstTemplate = state.schedule.templates?.[0];
    if (!firstTemplate) return;
    setDraft(draftFromTemplate(firstTemplate));
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
      <header className="scheduled-page-header">
        <div>
          <h1>Scheduled</h1>
          <p>Run repeatable sourcing work and keep a durable receipt for every attempt.</p>
        </div>
        {state.status === "loaded" && state.schedule.jobs.length > 0 && !showCreate && (
          <button type="button" onClick={openCreate} disabled={!state.schedule.templates?.length}>
            Create automation
          </button>
        )}
      </header>

      {state.status === "loading" && <LoadingSchedule />}
      {state.status === "failed" && (
        <section className="route-error" role="alert">
          <h2>Automations couldn’t be loaded</h2>
          <p>Check that Sourcecado is available, then try again.</p>
          <button type="button" onClick={() => setLoadAttempt((attempt) => attempt + 1)}>
            Retry loading automations
          </button>
        </section>
      )}
      {state.status === "loaded" && (
        <>
          {waitingApprovalCount > 0 && (
            <aside className="schedule-inbox-summary" role="status">
              <strong>
                {waitingApprovalCount} approval{waitingApprovalCount === 1 ? "" : "s"} waiting in
                Inbox
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
                onClick={openCreate}
                disabled={!state.schedule.templates?.length}
              >
                Create automation
              </button>
            </section>
          )}
          {state.schedule.jobs.length > 0 && (
            <div className="schedule-jobs">
              {state.schedule.jobs.map((job) => (
                <ScheduleJobCard
                  key={job.id}
                  job={job}
                  receipts={state.schedule.runs
                    .filter((receipt) => receipt.jobId === job.id)
                    .sort((a, b) => b.id - a.id)}
                  feedback={runFeedback[job.id]}
                  onRun={() => void runNow(job)}
                />
              ))}
            </div>
          )}
        </>
      )}
    </main>
  );
}
