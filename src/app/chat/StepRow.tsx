import type { ChatStep } from "./stream";

// A settled reasoning step. data-state drives the left-rule colour in globals.css:
// greige when done, brick when the tool errored. avocado is reserved for the live
// PendingRow — the "avocado = live, greige = settled" language.
export function StepRow({ step }: { step: ChatStep }) {
  return (
    <li data-state={step.ok ? "done" : "error"} className="step-row flex flex-col gap-0.5 pl-3 py-1">
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-[11px] text-muted">{step.tool}</span>
        <span className={`text-[12px] ${step.ok ? "text-muted" : "text-neg-tx"}`}>{step.detail}</span>
      </div>
      {step.thought ? <span className="text-[11px] leading-snug text-muted/80">{step.thought}</span> : null}
    </li>
  );
}

// The single live row at the tail of the trace while the agent is still working.
export function PendingRow({ label }: { label: string }) {
  return (
    <li
      data-state="working"
      aria-busy="true"
      className="step-row step-row--pending flex items-center gap-2 pl-3 py-1 text-[12px] text-accent-deep"
    >
      <span className="step-dot" aria-hidden />
      <span>{label}…</span>
    </li>
  );
}
