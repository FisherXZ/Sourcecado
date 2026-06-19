import { notFound } from "next/navigation";
import { getDb } from "@/lib/db";
import { getRunTrace, type RunStepTrace } from "@/lib/ledger";

interface RunPageProps {
  params: Promise<{ id: string }>;
}

const jsonPreviewLimit = 20_000;

export default async function RunPage({ params }: RunPageProps) {
  const { id } = await params;
  const runId = Number(id);
  if (!Number.isInteger(runId) || runId <= 0) {
    notFound();
  }

  const trace = await getRunTrace(getDb(), runId);
  if (!trace) {
    notFound();
  }

  return (
    <main className="mx-auto flex max-w-5xl flex-col gap-6 px-6 py-8">
      <header className="border-b border-foreground/10 pb-5">
        <div className="text-sm text-foreground/55">{trace.runType}</div>
        <div className="mt-1 flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">{trace.title ?? `Run ${trace.id}`}</h1>
          <StatusBadge status={trace.status} />
        </div>
        <div className="mt-2 text-sm text-foreground/60">
          Started {formatDate(trace.startedAt)}
          {trace.completedAt ? ` · Completed ${formatDate(trace.completedAt)}` : ""}
        </div>
      </header>

      {trace.errorMessage ? (
        <section className="rounded border border-red-300 bg-red-50 p-4 text-sm text-red-950">
          <div className="font-medium">{trace.errorType ?? "Run error"}</div>
          <div>{trace.errorMessage}</div>
        </section>
      ) : null}

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold">Steps</h2>
        {trace.steps.length > 0 ? (
          <div className="flex flex-col gap-3">
            {trace.steps.map((step) => (
              <StepNode key={step.id} step={step} depth={0} />
            ))}
          </div>
        ) : (
          <p className="text-sm text-foreground/60">No steps recorded.</p>
        )}
      </section>
    </main>
  );
}

function StepNode({ step, depth }: { step: RunStepTrace; depth: number }) {
  return (
    <div className="border-l border-foreground/15 pl-4" style={{ marginLeft: depth * 16 }}>
      <div className="rounded border border-foreground/10 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs uppercase text-foreground/50">{step.stepKind}</span>
          <h3 className="text-base font-medium">{step.name}</h3>
          <StatusBadge status={step.status} />
        </div>
        <div className="mt-1 text-xs text-foreground/55">
          Started {formatDate(step.startedAt)}
          {step.completedAt ? ` · Completed ${formatDate(step.completedAt)}` : ""}
        </div>
        {step.errorMessage ? (
          <div className="mt-3 rounded border border-red-300 bg-red-50 p-3 text-sm text-red-950">
            <div className="font-medium">{step.errorType ?? "Step error"}</div>
            <div>{step.errorMessage}</div>
          </div>
        ) : null}
        <JsonPreview label="Input" value={step.input} />
        <JsonPreview label="Output" value={step.output} />
        {step.modelCalls.length > 0 ? (
          <div className="mt-4">
            <div className="text-sm font-medium">Model calls</div>
            <div className="mt-2 flex flex-col gap-2">
              {step.modelCalls.map((call) => (
                <div key={call.id} className="rounded bg-foreground/[0.04] p-3 text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <span>{call.taskName}</span>
                    <StatusBadge status={call.status} />
                  </div>
                  <div className="mt-1 text-xs text-foreground/60">
                    {call.provider} · {call.model} · {call.callKind}
                    {call.totalTokens !== null ? ` · ${call.totalTokens} tokens` : ""}
                  </div>
                  {call.errorMessage ? <div className="mt-2 text-red-700">{call.errorMessage}</div> : null}
                  <JsonPreview label="Request" value={call.request} />
                  <JsonPreview label="Response" value={call.response} />
                </div>
              ))}
            </div>
          </div>
        ) : null}
        {step.toolCalls.length > 0 ? (
          <div className="mt-4">
            <div className="text-sm font-medium">Tool calls</div>
            <div className="mt-2 flex flex-col gap-2">
              {step.toolCalls.map((call) => (
                <div key={call.id} className="rounded bg-foreground/[0.04] p-3 text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <span>{call.toolName}</span>
                    <StatusBadge status={call.status} />
                  </div>
                  {call.errorMessage ? <div className="mt-2 text-red-700">{call.errorMessage}</div> : null}
                  <JsonPreview label="Arguments" value={call.arguments} />
                  <JsonPreview label="Result" value={call.result} />
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>
      {step.children.length > 0 ? (
        <div className="mt-3 flex flex-col gap-3">
          {step.children.map((child) => (
            <StepNode key={child.id} step={child} depth={depth + 1} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  return (
    <span className="rounded border border-foreground/15 px-2 py-0.5 text-xs text-foreground/70">
      {status}
    </span>
  );
}

function JsonPreview({ label, value }: { label: string; value: unknown }) {
  if (value === null || value === undefined) {
    return null;
  }
  const formatted = JSON.stringify(value, null, 2);
  const preview =
    formatted.length > jsonPreviewLimit
      ? `${formatted.slice(0, jsonPreviewLimit)}\n... truncated ${formatted.length - jsonPreviewLimit} chars`
      : formatted;

  return (
    <details className="mt-3 rounded border border-foreground/10 bg-background">
      <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-foreground/70">{label}</summary>
      <pre className="max-h-96 overflow-auto border-t border-foreground/10 p-3 text-xs leading-relaxed">
        {preview}
      </pre>
    </details>
  );
}

function formatDate(value: Date) {
  return new Intl.DateTimeFormat("en", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(value);
}
