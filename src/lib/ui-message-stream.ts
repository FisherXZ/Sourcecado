import { createUIMessageStream, createUIMessageStreamResponse } from "ai";

// Boundary module for the AI SDK UI-message-stream transport. All `from "ai"`
// usage for streaming lives here (model-boundary.test.ts allows this file and
// model-gateway.ts) so AI SDK surface stays contained and auditable. Routes call
// streamAgentResponse and never touch the SDK directly.

export interface AgentStreamWriter {
  // A reasoning step (reconciled by id). data is the rendered ChatStepPart.
  step: (id: string, data: unknown) => void;
  // The final answer, written as one composed assistant text part.
  answer: (text: string) => void;
  // Run metadata (runId, status, steps, invalidCitations).
  meta: (data: unknown) => void;
}

export function streamAgentResponse(run: (writer: AgentStreamWriter) => Promise<void>): Response {
  const stream = createUIMessageStream({
    execute: async ({ writer }) => {
      writer.write({ type: "start" });
      await run({
        step: (id, data) => writer.write({ type: "data-step", id, data }),
        answer: (text) => {
          writer.write({ type: "text-start", id: "answer" });
          writer.write({ type: "text-delta", id: "answer", delta: text });
          writer.write({ type: "text-end", id: "answer" });
        },
        meta: (data) => writer.write({ type: "data-meta", id: "meta", data }),
      });
    },
    onError: (error) => (error instanceof Error ? error.message : String(error)),
  });
  return createUIMessageStreamResponse({ stream });
}
