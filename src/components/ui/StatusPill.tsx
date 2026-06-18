import type { ReactNode } from "react";

type Tone = "go" | "draft" | "reply" | "warn" | "error";

const tones: Record<Tone, string> = {
  go: "bg-accent-tint text-accent-deep",
  draft: "bg-warn-bg text-warn-tx",
  reply: "bg-pit-tint text-pit",
  warn: "bg-warn-bg text-warn-tx",
  error: "bg-neg-bg text-neg-tx",
};

export function StatusPill({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11.5px] font-medium ${tones[tone]}`}
    >
      <span className="h-[5px] w-[5px] rounded-full bg-current opacity-70" />
      {children}
    </span>
  );
}

export function Tag({ children }: { children: ReactNode }) {
  return (
    <span className="mr-1 rounded-[4px] border border-border px-1.5 py-0.5 text-[11px] text-muted">
      {children}
    </span>
  );
}
