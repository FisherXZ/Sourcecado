"use client";

import { useState, type FormEvent } from "react";
import { Button, Card, Input } from "@/components/ui";

interface AgentResult {
  runId: number;
  status: "succeeded" | "failed";
  answer?: string;
  steps: number;
}

export function ChatClient() {
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AgentResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!question.trim() || loading) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await fetch("/api/agent", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ question }),
      });
      const data = (await res.json()) as AgentResult & { error?: string };
      if (data.error) {
        setError(data.error);
      } else {
        setResult(data);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-4 px-6 py-8">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-text">Research Chat</h1>
        <p className="mt-1 text-[13px] text-muted">
          Ask a question. The agent runs a traced multi-step loop through the gateway and tools.
        </p>
      </div>

      <form onSubmit={onSubmit} className="flex gap-2">
        <Input
          aria-label="Question"
          placeholder="Ask the agent (e.g. echo hello)"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          disabled={loading}
        />
        <Button type="submit" disabled={loading || !question.trim()}>
          {loading ? "Running…" : "Run"}
        </Button>
      </form>

      {error && (
        <Card>
          <div role="alert" className="text-[13px] text-text">Error: {error}</div>
        </Card>
      )}

      {result && (
        <Card>
          <div className="flex items-center justify-between gap-3">
            <span className="text-[13px] font-medium text-text">
              Run #{result.runId} — {result.status} ({result.steps} step{result.steps === 1 ? "" : "s"})
            </span>
            <a className="text-[13px] text-accent-deep underline" href={`/runs/${result.runId}`}>
              View trace
            </a>
          </div>
          {result.answer ? (
            <p className="mt-3 whitespace-pre-wrap text-[13px] text-text">{result.answer}</p>
          ) : (
            <p className="mt-3 text-[13px] text-muted">
              No answer — the run ended as {result.status}. Open the trace for details.
            </p>
          )}
        </Card>
      )}
    </div>
  );
}
