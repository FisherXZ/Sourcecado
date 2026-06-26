import { StepRow, PendingRow } from "./StepRow";
import type { ChatStep } from "./stream";

// The collapsible reasoning trace. While the agent runs it auto-expands and a live
// PendingRow sits at the tail; once the answer lands the container collapses it to
// a one-line "Reasoning · N steps". Collapse animates via grid-template-rows
// (animation-safe) in globals.css.
export function ReasoningTrace({
  steps,
  running,
  open,
  onToggle,
}: {
  steps: ChatStep[];
  running: boolean;
  open: boolean;
  onToggle: () => void;
}) {
  const count = steps.length;
  const pendingLabel = count === 0 ? "Searching memory" : "Composing answer";

  return (
    <div className="reasoning-trace">
      <button
        type="button"
        aria-expanded={open}
        onClick={onToggle}
        className="flex items-center gap-1.5 text-[12px] font-medium text-muted transition-colors hover:text-text focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-accent-tint rounded-[4px]"
      >
        <span className={`chevron ${open ? "chevron--open" : ""}`} aria-hidden>
          ▸
        </span>
        <span>Reasoning</span>
        {count > 0 ? (
          <span className="font-mono text-[11px] text-muted">
            · {count} step{count === 1 ? "" : "s"}
          </span>
        ) : null}
      </button>

      <div className="reasoning-body" data-open={open}>
        <ul className="reasoning-list mt-1.5 flex flex-col gap-1">
          {steps.map((s) => (
            <StepRow key={s.index} step={s} />
          ))}
          {running ? <PendingRow label={pendingLabel} /> : null}
        </ul>
      </div>
    </div>
  );
}
