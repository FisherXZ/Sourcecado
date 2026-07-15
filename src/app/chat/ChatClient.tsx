"use client";

import { useEffect, useRef, useState } from "react";
import { EmptyState } from "@/components/ui";
import { Composer } from "./Composer";
import { MessageBubble } from "./MessageBubble";
import { ReasoningTrace } from "./ReasoningTrace";
import { runChat, type AssistantTurn, type ChatMeta, type ConversationTurn } from "./stream";

interface Exchange {
  id: number;
  question: string;
  turn: AssistantTurn;
  open: boolean; // reasoning trace expanded?
  done: boolean;
  errored?: boolean;
}

// A run that never settles would leave the "live" reasoning trace pulsing forever
// (design-review P0-1). Abort after this so a hung stream resolves into an error.
const RUN_TIMEOUT_MS = 90_000;

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

export function ChatClient() {
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const idRef = useRef(0);
  const bottomRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef(true);

  // Stick to the bottom while streaming, but never yank a user who scrolled up.
  useEffect(() => {
    function onScroll() {
      pinnedRef.current =
        window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 120;
    }
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  useEffect(() => {
    const el = bottomRef.current;
    if (pinnedRef.current && el && typeof el.scrollIntoView === "function") {
      // JS smooth-scroll is NOT downgraded by the prefers-reduced-motion CSS
      // block (design-review P1-3) — branch on it explicitly.
      el.scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth", block: "end" });
    }
  }, [exchanges]);

  function patch(id: number, update: (e: Exchange) => Exchange) {
    setExchanges((prev) => prev.map((e) => (e.id === id ? update(e) : e)));
  }

  function submit() {
    const question = input.trim();
    if (!question || busy) return;

    const history: ConversationTurn[] = exchanges
      .filter((e) => e.done && !e.errored && e.turn.answer)
      .flatMap((e) => [
        { role: "user", content: e.question },
        { role: "assistant", content: e.turn.answer },
      ]);

    const id = ++idRef.current;
    setExchanges((prev) => [...prev, { id, question, turn: { steps: [], answer: "" }, open: true, done: false }]);
    setInput("");
    setBusy(true);

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), RUN_TIMEOUT_MS);

    runChat(question, history, (turn) => patch(id, (e) => ({ ...e, turn })), controller.signal)
      .then((finalTurn) => patch(id, (e) => ({ ...e, turn: finalTurn, done: true, open: false })))
      .catch((err) => {
        const message = controller.signal.aborted
          ? "The run timed out before completing. Try again."
          : err instanceof Error
            ? err.message
            : "The run could not complete.";
        // errored: true settles the trace (no live row) and surfaces the failure
        // as an alert — the "everything goes greige" thesis must hold on failure.
        patch(id, (e) => ({ ...e, errored: true, done: true, open: false, turn: { ...e.turn, answer: message } }));
      })
      .finally(() => {
        clearTimeout(timeout);
        setBusy(false);
      });
  }

  return (
    <div className="mx-auto flex min-h-screen w-full max-w-3xl flex-col px-6">
      <header className="pb-4 pt-6">
        <h1 className="text-xl font-semibold tracking-tight text-text">Research Chat</h1>
        <p className="mt-1 text-[13px] text-muted">
          Ask about anyone or anything in team memory. The agent searches sourced records and answers
          with citations you can trace.
        </p>
      </header>

      <div className="flex-1 pb-6">
        {exchanges.length === 0 ? (
          <div className="grid h-full place-items-center py-16">
            <EmptyState
              title="Ask your team's memory"
              description="Ask about a contact, company, or anything the team has sourced. Answers cite their sources."
            />
          </div>
        ) : (
          <div className="flex flex-col gap-6">
            {exchanges.map((e) => (
              <div key={e.id} className="flex flex-col gap-2">
                <MessageBubble role="user">{e.question}</MessageBubble>
                <div className="flex flex-col gap-2" aria-live="polite">
                  {(e.turn.steps.length > 0 || !e.done) && (
                    <ReasoningTrace
                      steps={e.turn.steps}
                      running={!e.done && !e.errored}
                      open={e.open}
                      onToggle={() => patch(e.id, (x) => ({ ...x, open: !x.open }))}
                      pendingTool={e.turn.pendingTool}
                    />
                  )}
                  {e.errored ? (
                    <div
                      role="alert"
                      className="rounded-[8px] bg-neg-bg px-3 py-2 text-[13px] text-neg-tx"
                    >
                      {e.turn.answer}
                    </div>
                  ) : e.turn.answer ? (
                    <MessageBubble role="assistant">
                      <span className="whitespace-pre-wrap">{e.turn.answer}</span>
                    </MessageBubble>
                  ) : null}
                  {e.turn.meta ? <MetaFooter meta={e.turn.meta} /> : null}
                </div>
              </div>
            ))}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      <div className="sticky bottom-0 -mx-6">
        <Composer value={input} onChange={setInput} onSubmit={submit} disabled={busy} />
      </div>
    </div>
  );
}

function MetaFooter({ meta }: { meta: ChatMeta }) {
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-muted">
      <span className="font-mono">Run #{meta.runId}</span>
      <span aria-hidden>·</span>
      <span>{meta.status}</span>
      <span aria-hidden>·</span>
      <span className="font-mono">
        {meta.steps} step{meta.steps === 1 ? "" : "s"}
      </span>
      <span aria-hidden>·</span>
      <a href={`/runs/${meta.runId}`} className="text-accent-deep underline">
        View trace
      </a>
      {meta.invalidCitations.length > 0 ? (
        <span className="text-warn-tx">
          · {meta.invalidCitations.length} unverified citation{meta.invalidCitations.length === 1 ? "" : "s"} removed
        </span>
      ) : null}
    </div>
  );
}
